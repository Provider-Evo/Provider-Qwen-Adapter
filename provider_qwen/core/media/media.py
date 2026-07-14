from __future__ import annotations

"""Media mixin providing video generation and TTS synthesis."""

import asyncio
import base64
import json
import time
from typing import Any, Dict, List, Optional

import aiohttp

from ..store.cdn import build_cdn_video_url
from ..config.endpoints import (
    BASE_URL,
    CHAT_PATH,
    GENERATED_VIDEO_DIR,
    SSE_TIMEOUT,
    TASK_STATUS_PATH,
    TTS_DIR,
    TTS_PATH,
    TTS_TIMEOUT,
    USER_AGENT,
    VIDEO_TASK_MAX_POLL_TIME,
    VIDEO_TASK_POLL_INTERVAL,
)
from ..http.headers import build_headers
from ..http.payloads import build_i2v_payload, build_replace_content_payload, build_tts_payload
from ..store.storage import save_video_file, save_wav_file
from src.foundation.logger import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 3


class MediaMixin:
    """Mixin providing video generation and TTS synthesis helpers."""

    async def _poll_task_status(
        self,
        task_id: str,
        token: str,
        chat_id: str,
    ) -> Dict[str, Any]:
        """Poll an async media task until completion."""
        url = f"{BASE_URL}{TASK_STATUS_PATH.format(task_id=task_id)}"
        headers = build_headers(
            token,
            chat_id=chat_id,
            include_sse=False,
            cookies=self._cookies,
        )
        start = time.time()
        while time.time() - start < VIDEO_TASK_MAX_POLL_TIME:
            try:
                async with self._session.get(
                    url,
                    headers=headers,
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=30),
                    proxy=self._get_proxy_kwarg(),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        task_status = data.get("task_status", "")
                        if task_status == "succeeded":
                            return data
                        if task_status == "failed":
                            raise RuntimeError(f"任务失败: {data.get('message', '未知')}")
            except Exception as exc:
                if "任务失败" in str(exc):
                    raise
                logger.warning("Qwen 媒体任务轮询异常: %s", exc)
            await asyncio.sleep(VIDEO_TASK_POLL_INTERVAL)
        raise RuntimeError(f"任务轮询超时 ({VIDEO_TASK_MAX_POLL_TIME}s)")

    async def generate_video(
        self,
        prompt: str,
        image_url: str,
        token: str,
        user_id: str,
        model: str = "qwen-max-latest",
        size: str = "16:9",
        image_name: str = "source.png",
        download: bool = True,
    ) -> Dict[str, Any]:
        """Run the full image-to-video flow."""
        try:
            chat_id = await self._create_chat(token, model, "i2v")
        except Exception as exc:
            return {"success": False, "error": f"创建 i2v 对话失败: {exc}"}

        payload = build_i2v_payload(
            prompt=prompt,
            chat_id=chat_id,
            model=model,
            image_url=image_url,
            image_name=image_name,
            size=size,
        )
        headers = build_headers(token, chat_id=chat_id, cookies=self._cookies)
        url = f"{BASE_URL}{CHAT_PATH}?chat_id={chat_id}"
        try:
            async with self._session.post(
                url,
                json=payload,
                headers=headers,
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=SSE_TIMEOUT),
                proxy=self._get_proxy_kwarg(),
            ) as resp:
                if resp.status != 200:
                    err = await resp.text()
                    asyncio.ensure_future(self._cleanup_chat(chat_id, token))
                    return {"success": False, "error": f"HTTP {resp.status}: {err[:300]}"}
                data = await resp.json()
                if not data.get("success"):
                    asyncio.ensure_future(self._cleanup_chat(chat_id, token))
                    return {"success": False, "error": str(data)}
                result_data = data.get("data", {})
                message_id = result_data.get("message_id", "")
                task_id = ""
                messages = result_data.get("messages", [])
                if messages:
                    task_id = ((messages[0].get("extra") or {}).get("wanx") or {}).get("task_id", "")
                if not task_id:
                    asyncio.ensure_future(self._cleanup_chat(chat_id, token))
                    return {"success": False, "error": "响应中未找到 task_id"}
        except Exception as exc:
            asyncio.ensure_future(self._cleanup_chat(chat_id, token))
            return {"success": False, "error": str(exc)}

        try:
            task_result = await self._poll_task_status(task_id, token, chat_id)
        except Exception as exc:
            asyncio.ensure_future(self._cleanup_chat(chat_id, token))
            return {"success": False, "task_id": task_id, "error": str(exc)}

        video_url = task_result.get("content") or build_cdn_video_url(
            user_id=user_id,
            video_type="i2v",
            message_id=message_id,
            task_id=task_id,
            token=token,
        )
        result: Dict[str, Any] = {
            "success": True,
            "task_id": task_id,
            "message_id": message_id,
            "chat_id": chat_id,
            "video_url": video_url,
            "size": size,
        }
        if download and video_url:
            try:
                async with self._session.get(
                    video_url,
                    headers={
                        "Accept": "*/*",
                        "Origin": BASE_URL,
                        "Referer": f"{BASE_URL}/",
                        "User-Agent": USER_AGENT,
                    },
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=SSE_TIMEOUT),
                    proxy=self._get_proxy_kwarg(),
                ) as resp:
                    if resp.status == 200:
                        local_path = save_video_file(await resp.read(), GENERATED_VIDEO_DIR)
                        if local_path:
                            result["local_path"] = local_path
            except Exception as exc:
                logger.warning("Qwen 视频下载失败: %s", exc)
        asyncio.ensure_future(self._cleanup_chat(chat_id, token))
        return result

    async def _replace_message_content(
        self,
        chat_id: str,
        response_id: str,
        new_content: str,
        origin_content: str,
        token: str,
    ) -> bool:
        """Replace an assistant message content before TTS."""
        url = f"{BASE_URL}/api/v2/chats/{chat_id}/messages/{response_id}"
        headers = build_headers(token, chat_id=chat_id, cookies=self._cookies)
        payload = build_replace_content_payload(new_content, origin_content)
        for attempt in range(MAX_RETRIES):
            if attempt > 0:
                await asyncio.sleep(1.0 * (2 ** (attempt - 1)))
            try:
                async with self._session.post(
                    url,
                    json=payload,
                    headers=headers,
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=30),
                    proxy=self._get_proxy_kwarg(),
                ) as resp:
                    if resp.status == 200:
                        return True
                    logger.warning("内容替换失败 HTTP %d: %s", resp.status, (await resp.text())[:200])
            except Exception as exc:
                logger.warning("内容替换异常: %s", exc)
        return False

    async def request_tts(
        self,
        chat_id: str,
        response_id: str,
        token: str,
        save_dir: str = TTS_DIR,
    ) -> Optional[str]:
        """Request TTS audio and persist the decoded WAV file."""
        url = f"{BASE_URL}{TTS_PATH}?chat_id={chat_id}"
        headers = build_headers(
            token,
            chat_id=chat_id,
            include_sse=True,
            fingerprint=self._fp,
            cookies=self._cookies,
        )
        headers["Accept"] = "*/*"
        chunks: List[str] = []
        async with self._session.post(
            url,
            json=build_tts_payload(chat_id, response_id),
            headers=headers,
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=TTS_TIMEOUT),
            proxy=self._get_proxy_kwarg(),
        ) as resp:
            if resp.status != 200:
                logger.warning("TTS 请求失败 HTTP %d", resp.status)
                return None
            buffer = b""
            async for raw in resp.content.iter_any():
                if not raw:
                    continue
                buffer += raw
                lines = buffer.split(b"\n")
                buffer = lines[-1]
                for line_bytes in lines[:-1]:
                    line = line_bytes.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].lstrip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        payload = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue
                    choices = payload.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    tts_fragment = delta.get("tts")
                    if tts_fragment:
                        chunks.append(tts_fragment)
                    if delta.get("status") == "finished":
                        break
        if not chunks:
            return None
        combined = "".join(chunks)
        padding = (-len(combined)) % 4
        if padding:
            combined += "=" * padding
        return save_wav_file(base64.b64decode(combined), save_dir)

    async def synthesize_tts(
        self,
        text: str,
        token: str,
        model: str = "qwen3-max",
        save_dir: str = TTS_DIR,
    ) -> Optional[str]:
        """Run the full placeholder-replace-synthesize TTS flow."""
        chat_id: Optional[str] = None
        try:
            chat_id = await self._create_chat(token, model, "t2t")
            response_id, origin_text = await self._send_placeholder_message(chat_id, token, model)
            if not response_id:
                return None
            ok = await self._replace_message_content(chat_id, response_id, text, origin_text.strip(), token)
            if not ok:
                return None
            return await self.request_tts(chat_id, response_id, token, save_dir)
        finally:
            if chat_id:
                asyncio.ensure_future(self._cleanup_chat(chat_id, token))
