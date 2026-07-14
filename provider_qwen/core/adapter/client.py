from __future__ import annotations

"""Primary Qwen HTTP client implementation."""

import asyncio
import json
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple, Union

import aiohttp

try:
    from src.core.dispatch.candidate import Candidate, make_id
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
from ..store.chat_session import ChatSession
from ..config.constants import CAPS, MODELS, COOKIE_REFRESH_INTERVAL, SMART_PROXY_ENABLED
from ..auth.crypto import generate_cookies, generate_fingerprint
from ..config.endpoints import (
    BASE_URL,
    CHAT_PATH,
    MODELS_PATH,
    PROXY_SELECTOR_PERSIST_PATH,
    PERSIST_INTERVAL,
    SSE_TIMEOUT,
    TASK_TIMERS_PATH,
    TTS_DIR,
)
from ..config.errors import WafBlockedError, TokenExpiredError
from ..http.headers import build_headers
from ..store.logs import LogsMixin
from ..media.media import MediaMixin
from ..config.models import extract_model_ids
from ..http.payloads import build_payload
from ..store.persistence import load_persist, save_persist
from ..config.proxy import ProxyState
from ..media.upload import UploadMixin
from ..http.stream import StreamHandler
from ..http.tts import TtsService
from ..media.video import VideoService


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
    """从插件自身 config.toml 读取账号配置并返回账号列表。"""
    from pathlib import Path
    from src.foundation.config.reader import get_config_reader

    plugin_dir = Path(__file__).resolve().parents[3]
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


