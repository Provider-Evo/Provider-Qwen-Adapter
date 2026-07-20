


import base64
import os
import secrets
import time
import uuid
from typing import Dict

from .cookies import generate_cookies
from ..config.endpts import BAXIA_VERSION, CUSTOM_BASE64_CHARS
from .passwd import hash_password

BAXIA_VERSION = BAXIA_VERSION


def generate_device_id() -> str:
    """Return a browser-like device identifier."""
    return uuid.uuid4().hex


def collect_fingerprint_data() -> str:
    """Build the compact fingerprint string used for Baxia headers."""
    device_id = generate_device_id()
    fields = [
        device_id,
        "1.0.0",
        "web",
        "Chrome",
        "148.0.0.0",
        "zh-CN",
        "Asia/Shanghai",
        "1920x1080",
        "24",
        "Win32",
        "macOS",
        "Apple GPU",
        "Apple GPU",
        "desktop",
        "arena",
        "stable",
    ]
    return "^".join(fields)


def generate_fingerprint() -> str:
    """Return a stable-format fingerprint string."""
    return collect_fingerprint_data()


def _encode_payload(text: str) -> str:
    """Encode a Baxia payload with URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii").rstrip("=")


def generate_bxua(fingerprint: str) -> str:
    """Build the ``bx-ua`` header value."""
    payload = f"{fingerprint}|{int(time.time() * 1000)}|{BAXIA_VERSION}"
    return _encode_payload(payload)


def get_bxumidtoken(token: str = "") -> str:
    """Return the ``bx-umidtoken`` value, using env override when present."""
    if token:
        return token
    env_value = os.environ.get("QWEN_BX_UMIDTOKEN", "").strip()
    if env_value:
        return env_value
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    return "T2gA" + "".join(secrets.choice(alphabet) for _ in range(40))


def lzw_compress(data: str, bits: int = 6, alphabet: str = CUSTOM_BASE64_CHARS) -> str:
    """Compatibility placeholder for the historical LZW helper.

    The old adapter exported this name through ``shared.py``. The current
    package keeps the symbol available so legacy imports remain intact.
    """
    if not data:
        return ""
    encoded = base64.urlsafe_b64encode(data.encode("utf-8")).decode("ascii").rstrip("=")
    if alphabet == CUSTOM_BASE64_CHARS:
        return encoded.replace("_", "-")
    return encoded


def custom_encode(data: str, url_safe: bool = True) -> str:
    """Compatibility wrapper around the legacy custom encoder name."""
    encoded = lzw_compress(data)
    if url_safe:
        return encoded
    remainder = len(encoded) % 4
    if remainder:
        encoded += "=" * (4 - remainder)
    return encoded


def get_baxia_tokens() -> Dict[str, str]:
    """Return the Baxia header triplet required by current web requests."""
    fingerprint = generate_fingerprint()
    return {
        "bxV": BAXIA_VERSION,
        "bxUa": generate_bxua(fingerprint),
        "bxUmidToken": get_bxumidtoken(),
        "fingerprint": fingerprint,
    }
