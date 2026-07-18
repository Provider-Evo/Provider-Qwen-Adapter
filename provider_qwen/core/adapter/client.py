from __future__ import annotations

"""Primary Qwen HTTP client implementation."""

import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

try:
    from src.core.dispatch.cand import Candidate, make_id
except ModuleNotFoundError:
    from .runtime import Candidate, make_id

try:
    from src.core.utils.compat.models_cache import ModelsCache
except ModuleNotFoundError:
    from .runtime import ModelsCache

try:
    from src.core.utils.compat.proxy_selector import ProxySelector
except ModuleNotFoundError:
    from .runtime import ProxySelector

from dataclasses import dataclass
from ..auth.auth import AuthMixin
from ..auth.auth_sched import AuthScheduleMixin
from ..store.chat_session import ChatSession
from ..config.consts import CAPS, MODELS
from ..auth.crypto import generate_cookies, generate_fingerprint
from ..config.endpts import (
    BASE_URL,
    MODELS_PATH,
    PROXY_SELECTOR_PERSIST_PATH,
    PERSIST_INTERVAL,
    TASK_TIMERS_PATH,
)
from ..http.headers import build_headers
from ..store.logs import LogsMixin
from ..media.media import MediaMixin
from ..config.models import extract_model_ids
from ..store.persist import load_persist, save_persist
from ..config.proxy import ProxyState
from ..media.upload import UploadMixin
from ..http.tts import TtsService
from ..media.video import VideoService
from .client_cmpl import ClientCompleteMixin


@dataclass
class Account:
    """Qwen 登录账号。"""

    username: str
    password: str
    token: str = ""
    user_id: str = ""
    password_hash: str = ""
    token_expires: float = 0.0
    last_login: float = 0.0
    memory_disabled: bool = False
    context_length: Optional[int] = None
    is_login: bool = False


