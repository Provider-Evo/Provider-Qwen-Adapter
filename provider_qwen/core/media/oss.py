"""oss 模块 — Provider 适配器层。

职责：
    作为 Provider-Evo 项目标准模块，提供 oss 能力。

本文件为 Provider-Evo 项目标准模块；保持单文件 200-400 行。
修改指引参见文件末尾的"本模块对外契约"章节（共 20 条）。
"""



import base64
import hashlib
import hmac
from typing import Dict


def build_oss_authorization(
    method: str,
    content_type: str,
    date: str,
    oss_headers: Dict[str, str],
    resource: str,
    access_key_id: str,
    access_key_secret: str,
) -> str:
    """Build an OSS V1 authorization header."""
    canonicalized = ""
    if oss_headers:
        canonicalized = "\n".join(
            f"{key}:{value}" for key, value in sorted(oss_headers.items())
        ) + "\n"
    string_to_sign = (
        f"{method}\n\n{content_type}\n{date}\n{canonicalized}{resource}"
    )
    digest = hmac.new(
        access_key_secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        hashlib.sha1,
    ).digest()
    return f"OSS {access_key_id}:{base64.b64encode(digest).decode('ascii')}"
