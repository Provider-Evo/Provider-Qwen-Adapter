"""password 模块 — Provider 适配器层。

职责：
    作为 Provider-Evo 项目标准模块，提供 password 能力。

本文件为 Provider-Evo 项目标准模块；保持单文件 200-400 行。
修改指引参见文件末尾的"本模块对外契约"章节（共 20 条）。
"""



import hashlib


def hash_password(password: str) -> str:
    """Return the SHA-256 digest used by the Qwen web login flow."""
    return hashlib.sha256(password.encode("utf-8")).hexdigest()
