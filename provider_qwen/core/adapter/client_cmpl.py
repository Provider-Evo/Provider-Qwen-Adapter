from __future__ import annotations

"""Chat completion request/stream handling mixin for QwenClient."""

import asyncio
import json
import ssl
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple, Union

import aiohttp

try:
    from src.core.dispatch.cand import Candidate
    from src.core.utils.errors import ModerationError
except ModuleNotFoundError:
    from .runtime import Candidate

    class ModerationError(RuntimeError):
        """Fallback when provider-core is not on the import path."""

from ..config.consts import SMART_PROXY_ENABLED
from ..config.endpts import BASE_URL, CHAT_PATH, SSE_TIMEOUT, TTS_DIR
from ..config.errors import WafBlockedError, TokenExpiredError, RateLimitedError
from ..http.headers import build_headers
from ..http.payload import build_payload
from ..http.stream import StreamHandler

# 上游返回自签/无效证书链，仍需保持不校验证书；同时关闭 TLS session ticket
# 复用，避免连接池复用已失效的 ticket 导致 DECRYPTION_FAILED_OR_BAD_RECORD_MAC。
_INSECURE_NO_TICKET_SSL_CONTEXT = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
_INSECURE_NO_TICKET_SSL_CONTEXT.check_hostname = False
_INSECURE_NO_TICKET_SSL_CONTEXT.verify_mode = ssl.CERT_NONE
_INSECURE_NO_TICKET_SSL_CONTEXT.options |= ssl.OP_NO_TICKET


class ClientCompleteMixin:
    """封装聊天补全的重试与底层请求逻辑。"""

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
        model = self.resolve_upstream_model(model)
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
                if await self._handle_token_expired(candidate, exc):
                    continue
                raise exc
            except WafBlockedError as exc:
                last_error = exc
                self._log_retry("WAF retry {}/3".format(attempt + 1))
            except RateLimitedError as exc:
                last_error = exc
                self._log_retry("rate limited, skipping retries for this account")
                break
            except ModerationError:
                raise
            except Exception as exc:
                last_error = exc
                self._log_retry("retry {}/3: {}".format(attempt + 1, exc))
        if last_error is not None:
            raise last_error

    async def _handle_token_expired(self, candidate: Candidate, exc: TokenExpiredError) -> bool:
        """尝试重新登录以恢复 token 过期的账号；成功返回 True 以便重试请求。"""
        email = str(candidate.meta.get("email", ""))
        account = self._account_states.get(email)
        if account is None:
            return False
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
            return True
        except Exception as relogin_exc:
            self._log_login_failure(account.username[:6], str(relogin_exc))
            return False

    def _resolve_account_for_request(self, candidate: Candidate) -> Tuple[str, Any]:
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
        if not account.token:
            raise RuntimeError("candidate token is missing")
        return email, account

    async def _collect_upload_files(
        self,
        messages: List[Dict[str, Any]],
        upload_files: Optional[List[Tuple[bytes, str]]],
        token: str,
        user_id: str,
    ) -> List[Dict[str, Any]]:
        file_objects: List[Dict[str, Any]] = []
        if upload_files:
            for file_data, filename in upload_files:
                file_objects.append(await self.upload_file(file_data, filename, token, user_id))
        for data_uri in self._extract_base64_images(messages):
            file_objects.append(await self.upload_file_from_base64(data_uri, token, user_id))
        return file_objects

    def _build_chat_request(
        self,
        messages: List[Dict[str, Any]],
        model: str,
        chat_id: str,
        file_objects: List[Dict[str, Any]],
        token: str,
        thinking: bool,
        search: bool,
    ) -> Tuple[Dict[str, Any], Dict[str, str]]:
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
        return payload, headers

    def _record_proxy_result(self, proxy_used: bool, success: bool, request_start: float) -> None:
        if not SMART_PROXY_ENABLED:
            return
        if success:
            self._proxy_selector.record(proxy_used, True, (time.time() - request_start) * 1000)
        else:
            self._proxy_selector.record(proxy_used, False)

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
        email, account = self._resolve_account_for_request(candidate)
        token = account.token
        user_id = str(account.user_id or "")
        file_objects = await self._collect_upload_files(messages, upload_files, token, user_id)
        chat_id = await self._chat_session.create(token, model, "t2t")
        self._active_chats[candidate.id] = chat_id
        proxy_used = self._get_proxy_kwarg() is not None
        request_start = time.time()
        success = False
        try:
            payload, headers = self._build_chat_request(
                messages, model, chat_id, file_objects, token, thinking, search,
            )
            async with self._session.post(
                "{}{}?chat_id={}".format(BASE_URL, CHAT_PATH, chat_id),
                json=payload,
                headers=headers,
                ssl=_INSECURE_NO_TICKET_SSL_CONTEXT,
                timeout=aiohttp.ClientTimeout(connect=10, total=SSE_TIMEOUT),
                proxy=self._get_proxy_kwarg(),
            ) as response:
                async for item in self._handle_chat_response(response, candidate, email, chat_id, token, tts):
                    yield item
                success = True
        finally:
            self._active_chats.pop(candidate.id, None)
            self._active_responses.pop(candidate.id, None)
            asyncio.ensure_future(self._chat_session.cleanup(chat_id, token))
            self._record_proxy_result(proxy_used, success, request_start)

    async def _handle_chat_response(
        self,
        response: aiohttp.ClientResponse,
        candidate: Candidate,
        email: str,
        chat_id: str,
        token: str,
        tts: bool,
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        if response.status != 200:
            body = await response.text()
            if response.status == 401 or "Token has expired" in body or "unauthorized" in body.lower():
                raise TokenExpiredError("token expired: {}".format(body[:200]))
            raise RuntimeError("chat HTTP {}: {}".format(response.status, body[:300]))
        content_type = response.headers.get("Content-Type", "")
        if "text/html" in content_type:
            raise WafBlockedError("upstream returned HTML instead of SSE")
        if "application/json" in content_type:
            await self._raise_for_json_error(response, email)
        handler = StreamHandler(self.download_image)
        try:
            async for item in handler.stream(response):
                if handler.last_response_id:
                    self._active_responses[candidate.id] = handler.last_response_id
                yield item
        except (aiohttp.ClientError, asyncio.IncompleteReadError, ConnectionResetError, ssl.SSLError):
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

    async def _raise_for_json_error(self, response: aiohttp.ClientResponse, email: str) -> None:
        body = await response.text()
        if "Token has expired" in body or "unauthorized" in body.lower():
            raise TokenExpiredError("token expired: {}".format(body[:200]))
        try:
            err_data = json.loads(body)
            if isinstance(err_data.get("data"), dict) and err_data["data"].get("code") == "RateLimited":
                self._set_rate_limit_cooldown(email)
                raise RateLimitedError("rate limited: {}".format(body[:200]))
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
        raise RuntimeError("Qwen chat returned non-stream JSON error: {}".format(body[:300]))

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
