from __future__ import annotations

"""Authentication and account background-management helpers."""

import asyncio
import base64
import json
import random
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

import aiohttp

if TYPE_CHECKING:
    from ..adapter.client import Account
from ..config.endpoints import (
    AUTH_BASE_URL,
    BASE_URL,
    COOKIE_REFRESH_INTERVAL,
    LOGIN_BATCH_SIZE,
    LOGIN_POLL_INTERVAL,
    LOGIN_POOL_SIZE,
    LOGIN_SELECT_MAX,
    LOGIN_SELECT_MIN,
    SETTINGS_PATH,
    SIGNIN_PATH,
    TOKEN_CHECK_INTERVAL,
    TOKEN_EXPIRY_MARGIN,
    TOKEN_LIFETIME,
    TOKEN_REFRESH_INTERVAL,
)
from ..http.headers import build_headers, build_login_headers
from .password import hash_password
from ..config.settings import DEFAULT_FULL_SETTINGS
from ..config.constants import SMART_PROXY_ENABLED
from .crypto import generate_cookies, generate_fingerprint
try:
    from src.foundation.logger import get_logger
except ImportError:
    import logging
    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)

logger = get_logger(__name__)


_PROXY_COOLDOWN_SECONDS: float = 300.0  # skip proxy for 5 minutes after failure