class QwenClient(AuthMixin, UploadMixin, MediaMixin, LogsMixin):
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
        for attr_name, flush_name in [
            ("_relogin_flush_task", "_flush_relogin_buffer_now"),
            ("_retry_log_flush_task", "_flush_retry_log_buffer_now"),
            ("_login_fail_flush_task", "_flush_login_fail_buffer_now"),
        ]:
            task = getattr(self, attr_name, None)
            if task is not None and not task.done():
                task.cancel()
                setattr(self, attr_name, None)
            flush = getattr(self, flush_name, None)
            if callable(flush):
                flush()
        for task in self._bg_tasks:
            task.cancel()
        for task in self._bg_tasks:
            try:
                await task
            except asyncio.CancelledError:
                continue
        self._bg_tasks.clear()
        self._save_persist()

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
        ]

    async def candidates(self) -> List[Candidate]:
        self._sync_expired_account_states()
        self._rebuild_candidates()
        return list(self._candidates)

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
        endpoints = [f"{BASE_URL}{MODELS_PATH}", f"{BASE_URL}/api/v1/models"]
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

    async def complete(
        self,
        candidate: Candidate,
        messages: List[Dict[str, Any]],
        model: str,
        stream: bool,
        *,
        thinking: bool = False,
        search: bool = False,
        tts: bool = False,
        upload_files: Optional[List[Tuple[bytes, str]]] = None,
        **kw: Any,
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        last_error: Optional[Exception] = None
        for attempt in range(3):
            if attempt:
                await asyncio.sleep(2 ** (attempt - 1))
            try:
                async for chunk in self._do_request(
                    candidate,
                    messages,
                    model,
                    stream=stream,
                    thinking=thinking,
                    search=search,
                    tts=tts,
                    upload_files=upload_files,
                ):
                    yield chunk
                return
            except TokenExpiredError as exc:
                email = str(candidate.meta.get("email", ""))
                account = self._account_states.get(email)
                if account is not None:
                    account.is_login = False
                    account.token = ""
                    account.token_expires = 0.0
                    self._rebuild_candidates()
                    self._save_persist()
                    try:
                        await self._login_and_configure(account)
                        self._rebuild_candidates()
                        candidate.meta["token"] = account.token
                        candidate.meta["user_id"] = account.user_id
                        self._save_persist()
                        continue
                    except Exception as relogin_exc:
                        self._log_login_failure(account.username[:6], str(relogin_exc))
                raise exc
            except WafBlockedError as exc:
                last_error = exc
                self._log_retry(f"WAF retry {attempt + 1}/3")
            except Exception as exc:
                last_error = exc
                self._log_retry(f"retry {attempt + 1}/3: {exc}")
        if last_error is not None:
            raise last_error

    async def _do_request(
        self,
        candidate: Candidate,
        messages: List[Dict[str, Any]],
        model: str,
        *,
        stream: bool = True,
        thinking: bool = False,
        search: bool = False,
        tts: bool = False,
        upload_files: Optional[List[Tuple[bytes, str]]] = None,
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        email = str(candidate.meta.get("email", ""))
        account = self._account_states.get(email)
        if (
            account is None
            or not account.is_login
            or not account.token
            or self._is_token_expired(account)
        ):
            raise RuntimeError("candidate account is not logged in")
        candidate.meta["token"] = account.token
        candidate.meta["user_id"] = account.user_id
        candidate.meta["is_login"] = True
        token = account.token
        user_id = str(account.user_id or "")
        if not token:
            raise RuntimeError("candidate token is missing")
        file_objects: List[Dict[str, Any]] = []
        if upload_files:
            for file_data, filename in upload_files:
                file_objects.append(await self.upload_file(file_data, filename, token, user_id))
        for data_uri in self._extract_base64_images(messages):
            file_objects.append(await self.upload_file_from_base64(data_uri, token, user_id))
        chat_id = await self._chat_session.create(token, model, "t2t")
        self._active_chats[candidate.id] = chat_id
        proxy_used = self._get_proxy_kwarg() is not None
        request_start = time.time()
        success = False
        try:
            # Qwen chat completions always respond as SSE; StreamHandler only
            # parses event-stream bodies. Client stream=false must not disable
            # upstream streaming or the race/single paths see empty output.
            payload = build_payload(
                messages=messages,
                model=model,
                chat_id=chat_id,
                files=file_objects,
                thinking_enabled=thinking,
                thinking_mode="Thinking" if thinking else "Fast",
                auto_search=search,
                stream=True,
            )
            headers = build_headers(token, chat_id=chat_id, include_sse=True, fingerprint=self._fp, cookies=self._cookies)
            async with self._session.post(
                f"{BASE_URL}{CHAT_PATH}?chat_id={chat_id}",
                json=payload,
                headers=headers,
                ssl=False,
                timeout=aiohttp.ClientTimeout(connect=10, total=SSE_TIMEOUT),
                proxy=self._get_proxy_kwarg(),
            ) as response:
                if response.status != 200:
                    body = await response.text()
                    if response.status == 401 or "Token has expired" in body or "unauthorized" in body.lower():
                        raise TokenExpiredError(f"token expired: {body[:200]}")
                    raise RuntimeError(f"chat HTTP {response.status}: {body[:300]}")
                content_type = response.headers.get("Content-Type", "")
                if "text/html" in content_type:
                    raise WafBlockedError("upstream returned HTML instead of SSE")
                if "application/json" in content_type:
                    body = await response.text()
                    if "Token has expired" in body or "unauthorized" in body.lower():
                        raise TokenExpiredError(f"token expired: {body[:200]}")
                    raise RuntimeError(f"Qwen chat returned non-stream JSON error: {body[:300]}")
                handler = StreamHandler(self.download_image)
                try:
                    async for item in handler.stream(response):
                        if handler.last_response_id:
                            self._active_responses[candidate.id] = handler.last_response_id
                        yield item
                except (aiohttp.ClientError, asyncio.IncompleteReadError, ConnectionResetError):
                    response_id = handler.last_response_id
                    if not response_id:
                        raise
                    async for item in self._chat_session.resume_stream(
                        chat_id, response_id, token, handler,
                    ):
                        if handler.last_response_id:
                            self._active_responses[candidate.id] = handler.last_response_id
                        yield item
                if tts and handler.last_response_id:
                    await self.request_tts(chat_id, handler.last_response_id, token, TTS_DIR)
                success = True
        finally:
            self._active_chats.pop(candidate.id, None)
            self._active_responses.pop(candidate.id, None)
            asyncio.ensure_future(self._chat_session.cleanup(chat_id, token))
            if SMART_PROXY_ENABLED:
                if success:
                    self._proxy_selector.record(proxy_used, True, (time.time() - request_start) * 1000)
                else:
                    self._proxy_selector.record(proxy_used, False)

    async def stop_generation(self, chat_id: str, token: str, response_id: str = "") -> bool:
        return await self._chat_session.stop(chat_id, token, response_id)

    async def delete_chat(self, chat_id: str, token: str) -> bool:
        return await self._chat_session.delete(chat_id, token)

    async def stop_candidate_generation(self, candidate: Candidate) -> bool:
        chat_id = self._active_chats.get(candidate.id)
        if not chat_id:
            return False
        response_id = self._active_responses.get(candidate.id, "")
        return await self.stop_generation(
            chat_id,
            str(candidate.meta.get("token", "")),
            response_id,
        )
