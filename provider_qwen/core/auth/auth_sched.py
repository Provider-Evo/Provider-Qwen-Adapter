from __future__ import annotations

"""Background account relogin scheduling and expiry watch loops."""

import asyncio
import random
import time
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from ..adapter.client import Account
from ..config.endpts import (
    COOKIE_REFRESH_INTERVAL,
    LOGIN_BATCH_SIZE,
    LOGIN_POLL_INTERVAL,
    LOGIN_POOL_SIZE,
    LOGIN_SELECT_MAX,
    LOGIN_SELECT_MIN,
    TOKEN_CHECK_INTERVAL,
    TOKEN_REFRESH_INTERVAL,
)
from .crypto import generate_cookies, generate_fingerprint
try:
    from src.foundation.logger import get_logger
except ImportError:
    import logging
    def get_logger(name: str) -> logging.Logger:
        return logging.getLogger(name)

logger = get_logger(__name__)


class AuthScheduleMixin:
    """Mixin implementing periodic relogin/expiry-watch background tasks."""

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
                was_valid = bool(account.token) and not self._is_token_expired(account)
                try:
                    if account.token and self._is_token_expired(account):
                        self._log_queued_relogin(account.username[:6])
                        account.token = ""
                        account.is_login = False
                    await self._login_and_configure(account)
                except Exception as exc:
                    account.is_login = was_valid
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
                await self._run_login_poll_once()
            except Exception as exc:
                logger.warning("Qwen 登录轮询异常: %s", exc)
            timers["login_poll"] = time.time()
            self._save_task_timers(timers)

    async def _run_login_poll_once(self) -> None:
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

    async def _bg_token_expiry_watch(self) -> None:
        """Periodically invalidate expired accounts and relogin immediately."""
        while not self._closing:
            await asyncio.sleep(TOKEN_CHECK_INTERVAL)
            if self._closing:
                break
            try:
                await self._run_token_expiry_watch_once()
            except Exception as exc:
                logger.warning("Qwen token 过期巡检异常: %s", exc)

    async def _run_token_expiry_watch_once(self) -> None:
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