def _jwt_expires_at(token: str) -> float:
    """Return JWT ``exp`` claim as unix timestamp, or 0 when unavailable."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return 0.0
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        if exp is None:
            return 0.0
        return float(exp)
    except Exception:
        return 0.0


class AuthMixin:
    """Mixin implementing Qwen account authentication workflows."""

    def _is_proxy_available(self) -> bool:
        """Check if proxy should be tried (respects cooldown after failure)."""
        if not hasattr(self, "_proxy_cooldown_until"):
            self._proxy_cooldown_until = 0.0
        return time.time() >= self._proxy_cooldown_until

    def _mark_proxy_failed(self) -> None:
        """Set cooldown after proxy failure."""
        self._proxy_cooldown_until = time.time() + _PROXY_COOLDOWN_SECONDS

    @staticmethod
    def _is_proxy_error(exc: Exception) -> bool:
        """Check if an exception indicates a proxy infrastructure failure."""
        if isinstance(exc, (aiohttp.ClientProxyConnectionError, aiohttp.ClientConnectionError)):
            return True
        if isinstance(exc, aiohttp.ServerDisconnectedError):
            return True
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return True
        if isinstance(exc, RuntimeError):
            msg = str(exc)
            if any(code in msg for code in ("502", "503", "504")):
                return True
        return False

    async def _login(self, account: Account) -> bool:
        account.password_hash = account.password_hash or hash_password(account.password)
        payload = {"email": account.username, "password": account.password_hash}

        proxy_kwarg = self._get_proxy_kwarg()
        use_proxy = proxy_kwarg is not None

        async def _attempt(proxy: Optional[str]) -> None:
            async with self._session.post(
                f"{AUTH_BASE_URL}{SIGNIN_PATH}",
                json=payload,
                headers=build_login_headers(),
                ssl=False,
                timeout=aiohttp.ClientTimeout(total=30),
                proxy=proxy,
            ) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"Qwen 登录失败 HTTP {response.status}: {(await response.text())[:300]}"
                    )
                data = await response.json()
                envelope = data.get("data") if isinstance(data.get("data"), dict) else {}
                user = envelope.get("user") if isinstance(envelope.get("user"), dict) else {}
                token = (
                    user.get("token")
                    or envelope.get("access_token")
                    or data.get("access_token")
                    or ""
                )
                if not token:
                    raise RuntimeError(f"Qwen 登录响应缺少 token: {data}")
                account.token = token
                account.is_login = True
                account.last_login = time.time()
                account.token_expires = _jwt_expires_at(token) or (time.time() + TOKEN_LIFETIME)
                if hasattr(self, "_cookies"):
                    self._cookies["token"] = token

        # Try with proxy first if available and not in cooldown
        if use_proxy and self._is_proxy_available():
            try:
                await _attempt(proxy_kwarg)
                if SMART_PROXY_ENABLED:
                    self._proxy_selector.record(True, True)
                return True
            except Exception as exc:
                if self._is_proxy_error(exc):
                    self._mark_proxy_failed()
                    if SMART_PROXY_ENABLED:
                        self._proxy_selector.record(True, False)
                else:
                    raise

        # Direct attempt (fallback or primary when no proxy configured)
        try:
            await _attempt(None)
            if SMART_PROXY_ENABLED:
                self._proxy_selector.record(False, True)
            return True
        except Exception:
            if SMART_PROXY_ENABLED:
                self._proxy_selector.record(False, False)
            raise

    async def _fetch_with_proxy_fallback(self, url: str, account: Account) -> Dict[str, Any]:
        """GET request with proxy fallback — try proxy first, fall back to direct on proxy error."""
        proxy_kwarg = self._get_proxy_kwarg()
        headers = build_headers(account.token, cookies=self._cookies)
        timeout = aiohttp.ClientTimeout(total=30)

        if proxy_kwarg and self._is_proxy_available():
            try:
                async with self._session.get(
                    url, headers=headers, ssl=False, timeout=timeout, proxy=proxy_kwarg,
                ) as response:
                    if response.status != 200:
                        return {}
                    return await response.json(content_type=None)
            except Exception as exc:
                if not self._is_proxy_error(exc):
                    raise
                self._mark_proxy_failed()

        async with self._session.get(
            url, headers=headers, ssl=False, timeout=timeout, proxy=None,
        ) as response:
            if response.status != 200:
                return {}
            return await response.json(content_type=None)

    async def _fetch_user_settings(self, account: Account) -> Dict[str, Any]:
        return await self._fetch_with_proxy_fallback(f"{BASE_URL}{SETTINGS_PATH}", account)

    async def _fetch_user_profile(self, account: Account) -> Dict[str, Any]:
        return await self._fetch_with_proxy_fallback(f"{AUTH_BASE_URL}/api/v2/user", account)

    async def _configure_account(self, account: Account) -> None:
        profile = await self._fetch_user_profile(account)
        profile_data = profile.get("data", profile) if isinstance(profile, dict) else {}
        account.user_id = str(profile_data.get("id") or profile_data.get("user_id") or account.user_id or "")

# === PART-TAIL v2 ===
# 此 part_01.py 由原文件拆出，含本模块的主入口符号。
# 其余 part_NN.py 持有辅助函数、扩展点、私有实现。
# 详情见 PROJECT_DECISIONS.md#module-split。

        settings = await self._fetch_user_settings(account)
        settings_data = settings.get("data", settings) if isinstance(settings, dict) else {}
        memory = settings_data.get("memory") if isinstance(settings_data, dict) else {}
        if isinstance(memory, dict):
            account.memory_disabled = not bool(memory.get("enabled", True))
        context_length = settings_data.get("context_length") if isinstance(settings_data, dict) else None
        if isinstance(context_length, int) and context_length > 0:
            account.context_length = context_length

    async def _save_default_settings(self, account: Account) -> None:
        proxy_kwarg = self._get_proxy_kwarg()
        headers = build_headers(account.token, cookies=self._cookies)
        timeout = aiohttp.ClientTimeout(total=30)

        async def _put(proxy: Optional[str]) -> None:
            async with self._session.put(
                f"{BASE_URL}{SETTINGS_PATH}",
                json=DEFAULT_FULL_SETTINGS,
                headers=headers,
                ssl=False,
                timeout=timeout,
                proxy=proxy,
            ) as response:
                await response.read()

        if proxy_kwarg and self._is_proxy_available():
            try:
                await _put(proxy_kwarg)
                return
            except Exception as exc:
                if not self._is_proxy_error(exc):
                    logger.warning("Qwen 默认设置下发失败 %s: %s", account.username[:6], exc)
                    return
                self._mark_proxy_failed()
        try:
            await _put(None)
        except Exception as exc:
            logger.warning("Qwen 默认设置下发失败 %s: %s", account.username[:6], exc)

    async def _login_and_configure(self, account: Account) -> None:
        await self._login(account)
        try:
            await self._configure_account(account)
        except Exception as exc:
            logger.warning(
                "Qwen 账号配置失败 %s (登录已成功): %s",
                account.username[:6],
                exc,
            )
        await self._save_default_settings(account)

    def _token_expires_at(self, account: Account) -> float:
        """Return the effective expiry timestamp for an account token."""
        if account.token:
            jwt_exp = _jwt_expires_at(account.token)
            if jwt_exp:
                return jwt_exp
        if account.token_expires:
            return account.token_expires
        if account.last_login:
            return account.last_login + TOKEN_LIFETIME
        return 0.0

    def _is_token_expired(self, account: Account) -> bool:
        if not account.token:
            return True
        expires_at = self._token_expires_at(account)
        if not expires_at:
            return True
        return expires_at <= time.time() + TOKEN_EXPIRY_MARGIN

    def _sync_expired_account_states(self) -> bool:
        """Mark accounts past TOKEN_LIFETIME / token_expires as logged out."""
        changed = False
        for account in self._account_states.values():
            if not account.is_login and not account.token:
                continue
            if not account.token or self._is_token_expired(account):
                if account.is_login or account.token:
                    account.is_login = False
                    account.token = ""
                    changed = True
        if changed:
            self._rebuild_candidates()
        return changed

    async def _relogin_accounts(self, accounts: List[Account]) -> None:
        """Refresh one or more accounts and rebuild routing candidates."""
        if not accounts:
            return
        semaphore = asyncio.Semaphore(LOGIN_POOL_SIZE)

        async def worker(account: Account) -> None:
            async with semaphore:
                try:
                    if account.token and self._is_token_expired(account):
                        self._log_queued_relogin(account.username[:6])
                        account.token = ""
                    account.is_login = False
                    await self._login_and_configure(account)
                except Exception as exc:
                    account.is_login = False
                    self._log_login_failure(account.username[:6], str(exc))

        await asyncio.gather(*(worker(acc) for acc in accounts), return_exceptions=True)
        self._rebuild_candidates()
        self._save_persist()

    def _accounts_due_for_refresh(self) -> List[Account]:
        """Logged-in accounts whose token expires within TOKEN_REFRESH_INTERVAL."""
        horizon = time.time() + TOKEN_REFRESH_INTERVAL
        due: List[Account] = []
        for account in self._account_states.values():
            if not account.token or not account.is_login:
                continue
            if self._is_token_expired(account):
                continue
            expires_at = self._token_expires_at(account)
            if expires_at and expires_at <= horizon:
                due.append(account)
        return due

    def _select_login_batch(self) -> List[Account]:
        pool = [acc for acc in self._account_states.values() if self._is_token_expired(acc) or not acc.is_login]
        if not pool:
            return []
        random.shuffle(pool)
        upper = min(LOGIN_SELECT_MAX, max(LOGIN_SELECT_MIN, LOGIN_BATCH_SIZE), len(pool))
        lower = min(LOGIN_SELECT_MIN, upper)
        size = upper if upper == lower else random.randint(lower, upper)
        return pool[:size]

    async def _initial_login_pass(self) -> None:
        self._sync_expired_account_states()
        batch = self._select_login_batch()
        if not batch:
            return
        await self._relogin_accounts(batch)

    async def _login_poll_loop(self) -> None:
        timers = self._load_task_timers()
        last_run = timers.get("login_poll", 0)
        remaining = LOGIN_POLL_INTERVAL - (time.time() - last_run)
        while not self._closing:
            sleep_time = remaining if remaining > 0 else LOGIN_POLL_INTERVAL
            remaining = -1
            await asyncio.sleep(sleep_time)
            if self._closing:
                break
            try:
                self._sync_expired_account_states()
                batch = self._select_login_batch()
                proactive = self._accounts_due_for_refresh()
                targets: List[Account] = []
                seen = set()
                for account in batch + proactive:
                    if account.username in seen:
                        continue
                    seen.add(account.username)
                    targets.append(account)
                if targets:
                    await self._relogin_accounts(targets)
                elif self._sync_expired_account_states():
                    self._save_persist()
            except Exception as exc:
                logger.warning("Qwen 登录轮询异常: %s", exc)
            timers["login_poll"] = time.time()
            self._save_task_timers(timers)

    async def _bg_token_expiry_watch(self) -> None:
        """Periodically invalidate expired accounts and relogin immediately."""
        while not self._closing:
            await asyncio.sleep(TOKEN_CHECK_INTERVAL)
            if self._closing:
                break
            try:
                expired = [
                    account
                    for account in self._account_states.values()
                    if account.token and self._is_token_expired(account)
                ]
                if expired:
                    for account in expired:
                        account.is_login = False
                        account.token = ""
                    self._rebuild_candidates()
                    self._save_persist()
                    await self._relogin_accounts(expired)
                elif self._sync_expired_account_states():
                    self._save_persist()
            except Exception as exc:
                logger.warning("Qwen token 过期巡检异常: %s", exc)

    async def _bg_cookie_refresh(self) -> None:
        while not self._closing:
            await asyncio.sleep(COOKIE_REFRESH_INTERVAL)
            if self._closing:
                break
            try:
                self._fp = generate_fingerprint()
                self._cookies = generate_cookies(self._fp)
                self._save_persist()
            except Exception as exc:
                logger.warning("Qwen Cookie 刷新失败: %s", exc)
