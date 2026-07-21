from __future__ import annotations

"""Media mixin providing video generation and TTS synthesis."""

import asyncio
import base64
import json
import time
from typing import Any, Dict, List, Optional

import aiohttp

from ..store.cdn import build_cdn_video_url
from ..config.endpts import (
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
from ..http.payload import build_i2v_payload, build_replace_content_payload, build_tts_payload
from ..store.storage import save_video_file, save_wav_file
from src.foundation.logger import get_logger

logger = get_logger(__name__)

MAX_RETRIES = 3


class MediaMixin:
    """Mixin providing video generation and TTS synthesis helpers."""

    async def _poll_once(
        self,
        url: str,
        headers: Dict[str, str],
    ) -> Optional[Dict[str, Any]]:
        """执行一次轮询请求；任务未完成时返回 ``None``。"""
        async with self._session.get(
            url,
            headers=headers,
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=30),
            proxy=self._get_proxy_kwarg(),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            task_status = data.get("task_status", "")
            if task_status == "succeeded":
                return data
            if task_status == "failed":
                raise RuntimeError(f"任务失败: {data.get('message', '未知')}")
            return None

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
                result = await self._poll_once(url, headers)
                if result is not None:
                    return result
            except Exception as exc:
                if "任务失败" in str(exc):
                    raise
                logger.warning("Qwen 媒体任务轮询异常: %s", exc)
            await asyncio.sleep(VIDEO_TASK_POLL_INTERVAL)
        raise RuntimeError(f"任务轮询超时 ({VIDEO_TASK_MAX_POLL_TIME}s)")

    async def _submit_i2v_task(
        self,
        chat_id: str,
        token: str,
        prompt: str,
        image_url: str,
        model: str,
        image_name: str,
        size: str,
    ) -> Dict[str, Any]:
        """Submit the i2v chat request and extract message_id/task_id, or return an error dict."""
        payload = build_i2v_payload(
            prompt=prompt,
            chat_id=chat_id,
            model=model,
            image_url=image_url,
            image_name=image_name,
            size=size,
        )
        headers = build_headers(token, chat_id=chat_id, cookies=self._cookies)
        url = "{0}{1}?chat_id={2}".format(BASE_URL, CHAT_PATH, chat_id)
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
                    return {"success": False, "error": "HTTP {0}: {1}".format(resp.status, err[:300])}
                data = await resp.json()
                if not data.get("success"):
                    return {"success": False, "error": str(data)}
                result_data = data.get("data", {})
                message_id = result_data.get("message_id", "")
                task_id = ""
                messages = result_data.get("messages", [])
                if messages:
                    task_id = ((messages[0].get("extra") or {}).get("wanx") or {}).get("task_id", "")
                if not task_id:
                    return {"success": False, "error": "响应中未找到 task_id"}
                return {"success": True, "message_id": message_id, "task_id": task_id}
        except Exception as exc:
            return {"success": False, "error": str(exc)}

    async def _download_generated_video(self, video_url: str) -> Optional[str]:
        """Download a generated video and persist it locally, returning the local path if saved."""
        try:
            async with self._session.get(
                video_url,
                headers={
                    "Accept": "*/*",
                    "Origin": BASE_URL,
                    "Referer": "{0}/".format(BASE_URL),
                    "User-Agent": USER_AGENT,
                },
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=SSE_TIMEOUT),
                proxy=self._get_proxy_kwarg(),
            ) as resp:
                if resp.status == 200:
                    return save_video_file(await resp.read(), GENERATED_VIDEO_DIR)
        except Exception as exc:
            logger.warning("Qwen 视频下载失败: %s", exc)
        return None

    def _build_video_result(
        self,
        task_id: str,
        message_id: str,
        chat_id: str,
        video_url: str,
        size: str,
    ) -> Dict[str, Any]:
        """Build the base success result dict for a completed video task."""
        return {
            "success": True,
            "task_id": task_id,
            "message_id": message_id,
            "chat_id": chat_id,
            "video_url": video_url,
            "size": size,
        }

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
        model = self.resolve_upstream_model(model)
        try:
            chat_id = await self._create_chat(token, model, "i2v")
        except Exception as exc:
            return {"success": False, "error": "创建 i2v 对话失败: {0}".format(exc)}

        submit_result = await self._submit_i2v_task(
            chat_id, token, prompt, image_url, model, image_name, size
        )
        if not submit_result["success"]:
            asyncio.ensure_future(self._cleanup_chat(chat_id, token))
            return {"success": False, "error": submit_result["error"]}
        message_id = submit_result["message_id"]
        task_id = submit_result["task_id"]

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
        result = self._build_video_result(task_id, message_id, chat_id, video_url, size)
        if download and video_url:
            local_path = await self._download_generated_video(video_url)
            if local_path:
                result["local_path"] = local_path
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

    @staticmethod
    def _parse_tts_sse_line(line_bytes: bytes, chunks: List[str]) -> bool:
        """Parse a single TTS SSE line, appending audio fragments to chunks.

        Returns True if the stream should stop (finished marker seen).
        """
        line = line_bytes.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            return False
        data_str = line[5:].lstrip()
        if not data_str or data_str == "[DONE]":
            return False
        try:
            payload = json.loads(data_str)
        except json.JSONDecodeError:
            return False
        choices = payload.get("choices") or []
        if not choices:
            return False
        delta = choices[0].get("delta", {})
        tts_fragment = delta.get("tts")
        if tts_fragment:
            chunks.append(tts_fragment)
        return delta.get("status") == "finished"

    async def _collect_tts_chunks(self, resp: aiohttp.ClientResponse) -> List[str]:
        """Read the streamed TTS response and collect base64 audio fragments."""
        chunks: List[str] = []
        buffer = b""
        async for raw in resp.content.iter_any():
            if not raw:
                continue
            buffer += raw
            lines = buffer.split(b"\n")
            buffer = lines[-1]
            for line_bytes in lines[:-1]:
                if self._parse_tts_sse_line(line_bytes, chunks):
                    return chunks
        return chunks

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
            chunks = await self._collect_tts_chunks(resp)
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
