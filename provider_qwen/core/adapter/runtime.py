

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional
import asyncio
import json


@dataclass
class Candidate:
    """Minimal candidate object compatible with the adapter runtime."""

    id: str
    platform: str
    resource_id: str
    models: List[str]
    context_length: Optional[int] = None
    meta: Dict[str, Any] = field(default_factory=dict)
    chat: bool = False
    vision: bool = False
    thinking: bool = False
    search: bool = False
    image_gen: bool = False
    image_edit: bool = False
    audio_gen: bool = False
    video_gen: bool = False
    continuation: bool = False
    artifacts: bool = False


def make_id(platform: str, resource_id: str) -> str:
    """Build a stable candidate identifier."""
    return f"{platform}:{resource_id}"


class PlatformAdapter:
    """Minimal base adapter interface used when the host project is absent."""

    @property
    def name(self) -> str:
        raise NotImplementedError


class ModelsCache:
    """Small local model cache fallback."""

    def __init__(self, namespace: str, models: List[str], fetch_enabled: bool = False) -> None:
        self.namespace = namespace
        self.models = list(models)
        self.fetch_enabled = fetch_enabled

    async def load(self) -> None:
        """No-op fallback load."""
        return None

    async def _do_refresh(
        self,
        fetcher: Callable[[], Awaitable[List[str]]],
        on_update: Optional[Callable[[List[str]], Awaitable[None]]] = None,
    ) -> None:
        """Refresh models through the provided fetcher."""
        models = await fetcher()
        if models:
            self.models = list(models)
            if on_update is not None:
                await on_update(self.models)

    async def start_refresh_loop(
        self,
        fetcher: Callable[[], Awaitable[List[str]]],
        interval: int,
        on_update: Optional[Callable[[List[str]], Awaitable[None]]] = None,
    ) -> None:
        """Run a simple periodic refresh loop."""
        while True:
            await self._do_refresh(fetcher, on_update=on_update)
            await asyncio.sleep(interval)


class ProxySelector:
    """Small persistence-backed proxy selector fallback."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.prefer_proxy = False
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.prefer_proxy = bool(data.get('prefer_proxy', False))
        except Exception:
            self.prefer_proxy = False

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({'prefer_proxy': self.prefer_proxy}, indent=2), encoding='utf-8')

    def select(self) -> bool:
        """Return the current proxy preference."""
        return self.prefer_proxy

    def record(self, used_proxy: bool, success: bool, latency_ms: Optional[float] = None) -> None:
        """Update a simple preference heuristic."""
        if success:
            if latency_ms is not None and latency_ms < 2000:
                self.prefer_proxy = used_proxy
        else:
            self.prefer_proxy = False if used_proxy else self.prefer_proxy
        self._save()


class _ProxyConfig:
    def __init__(self) -> None:
        self.proxy_enabled = False


class _PlatformsProxyConfig:
    def is_platform_enabled(self, platform: str) -> bool:
        return True


class _Config:
    def __init__(self) -> None:
        self.proxy = _ProxyConfig()
        self.platforms_proxy = _PlatformsProxyConfig()


def get_config() -> _Config:
    """Return a minimal configuration object."""
    return _Config()


def get_proxy_server() -> str:
    """Return an empty proxy URL in standalone mode."""
    return ''
