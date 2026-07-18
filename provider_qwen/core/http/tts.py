"""
tts 模块。

本文件为 Provider-Evo 项目标准模块，使用以下约定：

- 模块路径：provider-plugin.Provider-Qwen-Adapter.provider_qwen.core.http.tts
- 文件名：tts.py
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
import base64
import json
from typing import Awaitable, Callable, List, Optional, Tuple

import aiohttp

from ..config.endpts import BASE_URL, TTS_DIR, TTS_PATH, TTS_TIMEOUT
from .headers import build_headers
from .payload import build_replace_content_payload, build_tts_payload
from ..store.storage import save_wav_file


class TtsService:
    """Encapsulate the end-to-end TTS flow."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        proxy_resolver: Callable[[], Optional[str]],
        cookies_provider: Callable[[], dict],
        fingerprint_provider: Callable[[], str],
        create_chat: Callable[[str, str, str], Awaitable[str]],
        get_response_id: Callable[[str, str, str], Awaitable[Tuple[Optional[str], str]]],
        cleanup_chat: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self._session = session
        self._resolve_proxy = proxy_resolver
        self._cookies = cookies_provider
        self._fingerprint = fingerprint_provider
        self._create_chat = create_chat
        self._get_response_id = get_response_id
        self._cleanup_chat = cleanup_chat

    async def synthesize(
        self,
        text: str,
        token: str,
        model: str = "qwen3-max",
        save_dir: str = TTS_DIR,
    ) -> Optional[str]:
        """Run the full placeholder-replace-synthesize TTS flow."""
        try:
            chat_id = await self._create_chat(token, model, "t2t")
            response_id, origin_text = await self._get_response_id(chat_id, token, model)
            if not response_id:
                return None
            if not await self.replace_message_content(chat_id, response_id, text, origin_text.strip(), token):
                return None
            return await self.request_tts(chat_id, response_id, token, save_dir)
        finally:
            if "chat_id" in locals():
                asyncio.ensure_future(self._cleanup_chat(chat_id, token))

    async def replace_message_content(
        self,
        chat_id: str,
        response_id: str,
        new_content: str,
        origin_content: str,
        token: str,
    ) -> bool:
        """Replace an assistant message before TTS synthesis."""
        async with self._session.post(
            f"{BASE_URL}/api/v2/chats/{chat_id}/messages/{response_id}",
            json=build_replace_content_payload(new_content, origin_content),
            headers=build_headers(token, chat_id=chat_id, cookies=self._cookies()),
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=30),
            proxy=self._resolve_proxy(),
        ) as response:
            return response.status == 200

    @staticmethod
    def _extract_tts_fragment(line_bytes: bytes, chunks: List[str]) -> bool:
        """Parse one SSE line; append any TTS fragment to chunks. Return True if stream finished."""
        line = line_bytes.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            return False
        data_str = line[5:].lstrip()
        if not data_str or data_str == "[DONE]":
            return False
        payload = json.loads(data_str)
        choices = payload.get("choices") or []
        if not choices:
            return False
        delta = choices[0].get("delta", {})
        tts_fragment = delta.get("tts")
        if tts_fragment:
            chunks.append(tts_fragment)
        return delta.get("status") == "finished"

    async def _collect_tts_chunks(self, response: aiohttp.ClientResponse) -> List[str]:
        """Read the SSE response body and collect TTS audio fragments."""
        chunks: List[str] = []
        buffer = b""
        async for raw in response.content.iter_any():
            if not raw:
                continue
            buffer += raw
            lines = buffer.split(b"\n")
            buffer = lines[-1]
            finished = False
            for line_bytes in lines[:-1]:
                if self._extract_tts_fragment(line_bytes, chunks):
                    finished = True
                    break
            if finished:
                break
        return chunks

    async def request_tts(
        self,
        chat_id: str,
        response_id: str,
        token: str,
        save_dir: str = TTS_DIR,
    ) -> Optional[str]:
        """Request TTS audio and persist the decoded WAV file."""
        headers = build_headers(
            token,
            chat_id=chat_id,
            include_sse=True,
            fingerprint=self._fingerprint(),
            cookies=self._cookies(),
        )
        headers["Accept"] = "*/*"
        async with self._session.post(
            f"{BASE_URL}{TTS_PATH}?chat_id={chat_id}",
            json=build_tts_payload(chat_id, response_id),
            headers=headers,
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=TTS_TIMEOUT),
            proxy=self._resolve_proxy(),
        ) as response:
            if response.status != 200:
                return None
            chunks = await self._collect_tts_chunks(response)
        if not chunks:
            return None
        combined = "".join(chunks)
        padding = (-len(combined)) % 4
        if padding:
            combined += "=" * padding
        return save_wav_file(base64.b64decode(combined), save_dir)

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
