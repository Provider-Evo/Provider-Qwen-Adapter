from __future__ import annotations

"""Authentication helpers: login, account configuration, token expiry checks."""

import asyncio
import base64
import json
import time
from typing import TYPE_CHECKING, Any, Dict, Optional

import aiohttp

if TYPE_CHECKING:
    from ..adapter.client import Account
from ..config.endpts import (
    BASE_URL,
    SETTINGS_PATH,
    SIGNIN_PATH,
    TOKEN_EXPIRY_MARGIN,
    TOKEN_LIFETIME,
)
from ..http.headers import build_headers, build_login_headers
from .passwd import hash_password
from ..config.config import DEFAULT_FULL_SETTINGS
from ..config.consts import SMART_PROXY_ENABLED
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
    """Mixin implementing Qwen account login and profile/settings sync."""

    def _is_proxy_available(self) -> bool:
        """Check if proxy should be tried (respects cooldown after failure)."""
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

        # Try with proxy first if available and not in cooldown
        if use_proxy and self._is_proxy_available():
            try:
                await self._login_attempt(account, payload, proxy_kwarg)
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
            await self._login_attempt(account, payload, None)
            if SMART_PROXY_ENABLED:
                self._proxy_selector.record(False, True)
            return True
        except Exception:
            if SMART_PROXY_ENABLED:
                self._proxy_selector.record(False, False)
            raise

    async def _login_attempt(self, account: Account, payload: Dict[str, Any], proxy: Optional[str]) -> None:
        async with self._session.post(
            "{}{}".format(BASE_URL, SIGNIN_PATH),
            json=payload,
            headers=build_login_headers(),
            ssl=False,
            timeout=aiohttp.ClientTimeout(total=30),
            proxy=proxy,
        ) as response:
            if response.status != 200:
                raise RuntimeError(
                    "Qwen 登录失败 HTTP {}: {}".format(response.status, (await response.text())[:300])
                )
            body = await response.text()
            ct = response.headers.get("Content-Type", "")
            if "text/html" in ct or (body.lstrip()[:15].lower().startswith(("<!doctype", "<html"))):
                raise RuntimeError("Qwen 登录被拦截(HTML 响应)")
            data = json.loads(body)
            envelope = data.get("data") if isinstance(data.get("data"), dict) else {}
            user = envelope.get("user") if isinstance(envelope.get("user"), dict) else {}
            token = (
                user.get("token")
                or envelope.get("token")
                or envelope.get("access_token")
                or data.get("access_token")
                or ""
            )
            if not token:
                raise RuntimeError("Qwen 登录响应缺少 token: {}".format(data))
            account.token = token
            account.is_login = True
            account.last_login = time.time()
            account.token_expires = _jwt_expires_at(token) or (time.time() + TOKEN_LIFETIME)
            self._cookies["token"] = token

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
        return await self._fetch_with_proxy_fallback("{}{}".format(BASE_URL, SETTINGS_PATH), account)

    async def _fetch_user_profile(self, account: Account) -> Dict[str, Any]:
        return await self._fetch_with_proxy_fallback("{}/api/v2/user".format(BASE_URL), account)

    async def _configure_account(self, account: Account) -> None:
        profile = await self._fetch_user_profile(account)
        profile_data = profile.get("data", profile) if isinstance(profile, dict) else {}
        account.user_id = str(profile_data.get("id") or profile_data.get("user_id") or account.user_id or "")

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
                "{}{}".format(BASE_URL, SETTINGS_PATH),
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
