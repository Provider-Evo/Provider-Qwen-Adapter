

import asyncio
from typing import Any, AsyncGenerator, Dict, List, Optional, Union

import aiohttp

try:
    from src.core.dispatch.cand import Candidate
except ModuleNotFoundError:
    from .runtime import Candidate

try:
    from provider_sdk.extensions.platform.adapter import PlatformAdapter
except ModuleNotFoundError:
    from .runtime import PlatformAdapter

from .client import QwenClient


class QwenAdapter(PlatformAdapter):
    """Expose the Qwen client through the platform adapter interface."""

    @property
    def name(self) -> str:
        return "qwen"

    def __init__(self) -> None:
        self._client = QwenClient()
        self._session: Optional[aiohttp.ClientSession] = None
        self._owns_session = False
        self._init_lock = asyncio.Lock()
        self._initialized = False

    async def init(self, session: aiohttp.ClientSession) -> None:
        await self.ensure_initialized(session)

    async def close(self) -> None:
        await self.shutdown()

    async def ensure_initialized(
        self, session: Optional[aiohttp.ClientSession] = None
    ) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            if session is not None and not session.closed:
                self._session = session
                self._owns_session = False
            else:
                from src.core.server.net.connector import make_connector

                timeout = aiohttp.ClientTimeout(
                    total=None, connect=20, sock_connect=20, sock_read=None
                )
                self._session = aiohttp.ClientSession(
                    timeout=timeout, connector=make_connector()
                )
                self._owns_session = True
            await self._client.init_immediate(self._session)
            asyncio.create_task(self._client.background_setup())
            self._initialized = True

    async def shutdown(self) -> None:
        if not self._initialized:
            return
        await self._client.close()
        if (
            self._owns_session
            and self._session is not None
            and not self._session.closed
        ):
            await self._session.close()
        self._session = None
        self._initialized = False

    async def candidates(self) -> List[Candidate]:
        await self.ensure_initialized(self._session)
        return await self._client.candidates()

    async def ensure_candidates(self, count: int) -> int:
        await self.ensure_initialized(self._session)
        return await self._client.ensure_candidates(count)

    async def complete(
        self,
        candidate: Candidate,
        messages: List[Dict[str, Any]],
        model: str,
        stream: bool,
        *,
        thinking: bool = False,
        search: bool = False,
        **kw: Any,
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        await self.ensure_initialized(self._session)
        async for chunk in self._client.complete(
            candidate, messages, model, stream, thinking=thinking, search=search, **kw,
        ):
            yield chunk

    async def stop(self, candidate: Candidate) -> bool:
        await self.ensure_initialized(self._session)
        return await self._client.stop_candidate_generation(candidate)

    @property
    def supported_models(self) -> List[str]:
        return self._client.get_models()

    async def get_models(self) -> List[str]:
        await self.ensure_initialized(self._session)
        return self._client.get_models()

    async def set_proxy_enabled(self, enabled: bool) -> None:
        if not self.is_proxy_allowed():
            return
        await self.ensure_initialized(self._session)
        self._client.set_proxy_enabled(enabled)

    def is_proxy_allowed(self) -> bool:
        from ..config.proxy import load_use_proxy

        return load_use_proxy()

    async def is_proxy_enabled(self) -> bool:
        await self.ensure_initialized(self._session)
        return self._client.is_proxy_enabled()

    async def refresh_models(self) -> None:
        await self.ensure_initialized(self._session)
        await self._client.refresh_models()

    async def generate_video(
        self,
        prompt: str,
        image_url: str,
        token: str,
        user_id: str,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        await self.ensure_initialized(self._session)
        return await self._client.generate_video(
            prompt, image_url, token, user_id, **kwargs
        )

    async def synthesize_tts(
        self,
        text: str,
        token: str,
        **kwargs: Any,
    ) -> Optional[str]:
        await self.ensure_initialized(self._session)
        return await self._client.synthesize_tts(text, token, **kwargs)

    def get_config(self) -> Dict[str, Any]:
        return {
            "platform": "qwen",
            "models": self._client.get_models(),
            "model_info": self._client.get_model_info(),
        }
