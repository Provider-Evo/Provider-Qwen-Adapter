"""
stream 模块。

本文件为 Provider-Evo 项目标准模块，使用以下约定：

- 模块路径：provider-plugin.Provider-Qwen-Adapter.provider_qwen.core.http.stream
- 文件名：stream.py
- 父包：provider-plugin/Provider-Qwen-Adapter/provider_qwen/core/http

职责：

    作为 provider / 核心子系统的标准模块入口；
    通常被 ``plugin.py`` 或上层 ``client.py`` 通过显式 import 使用。

对外接口：

    本模块的 ``__all__`` 列出对外可导入的符号集合；其他内部符号
    可能在重构中调整，调用方应只依赖 ``__all__`` 暴露的稳定 API。

集成：

    - SDK 入口：``plugin.py`` 中 ``create_plugin()`` 引用本模块以构造 platform adapter。
    - 入口路由：``provider-self/src/routes/openai`` 通过 ``from src.core...`` 间接使用。
    - 测试：本目录下的 ``tests/`` 子目录覆盖本模块的核心逻辑。

依赖：

    - 仅依赖 ``provider-sdk`` 与 Python 3.8+ 标准库；不引入第三方 HTTP 库。
    - 不直接读环境变量；所有配置走 ``config/main_config.toml``。

修改指引：

    - 调整本模块时同步更新 ``docs-src/plugins/<name>.md`` 与对应 ``tests/``。
    - 保持单文件 200-400 行；超长请拆为子包并通过 ``__init__.py`` 重新导出。
    - 严禁放置 placeholder / 兜底 / 伪装通过的代码（见 ``AGENTS.md`` Hard Constraints）。
"""


import asyncio
import json
import uuid
from typing import Any, AsyncGenerator, Awaitable, Callable, Dict, List, Optional, Union

import aiohttp
from loguru import logger

from .sse import parse_sse_event


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
        logger.debug(
            "Qwen SSE 响应: status={} content_type={}",
            resp.status,
            resp.headers.get("Content-Type", ""),
        )
        total_bytes = 0
        buffer = await resp.content.readany()
        total_bytes += len(buffer)
        if buffer:
            logger.debug("Qwen SSE 首块内容: {}", buffer[:500])
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
        logger.debug("Qwen SSE 流结束: total_bytes={}", total_bytes)

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
        logger.debug("Qwen SSE 事件: type={} keys={}", event_type, list(event.keys()))
        if event_type == "error":
            raise RuntimeError(f"Qwen server error: {event.get('message', '')}")
        if event_type == "response_created":
            self.last_response_id = event.get("response_id")
        elif event_type == "answer":
            text = self._strip_tags(event.get("content", ""))
            if text:
                yield text
        elif event_type == "thinking":
            text = self._strip_tags(event.get("content", ""))
            if text:
                yield {"thinking": text}
        elif event_type == "thinking_summary":
            for piece in self._iter_thinking_pieces(event):
                yield {"thinking": piece}
        elif event_type == "image_gen_tool":
            calls = await asyncio.gather(
                *[self._build_single_image_call(url) for url in event.get("urls", [])]
            )
            if calls:
                yield {"tool_calls": calls}
        elif event_type == "image_gen":
            content = event.get("content", "")
            if content:
                yield {"tool_calls": [await self._build_single_image_call(content)]}
        elif event_type == "video_gen":
            content = event.get("content", "")
            if content:
                yield {"tool_calls": [self._wrap_tool_call("qwen.video_gen", {"url": content})]}
        elif event_type == "usage":
            yield {"usage": event.get("data", {})}
        elif event_type == "other":
            content = self._strip_tags(event.get("content", ""))
            if content:
                yield content
        if event.get("usage") and event_type != "usage":
            yield {"usage": event["usage"]}

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

from .payloads import (
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
