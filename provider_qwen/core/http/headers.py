

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from ..auth.crypto import get_baxia_tokens
from ..config.endpts import (
    BASE_URL,
    CHAT_ORIGIN,
    SEC_CH_UA,
    SEC_CH_UA_PLATFORM,
    USER_AGENT,
    WEB_VERSION,
)


def make_request_id() -> str:
    """Return a new request identifier."""
    return str(uuid.uuid4())


def make_timezone() -> str:
    """Return the browser-like timezone header value."""
    now = datetime.now().astimezone()
    offset = now.utcoffset()
    if offset is None:
        suffix = "+0000"
    else:
        total_seconds = int(offset.total_seconds())
        sign = "+" if total_seconds >= 0 else "-"
        total_seconds = abs(total_seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        suffix = f"{sign}{hours:02d}{minutes:02d}"
    return now.strftime(f"%a %b %d %Y %H:%M:%S GMT{suffix}")


def build_cookie_string(cookies: Optional[Dict[str, Any]]) -> str:
    """Convert a cookie mapping into a request header string."""
    if not cookies:
        return ""
    return "; ".join(f"{key}={value}" for key, value in cookies.items() if value not in {None, ""})


def merge_session_cookies(token: str, cookies: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Merge the session token into the cookie jar used by web requests."""
    merged = dict(cookies or {})
    if token:
        merged["token"] = token
    return merged


def _base_headers(*, include_sse: bool = False) -> Dict[str, str]:
    accept = "text/event-stream" if include_sse else "application/json, text/plain, */*"
    return {
        "Accept": accept,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Origin": CHAT_ORIGIN,
        "Referer": f"{CHAT_ORIGIN}/",
        "source": "web",
        "X-Request-Id": make_request_id(),
        "Timezone": make_timezone(),
        "Sec-Ch-Ua": SEC_CH_UA,
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": SEC_CH_UA_PLATFORM,
    }


def build_login_headers() -> Dict[str, str]:
    """Build headers for the v2 sign-in endpoint."""
    headers = _base_headers()
    headers["Version"] = WEB_VERSION
    headers["x-request-origin"] = BASE_URL
    return headers


def build_headers(
    token: str,
    *,
    chat_id: str = "",
    include_sse: bool = False,
    include_version: bool = True,
    fingerprint: str = "",
    cookies: Optional[Dict[str, Any]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    use_bearer: bool = False,
) -> Dict[str, str]:
    """Build authenticated headers for Qwen chat APIs.

Web/h5 uses cookie session auth (no Authorization). App/desktop may pass
``use_bearer=True`` to emit ``Authorization: Bearer``."""
    headers = _base_headers(include_sse=include_sse)
    session_cookies = merge_session_cookies(token, cookies)
    baxia = get_baxia_tokens()
    headers["bx-v"] = baxia["bxV"]
    headers["bx-ua"] = baxia["bxUa"]
    headers["bx-umidtoken"] = baxia["bxUmidToken"]
    if use_bearer and token:
        headers["Authorization"] = f"Bearer {token}"
    if include_version:
        headers["Version"] = WEB_VERSION
    if chat_id:
        headers["Referer"] = f"{CHAT_ORIGIN}/c/{chat_id}"
    if include_sse:
        headers["X-Accel-Buffering"] = "no"
    cookie_string = build_cookie_string(session_cookies)
    if cookie_string:
        headers["Cookie"] = cookie_string
    if extra_headers:
        headers.update(extra_headers)
    return headers


def build_stop_headers(token: str, *, cookies: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Build headers for the stop-generation endpoint."""
    return build_headers(token, include_version=True, cookies=cookies)

# =======================================================================
# 重导出 — 同包内协同模块的公共符号（保持外部 ``from .. import`` 路径稳定）
# =======================================================================

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
    "build_payload",
    "build_new_chat_payload",
    "build_stop_payload",
    "build_i2v_payload",
    "build_tts_payload",
    "build_replace_content_payload",
    "parse_sse_event",
    "parse_sse_line",
]
