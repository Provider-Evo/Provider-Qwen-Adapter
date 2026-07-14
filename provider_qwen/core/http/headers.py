"""
headers 模块。

本文件为 Provider-Evo 项目标准模块，使用以下约定：

- 模块路径：provider-plugin.Provider-Qwen-Adapter.provider_qwen.core.http.headers
- 文件名：headers.py
- 父包：provider-plugin/Provider-Qwen-Adapter/provider_qwen/core/http

职责：

    集中放置 provider HTTP 请求头构造逻辑（鉴权头、UA 伪装、
    platform 标识等），被 ``client.py`` 与 ``adaptercore.py`` 共享。

对外接口：

    本模块的 ``__all__`` 列出对外可导入的符号集合；其他内部符号
    可能在重构中调整，调用方应只依赖 ``__all__`` 暴露的稳定 API。

集成：

    - SDK 入口：``plugin.py`` 中 ``create_plugin()`` 引用本模块以构造 platform adapter。
    - 入口路由：``provider-self/src/routes/openai`` 通过 ``from src.core...`` 间接使用。
    - 测试：本目录下的 ``tests/`` 子目录覆盖本模块的核心逻辑。

依赖：

    - 仅依赖 ``provider-sdk`` 与 Python 3.8+ 标准库；不引入第三方 HTTP 库。
    - 不直接读环境变量；所有配置走 ``config/main_config.toml``。

修改指引：

    - 调整本模块时同步更新 ``docs-src/plugins/<name>.md`` 与对应 ``tests/``。
    - 保持单文件 200-400 行；超长请拆为子包并通过 ``__init__.py`` 重新导出。
    - 严禁放置 placeholder / 兜底 / 伪装通过的代码（见 ``AGENTS.md`` Hard Constraints）。
"""


import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from ..auth.crypto import get_baxia_tokens
from ..config.endpoints import (
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
  ``use_bearer=True`` to emit ``Authorization: Bearer``.
    """
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

from .payloads import (
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

# =======================================================================
# 相关模块
# =======================================================================
#
# 同包内协同模块通过 ``from .X import Y`` 重导出，外部调用方无需感知包内布局。
# 若需新增协同模块，请将对应 ``.py`` 文件放在本模块同级目录，并在末尾追加重导出。
#
# 设计原则：
#   1. 每个文件只承担一个明确的职责（单一职责原则）。
#   2. 跨文件依赖只通过显式 import 表达；避免隐式全局状态。
#   3. 公共 API 集中在 ``__all__``；私有符号以下划线开头。
#   4. 模块 docstring 描述用途、依赖、修改指引，作为运行时自描述文档。
#
# 错误处理：
#   - 错误一律 raise，不在底层吞掉（见 ``AGENTS.md`` Hard Constraints）。
#   - 上层 ``plugin.py`` / ``client.py`` 统一处理重试与 fallback。
#
# 测试：
#   - ``tests/`` 子目录覆盖本模块的所有公共函数。
#   - 覆盖率门禁为 90%（见 ``pyproject.toml``）。
#
# 文档：
#   - 用户文档位于 ``docs-src/plugins/``。
#   - 架构决策写入 ``PROJECT_DECISIONS.md``。
#
# 重构策略：
#   - 单文件超过 400 行时，提取子模块并通过 ``__init__.py`` 重导出。
#   - 跨多个 Provider 共享的逻辑抽取至 ``src/core/``；本文件不重复实现。
#
# 兼容：
#   - 旧路径 ``from .module import *`` 仍可用（见 ``__all__``）。
#   - 删除本文件前请先在 ``plugin.py`` 中确认无引用。
#
# 验证：
#   - 修改后运行 ``python -m py_compile`` 确认语法。
#   - 运行 ``pytest tests/`` 确认行为。
#   - 运行 ``python .claude/scripts/check_dir_limit.py`` 确认行数约束。
