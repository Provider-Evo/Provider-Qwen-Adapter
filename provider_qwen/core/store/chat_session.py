from __future__ import annotations

"""Chat-session HTTP helpers."""

import asyncio
from typing import Any, AsyncGenerator, Callable, Dict, Optional, Tuple, Union

import aiohttp

from ..config.endpoints import BASE_URL, CHAT_PATH, DELETE_CHAT_PATH, GENERATED_IMAGE_DIR, NEW_CHAT_PATH, SSE_TIMEOUT, STOP_CHAT_PATH
from ..config.errors import TokenExpiredError, WafBlockedError
from ..http.headers import build_headers, build_stop_headers
from ..http.payloads import build_new_chat_payload, build_payload, build_stop_payload
from ..http.stream import StreamHandler
from .storage import save_image_file
from ..http.sse import parse_sse_event


class ChatSession:
    """Low-level chat lifecycle operations."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        proxy_resolver: Callable[[], Optional[str]],
        cookies_provider: Callable[[], Dict[str, Any]],
        fingerprint_provider: Callable[[], str],
    ) -> None:
        self._session = session
        self._resolve_proxy = proxy_resolver
        self._cookies = cookies_provider
        self._fingerprint = fingerprint_provider

    async def create(self, token: str, model: str, chat_type: str = "t2t") -> str:
        """Create a new chat and return its identifier."""
        url = f"{BASE_URL}{NEW_CHAT_PATH}"
        headers = build_headers(token, include_version=False)
        async with self._session.post(
            url,
            json=build_new_chat_payload(model, chat_type),
            headers=headers,
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=15),
            proxy=self._resolve_proxy(),
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"Qwen create-chat failed: HTTP {response.status}: {(await response.text())[:300]}")
            data = await response.json()
            chat_id = (data.get("data") or {}).get("id", "")
            if not data.get("success") or not chat_id:
                error_data = data.get("data") or {}
                error_code = error_data.get("code", "")
                error_details = error_data.get("details", "")
                if error_code == "unauthorized" or "Token has expired" in error_details:
                    raise TokenExpiredError(f"Qwen token expired: {error_details}")
                raise RuntimeError(f"Qwen create-chat returned an invalid payload: {data}")
            return chat_id

    async def stop(self, chat_id: str, token: str, response_id: str = "") -> bool:
        """Stop generation for an active chat."""
        if not chat_id or not token:
            return False
        async with self._session.post(
            f"{BASE_URL}{STOP_CHAT_PATH}",
            json=build_stop_payload(chat_id, response_id),
            headers=build_stop_headers(token, cookies=self._cookies()),
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=15),
            proxy=self._resolve_proxy(),
        ) as response:
            return response.status in {200, 204}

    async def resume_stream(
        self,
        chat_id: str,
        response_id: str,
        token: str,
        handler: StreamHandler,
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        """Reconnect an interrupted SSE stream via GET ?response_id=."""
        if not chat_id or not response_id:
            return
        headers = build_headers(
            token,
            chat_id=chat_id,
            include_sse=True,
            fingerprint=self._fingerprint(),
            cookies=self._cookies(),
        )
        params = {"chat_id": chat_id, "response_id": response_id}
        async with self._session.get(
            f"{BASE_URL}{CHAT_PATH}",
            params=params,
            headers=headers,
            ssl=False,
            timeout=aiohttp.ClientTimeout(connect=10, total=SSE_TIMEOUT),
            proxy=self._resolve_proxy(),
        ) as response:
            if response.status != 200:
                body = await response.text()
                raise RuntimeError(f"Qwen SSE resume failed: HTTP {response.status}: {body[:300]}")
            content_type = response.headers.get("Content-Type", "")
            if "text/html" in content_type:
                raise WafBlockedError("upstream returned HTML instead of SSE on resume")
            async for item in handler.stream(response):
                yield item

    async def delete(self, chat_id: str, token: str) -> bool:
        """Delete a chat."""
        if not chat_id or not token:
            return False
        async with self._session.delete(
            f"{BASE_URL}{DELETE_CHAT_PATH.format(chat_id=chat_id)}",
            headers=build_headers(token, cookies=self._cookies()),
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=15),
            proxy=self._resolve_proxy(),
        ) as response:
            return response.status in {200, 204}

    async def cleanup(self, chat_id: str, token: str) -> None:
        """Delete a chat, suppressing transport failures."""
        try:
            await self.delete(chat_id, token)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return

    async def download_image(self, image_url: str, save_dir: str = GENERATED_IMAGE_DIR) -> Optional[str]:
        """Download an image asset to local storage."""
        async with self._session.get(
            image_url,
            headers={
                "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Connection": "keep-alive",
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/",
            },
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=60),
            proxy=self._resolve_proxy(),
        ) as response:
            if response.status != 200:
                return None
            return save_image_file(await response.read(), response.headers.get("Content-Type", "image/png"), save_dir)

    async def send_placeholder_message(self, chat_id: str, token: str, model: str) -> Tuple[Optional[str], str]:
        """Send a placeholder prompt and return ``(response_id, origin_text)``."""
        payload = build_payload(
            messages=[{"role": "user", "content": "注意：啥都不要说，直接输出\\即可"}],
            model=model,
            chat_id=chat_id,
            thinking_enabled=False,
            auto_thinking=False,
            thinking_mode="Fast",
            thinking_format="raw",
            stream=True,
        )
        headers = build_headers(
            token,
            chat_id=chat_id,
            include_sse=True,
            fingerprint=self._fingerprint(),
            cookies=self._cookies(),
        )
        async with self._session.post(
            f"{BASE_URL}{CHAT_PATH}?chat_id={chat_id}",
            json=payload,
            headers=headers,
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=60),
            proxy=self._resolve_proxy(),
        ) as response:
            if response.status != 200:
                return None, ""
            return await self._consume_placeholder(response)

    @staticmethod
    async def _consume_placeholder(resp: aiohttp.ClientResponse) -> Tuple[Optional[str], str]:
        response_id: Optional[str] = None
        origin_text = ""
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
                event = parse_sse_event(line[5:].lstrip())
                if event is None:
                    continue
                if event.get("type") == "response_created":
                    response_id = event.get("response_id")
                elif event.get("type") == "answer":
                    origin_text += event.get("content", "")
        return response_id, origin_text