def _load_accounts() -> List[Account]:
    """从本地 accounts.py（不入版本控制）读取账号列表，缺失时回退 config.toml。"""
    from pathlib import Path

    plugin_dir = Path(__file__).resolve().parents[3]

    raw: List[Dict[str, str]] = []
    accounts_path = plugin_dir / "accounts.py"
    if accounts_path.is_file():
        import importlib.util

        spec = importlib.util.spec_from_file_location("_qwen_accounts", accounts_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        raw = module.ACCOUNTS
    else:
        from src.foundation.config.reader import get_config_reader

        reader = get_config_reader()
        config, _schema, _raw = reader.get_plugin_config(plugin_dir)
        raw = config.get("accounts", [])

    if not isinstance(raw, list):
        return []
    accounts: List[Account] = []
    for item in raw:
        if isinstance(item, dict) and item.get("username") and item.get("password"):
            accounts.append(Account(username=str(item["username"]), password=str(item["password"])))
    return accounts


class QwenClient(AuthMixin, AuthScheduleMixin, ClientCompleteMixin, UploadMixin, MediaMixin, LogsMixin):
    """Current Qwen web client used by the adapter."""

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._account_states: Dict[str, Account] = {}
        self._candidates: List[Candidate] = []
        self._models: List[str] = list(MODELS)
        self._fp = generate_fingerprint()
        self._cookies: Dict[str, Any] = generate_cookies(self._fp)
        self._bg_tasks: List[asyncio.Task] = []
        self._closing = False
        self._active_chats: Dict[str, str] = {}
        self._active_responses: Dict[str, str] = {}
        self._models_cache = ModelsCache("qwen", MODELS, fetch_enabled=False)
        self._proxy_state = ProxyState()
        self._proxy_selector = ProxySelector(Path(PROXY_SELECTOR_PERSIST_PATH))
        self._relogin_log_buffer: List[str] = []
        self._relogin_flush_task: Optional[asyncio.Task] = None
        self._retry_log_buffer: List[str] = []
        self._retry_log_flush_task: Optional[asyncio.Task] = None
        self._login_fail_buffer: List[Tuple[str, str]] = []
        self._login_fail_flush_task: Optional[asyncio.Task] = None
        self._chat_session: Optional[ChatSession] = None
        self._tts_service: Optional[TtsService] = None
        self._video_service: Optional[VideoService] = None
        self._rate_limit_until: Dict[str, float] = {}
        self._proxy_cooldown_until: float = 0.0

    def get_models(self) -> List[str]:
        return list(self._models)

    def set_proxy_enabled(self, enabled: bool) -> None:
        self._proxy_state.set_enabled(enabled)

    def is_proxy_enabled(self) -> bool:
        return self._proxy_state.is_enabled()

    def _get_proxy_kwarg(self) -> Optional[str]:
        try:
            from src.foundation.config import get_config
        except ModuleNotFoundError:
            from .runtime import get_config
        try:
            from src.core.server import get_proxy_server
        except ModuleNotFoundError:
            from .runtime import get_proxy_server

        config = get_config()
        if not config.proxy.proxy_enabled:
            return None
        if self._proxy_state.override is True:
            return get_proxy_server() if config.platforms_proxy.is_platform_enabled("qwen") else None
        if self._proxy_state.override is False:
            return None
        from ..config.consts import SMART_PROXY_ENABLED

        if SMART_PROXY_ENABLED and self._proxy_selector.select():
            return get_proxy_server()
        return None

    async def init_immediate(self, session: aiohttp.ClientSession) -> None:
        self._session = session
        accounts = _load_accounts()
        for account in accounts:
            self._account_states[account.username] = Account(username=account.username, password=account.password)
        await self._models_cache.load()
        self._models = list(self._models_cache.models)
        self._cookies = load_persist(self._account_states, self._cookies, self._proxy_state)
        self._sync_expired_account_states()
        self._rebuild_candidates()
        self._chat_session = ChatSession(session, self._get_proxy_kwarg, lambda: self._cookies, lambda: self._fp)
        self._tts_service = TtsService(
            session,
            self._get_proxy_kwarg,
            lambda: self._cookies,
            lambda: self._fp,
            self._chat_session.create,
            self._chat_session.send_placeholder_message,
            self._chat_session.cleanup,
        )
        self._video_service = VideoService(
            session,
            self._get_proxy_kwarg,
            lambda: self._cookies,
            self._chat_session.create,
            self._chat_session.cleanup,
        )

    async def background_setup(self) -> None:
        await self._initial_login_pass()
        self._bg_tasks.append(asyncio.create_task(self._login_poll_loop()))
        self._bg_tasks.append(asyncio.create_task(self._bg_token_expiry_watch()))
        self._bg_tasks.append(asyncio.create_task(self._bg_cookie_refresh()))
        self._bg_tasks.append(asyncio.create_task(self._bg_persist()))
        self._bg_tasks.append(
            asyncio.create_task(
                self._models_cache.start_refresh_loop(
                    self.fetch_remote_models,
                    interval=24 * 60 * 60,
                    on_update=self._on_models_update,
                )
            )
        )

    def update_models(self, models: List[str]) -> None:
        merged: List[str] = []
        seen = set()
        for model in list(MODELS) + list(models):
            if model and model not in seen:
                seen.add(model)
                merged.append(model)
        self._models = merged
        self._rebuild_candidates()

    async def close(self) -> None:
        self._closing = True
        self._cancel_log_flush_tasks()
        for task in self._bg_tasks:
            task.cancel()
        for task in self._bg_tasks:
            try:
                await task
            except asyncio.CancelledError:
                continue
        self._bg_tasks.clear()
        self._save_persist()

    def _cancel_log_flush_tasks(self) -> None:
        if self._relogin_flush_task is not None and not self._relogin_flush_task.done():
            self._relogin_flush_task.cancel()
            self._relogin_flush_task = None
        self._flush_relogin_buffer_now()
        if self._retry_log_flush_task is not None and not self._retry_log_flush_task.done():
            self._retry_log_flush_task.cancel()
            self._retry_log_flush_task = None
        self._flush_retry_log_buffer_now()
        if self._login_fail_flush_task is not None and not self._login_fail_flush_task.done():
            self._login_fail_flush_task.cancel()
            self._login_fail_flush_task = None
        self._flush_login_fail_buffer_now()

    def _rebuild_candidates(self) -> None:
        self._candidates = [
            Candidate(
                id=make_id("qwen", account.username[:12]),
                platform="qwen",
                resource_id=account.username[:12],
                models=list(self._models),
                context_length=account.context_length,
                meta={
                    "email": account.username,
                    "token": account.token,
                    "user_id": account.user_id,
                    "is_login": True,
                },
                **CAPS,
            )
            for account in self._account_states.values()
            if account.is_login
            and account.token
            and not self._is_token_expired(account)
            and time.time() >= self._rate_limit_until.get(account.username, 0)
        ]

    async def candidates(self) -> List[Candidate]:
        self._sync_expired_account_states()
        self._rebuild_candidates()
        return list(self._candidates)

    def _set_rate_limit_cooldown(self, email: str, seconds: float = 60.0) -> None:
        self._rate_limit_until[email] = time.time() + seconds

    async def ensure_candidates(self, count: int) -> int:
        return len(self._candidates)

    def _save_persist(self) -> None:
        save_persist(self._account_states, self._cookies, self._proxy_state)

    def _load_task_timers(self) -> Dict[str, float]:
        path = Path(TASK_TIMERS_PATH)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return {str(key): float(value) for key, value in data.items()}
        except Exception:
            return {}

    def _save_task_timers(self, timers: Dict[str, float]) -> None:
        path = Path(TASK_TIMERS_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(timers, indent=2), encoding="utf-8")

    async def _bg_persist(self) -> None:
        while not self._closing:
            await asyncio.sleep(PERSIST_INTERVAL)
            self._save_persist()

    async def refresh_models(self) -> None:
        await self._models_cache._do_refresh(self.fetch_remote_models, on_update=self._on_models_update)

    async def _on_models_update(self, models: List[str]) -> None:
        self._models = list(models)
        for cand in self._candidates:
            cand.models = list(models)
        if self._account_states:
            self._rebuild_candidates()

    async def fetch_remote_models(self) -> List[str]:
        token = self._get_any_valid_token()
        if not token:
            return []
        endpoints = ["{}{}".format(BASE_URL, MODELS_PATH), "{}/api/v1/models".format(BASE_URL)]
        headers = build_headers(token, cookies=self._cookies)
        for endpoint in endpoints:
            try:
                async with self._session.get(
                    endpoint,
                    headers=headers,
                    ssl=False,
                    timeout=aiohttp.ClientTimeout(total=30),
                    proxy=self._get_proxy_kwarg(),
                ) as response:
                    if response.status != 200:
                        continue
                    models = extract_model_ids(await response.json(content_type=None))
                    if models:
                        return models
            except Exception:
                continue
        return []

    def _get_any_valid_token(self) -> Optional[str]:
        for account in self._account_states.values():
            if account.token and account.is_login and not self._is_token_expired(account):
                return account.token
        return None

    async def _create_chat(self, token: str, model: str, chat_type: str = "t2t") -> str:
        return await self._chat_session.create(token, model, chat_type)

    async def _cleanup_chat(self, chat_id: str, token: str) -> None:
        await self._chat_session.cleanup(chat_id, token)

    async def _send_placeholder_message(self, chat_id: str, token: str, model: str):
        return await self._chat_session.send_placeholder_message(chat_id, token, model)
