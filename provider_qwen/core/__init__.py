from __future__ import annotations

"""Core exports for the Qwen adapter package."""

ACCOUNTS = []


def __getattr__(name):
    if name == "Account":
        from .adapter.client import Account as _Account
        return _Account
    raise AttributeError("module 'provider_qwen.core' has no attribute '{}'".format(name))


from .auth.crypto import (
    BAXIA_VERSION,
    collect_fingerprint_data,
    custom_encode,
    generate_bxua,
    generate_cookies,
    generate_device_id,
    generate_fingerprint,
    get_baxia_tokens,
    get_bxumidtoken,
    hash_password,
    lzw_compress,
)
from .auth.bxumid import validate_bxumidtoken
from .config.endpts import *

__all__ = [
    "BAXIA_VERSION",
    "generate_bxua",
    "get_bxumidtoken",
    "get_baxia_tokens",
    "hash_password",
    "collect_fingerprint_data",
    "generate_cookies",
    "generate_device_id",
    "generate_fingerprint",
    "custom_encode",
    "lzw_compress",
    "validate_bxumidtoken",
    "Account",
    "ACCOUNTS",
    "BASE_URL",
    "CHAT_PATH",
    "NEW_CHAT_PATH",
    "BXUA_VERSION",
    "CUSTOM_BASE64_CHARS",
    "USER_AGENT",
]
