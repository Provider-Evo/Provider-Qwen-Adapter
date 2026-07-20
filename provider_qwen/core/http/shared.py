

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
