"""
shared 模块。

本文件为 Provider-Evo 项目标准模块，使用以下约定：

- 模块路径：provider-plugin.Provider-Qwen-Adapter.provider_qwen.core.http.shared
- 文件名：shared.py
- 父包：provider-plugin/Provider-Qwen-Adapter/provider_qwen/core/http

职责：

    作为 provider / 核心子系统的标准模块入口；
    通常被 ``plugin.py`` 或上层 ``client.py`` 通过显式 import 使用。

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


from typing import Any, Dict

from ..store.cdn import build_cdn_video_url
from ..config.consts import (
    BASE_URL,
    CAPS,
    MODELS,
    MODELS_PERSIST_PATH,
    SMART_PROXY_ENABLED,
    USER_AGENT,
    USER_AGENT_MOBILE,
    SEC_CH_UA,
    FRONTEND_VERSION,
    BAXIA_SDK_VERSION,
    BXUA_VERSION,
    CUSTOM_BASE64_CHARS,
    PERSIST_PATH,
    TASK_TIMERS_PATH,
    PROXY_SELECTOR_PERSIST_PATH,
    GENERATED_IMAGE_DIR,
    GENERATED_VIDEO_DIR,
    TTS_DIR,
    UPLOAD_TEMP_DIR,
    LOGIN_BATCH,
    LOGIN_BATCH_SIZE,
    LOGIN_CONCURRENCY,
    LOGIN_POOL_SIZE,
    LOGIN_SELECT_MIN,
    LOGIN_SELECT_MAX,
    INITIAL_LOGIN_MAX,
    LOGIN_POLL_INTERVAL,
    TOKEN_EXPIRY_MARGIN,
    TOKEN_REFRESH_INTERVAL,
    COOKIE_REFRESH_INTERVAL,
    PERSIST_INTERVAL,
    SSE_TIMEOUT,
    TTS_TIMEOUT,
    VIDEO_CDN_BASE,
    VIDEO_TASK_MAX_POLL_TIME,
    VIDEO_TASK_POLL_INTERVAL,
)
from ..auth.crypto import (
    collect_fingerprint_data,
    custom_encode,
    generate_bxua,
    generate_device_id,
    generate_fingerprint,
    get_baxia_tokens,
    get_bxumidtoken,
    hash_password,
    lzw_compress,
)
from ..media.mimes import DATA_URI_EXT_MAP, EXTENSION_TO_MIME
from ..auth.cookies import HASH_FIELDS
from ..config.endpts import *
from ..media.files import build_file_object, build_url_file_object
from .headers import build_cookie_string, build_headers, build_login_headers, build_stop_headers
from ..media.oss import build_oss_authorization
from ..store.storage import save_image_file, save_video_file, save_wav_file
from ..config.models import extract_model_ids
from .payload import (
    DEFAULT_FEATURE_CONFIG,
    build_i2v_payload,
    build_new_chat_payload,
    build_payload,
    build_replace_content_payload,
    build_stop_payload,
    build_tts_payload,
)
from ..config.config import DEFAULT_FULL_SETTINGS
from .sse import parse_sse_event, parse_sse_line
from ..media.mimes import get_file_category, get_mime_type
from ..store.storage import build_wav_from_pcm

__all__ = [
    "Any",
    "Dict",
    "BASE_URL",
    "CAPS",
    "MODELS",
    "MODELS_PERSIST_PATH",
    "SMART_PROXY_ENABLED",
    "USER_AGENT",
    "USER_AGENT_MOBILE",
    "SEC_CH_UA",
    "FRONTEND_VERSION",
    "BAXIA_SDK_VERSION",
    "BXUA_VERSION",
    "CUSTOM_BASE64_CHARS",
    "DEFAULT_FULL_SETTINGS",
    "DEFAULT_FEATURE_CONFIG",
    "PERSIST_PATH",
    "TASK_TIMERS_PATH",
    "PROXY_SELECTOR_PERSIST_PATH",
    "GENERATED_IMAGE_DIR",
    "GENERATED_VIDEO_DIR",
    "TTS_DIR",
    "UPLOAD_TEMP_DIR",
    "LOGIN_BATCH",
    "LOGIN_BATCH_SIZE",
    "LOGIN_CONCURRENCY",
    "LOGIN_POOL_SIZE",
    "LOGIN_SELECT_MIN",
    "LOGIN_SELECT_MAX",
    "INITIAL_LOGIN_MAX",
    "LOGIN_POLL_INTERVAL",
    "TOKEN_EXPIRY_MARGIN",
    "TOKEN_REFRESH_INTERVAL",
    "COOKIE_REFRESH_INTERVAL",
    "PERSIST_INTERVAL",
    "SSE_TIMEOUT",
    "TTS_TIMEOUT",
    "VIDEO_CDN_BASE",
    "VIDEO_TASK_MAX_POLL_TIME",
    "VIDEO_TASK_POLL_INTERVAL",
    "build_cookie_string",
    "build_headers",
    "build_login_headers",
    "build_stop_headers",
    "build_payload",
    "build_new_chat_payload",
    "build_stop_payload",
    "build_i2v_payload",
    "build_tts_payload",
    "build_replace_content_payload",
    "build_file_object",
    "build_url_file_object",
    "build_cdn_video_url",
    "build_oss_authorization",
    "build_wav_from_pcm",
    "get_file_category",
    "get_mime_type",
    "save_image_file",
    "save_video_file",
    "save_wav_file",
    "extract_model_ids",
    "parse_sse_event",
    "parse_sse_line",
    "EXTENSION_TO_MIME",
    "DATA_URI_EXT_MAP",
    "HASH_FIELDS",
    "generate_bxua",
    "get_bxumidtoken",
    "get_baxia_tokens",
    "generate_device_id",
    "generate_fingerprint",
    "collect_fingerprint_data",
    "custom_encode",
    "lzw_compress",
    "hash_password",
]

# =======================================================================
# 重导出 — 同包内协同模块的公共符号（保持外部 ``from .. import`` 路径稳定）
# =======================================================================

from .headers import (
    make_request_id,
    make_timezone,
    build_cookie_string,
    merge_session_cookies,
    build_login_headers,
    build_headers,
    build_stop_headers,
)

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
    "make_request_id",
    "make_timezone",
    "build_cookie_string",
    "merge_session_cookies",
    "build_login_headers",
    "build_headers",
    "build_stop_headers",
    "build_payload",
    "build_new_chat_payload",
    "build_stop_payload",
    "build_i2v_payload",
    "build_tts_payload",
    "build_replace_content_payload",
    "parse_sse_event",
    "parse_sse_line",
]
