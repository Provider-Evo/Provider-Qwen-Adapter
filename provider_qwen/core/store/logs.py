


import asyncio
from typing import Optional

from src.foundation.logger import get_logger

logger = get_logger(__name__)


class LogsMixin:
    """Collect short-lived log buffers and expose flush helpers."""

    async def _flush_relogin_buffer(self) -> None:
        await asyncio.sleep(1)
        self._flush_relogin_buffer_now()
        self._relogin_flush_task = None

    async def _flush_retry_log_buffer(self) -> None:
        await asyncio.sleep(1)
        self._flush_retry_log_buffer_now()
        self._retry_log_flush_task = None

    async def _flush_login_fail_buffer(self) -> None:
        await asyncio.sleep(1)
        self._flush_login_fail_buffer_now()
        self._login_fail_flush_task = None

    def _log_queued_relogin(self, username_prefix: str) -> None:
        self._relogin_log_buffer.append(username_prefix)
        task: Optional[asyncio.Task] = getattr(self, "_relogin_flush_task", None)
        if task is None or task.done():
            self._relogin_flush_task = asyncio.create_task(self._flush_relogin_buffer())

    def _log_retry(self, message: str) -> None:
        self._retry_log_buffer.append(message)
        task: Optional[asyncio.Task] = getattr(self, "_retry_log_flush_task", None)
        if task is None or task.done():
            self._retry_log_flush_task = asyncio.create_task(self._flush_retry_log_buffer())

    def _log_login_failure(self, username_prefix: str, error_message: str) -> None:
        self._login_fail_buffer.append((username_prefix, error_message))
        task: Optional[asyncio.Task] = getattr(self, "_login_fail_flush_task", None)
        if task is None or task.done():
            self._login_fail_flush_task = asyncio.create_task(self._flush_login_fail_buffer())

    def _flush_relogin_buffer_now(self) -> None:
        if not self._relogin_log_buffer:
            return
        self._relogin_log_buffer.clear()

    def _flush_retry_log_buffer_now(self) -> None:
        if not self._retry_log_buffer:
            return
        for message in self._retry_log_buffer:
            logger.debug("Qwen 请求重试: %s", message)
        self._retry_log_buffer.clear()

    def _flush_login_fail_buffer_now(self) -> None:
        if not self._login_fail_buffer:
            return
        for username_prefix, error_message in self._login_fail_buffer:
            logger.debug("Qwen 登录失败 %s: %s", username_prefix, error_message)
        self._login_fail_buffer.clear()
