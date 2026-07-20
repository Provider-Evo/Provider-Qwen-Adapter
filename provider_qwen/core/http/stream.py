

import ast
import asyncio
import json
import uuid
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional, Union

import aiohttp
from loguru import logger

try:
    from src.core.utils.errors import ModerationError
except ModuleNotFoundError:
    ModerationError = RuntimeError  # type: ignore[misc,assignment]

from .sse import parse_sse_event


def _parse_sse_error_payload(message: Any) -> Any:
    """Normalize Qwen SSE error payloads that may arrive as dict or repr string."""
    if isinstance(message, dict):
        return message
    if not isinstance(message, str):
        return message
    text = message.strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            parsed = ast.literal_eval(text)
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, SyntaxError):
            pass
    return message


def _raise_for_sse_error(message: Any) -> None:
    """Raise typed errors for known upstream SSE failures."""
    payload = _parse_sse_error_payload(message)
    if isinstance(payload, dict) and payload.get("code") == "data_inspection_failed":
        details = payload.get("details") or "输入内容未通过安全审核"
        raise ModerationError(str(details), status_code=400)
    raise RuntimeError("Qwen server error: {}".format(message))


class StreamHandler:
    """Consume one SSE response and emit normalized stream items."""

    def __init__(self, download_image: Callable[[str], Awaitable[Optional[str]]]) -> None:
        self._download_image = download_image
        self.last_response_id: Optional[str] = None
        self._thinking_count = 0
        self._tail = b""

    async def stream(
        self,
        resp: aiohttp.ClientResponse,
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        """Yield normalized items from a Qwen SSE response."""
        self.last_response_id = None
        self._thinking_count = 0
        self._tail = b""
        total_bytes = 0
        buffer = await resp.content.readany()
        total_bytes += len(buffer)
        if buffer:
            async for item in self._process_buffer(buffer):
                yield item
            buffer = self._tail
        async for raw in resp.content.iter_any():
            if not raw:
                continue
            total_bytes += len(raw)
            buffer += raw
            async for item in self._process_buffer(buffer):
                yield item
            buffer = self._tail

    async def _process_buffer(
        self,
        buffer: bytes,
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        lines = buffer.split(b"\n")
        self._tail = lines[-1]
        for line_bytes in lines[:-1]:
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            event = parse_sse_event(line[5:].lstrip())
            if event is None:
                continue
            async for item in self._dispatch(event):
                yield item

    async def _dispatch(
        self,
        event: Dict[str, Any],
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        event_type = event.get("type", "")
        if event_type == "error":
            _raise_for_sse_error(event.get("message", ""))
        async for item in self._yield_event_items(event_type, event):
            yield item
        if event.get("usage") and event_type != "usage":
            yield {"usage": event["usage"]}

    async def _yield_event_items(
        self,
        event_type: str,
        event: Dict[str, Any],
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        if event_type == "response_created":
            self.last_response_id = event.get("response_id")
            return
        if event_type == "answer":
            async for item in self._yield_text_content(event):
                yield item
            return
        if event_type == "thinking":
            async for item in self._yield_thinking_content(event):
                yield item
            return
        if event_type == "thinking_summary":
            for piece in self._iter_thinking_pieces(event):
                yield {"thinking": piece}
            return
        if event_type == "image_gen_tool":
            async for item in self._yield_image_gen_tool(event):
                yield item
            return
        if event_type == "image_gen":
            async for item in self._yield_single_image_gen(event):
                yield item
            return
        if event_type == "video_gen":
            async for item in self._yield_video_gen(event):
                yield item
            return
        if event_type == "usage":
            yield {"usage": event.get("data", {})}
            return
        if event_type == "other":
            async for item in self._yield_text_content(event):
                yield item

    async def _yield_text_content(
        self,
        event: Dict[str, Any],
    ) -> AsyncGenerator[str, None]:
        text = self._strip_tags(event.get("content", ""))
        if text:
            yield text

    async def _yield_thinking_content(
        self,
        event: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        text = self._strip_tags(event.get("content", ""))
        if text:
            yield {"thinking": text}

    async def _yield_image_gen_tool(
        self,
        event: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        calls = await asyncio.gather(
            *[self._build_single_image_call(url) for url in event.get("urls", [])]
        )
        if calls:
            yield {"tool_calls": calls}

    async def _yield_single_image_gen(
        self,
        event: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        content = event.get("content", "")
        if not content:
            return
        yield {"tool_calls": [await self._build_single_image_call(content)]}

    async def _yield_video_gen(
        self,
        event: Dict[str, Any],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        content = event.get("content", "")
        if not content:
            return
        yield {"tool_calls": [self._wrap_tool_call("qwen.video_gen", {"url": content})]}

    def _iter_thinking_pieces(self, event: Dict[str, Any]) -> List[str]:
        if event.get("status") != "typing":
            return []
        extra = event.get("extra", {})
        titles = extra.get("summary_title", {}).get("content", [])
        thoughts = extra.get("summary_thought", {}).get("content", [])
        total = max(len(titles), len(thoughts))
        pieces: List[str] = []
        for index in range(self._thinking_count, total):
            title = titles[index] if index < len(titles) else ""
            thought = thoughts[index] if index < len(thoughts) else ""
            pieces.append(f"{title}: {thought}" if title else thought)
        self._thinking_count = total
        return pieces

    async def _build_single_image_call(self, url: str) -> Dict[str, Any]:
        local_path = await self._download_image(url)
        arguments: Dict[str, Any] = {"url": url}
        if local_path:
            arguments["local_path"] = local_path
        return self._wrap_tool_call("qwen.image_gen", arguments)

    @staticmethod
    def _strip_tags(content: str) -> str:
        return content.replace("<think>", "").replace("</think>", "")

    @staticmethod
    def _wrap_tool_call(name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": f"call_{uuid.uuid4().hex[:12]}",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        }

# =======================================================================
# 重导出 — 同包内协同模块的公共符号（保持外部 ``from .. import`` 路径稳定）
# =======================================================================

from .headers import (
    make_request_id,
    make_timezone,
    build_cookie_string,
    merge_session_cookies,
    build_login_headers,
    build_headers,
    build_stop_headers,
)

from .payload import (
    build_payload,
    build_new_chat_payload,
    build_stop_payload,
    build_i2v_payload,
    build_tts_payload,
    build_replace_content_payload,
)

from .sse import (
    parse_sse_event,
    parse_sse_line,
)

__all__ = [
    "make_request_id",
    "make_timezone",
    "build_cookie_string",
    "merge_session_cookies",
    "build_login_headers",
    "build_headers",
    "build_stop_headers",
    "build_payload",
    "build_new_chat_payload",
    "build_stop_payload",
    "build_i2v_payload",
    "build_tts_payload",
    "build_replace_content_payload",
    "parse_sse_event",
    "parse_sse_line",
]
