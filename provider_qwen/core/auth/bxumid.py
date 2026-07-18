"""bxumid 模块 — Provider 适配器层。

职责：
    作为 Provider-Evo 项目标准模块，提供 bxumid 能力。

本文件为 Provider-Evo 项目标准模块；保持单文件 200-400 行。
修改指引参见文件末尾的"本模块对外契约"章节（共 20 条）。
"""



import re


def validate_bxumidtoken(token: str) -> bool:
    """Return whether the token matches the expected compact format."""
    return bool(token and re.fullmatch(r"(?:T2gA)?[A-Za-z0-9+/=]{20,}", token))
