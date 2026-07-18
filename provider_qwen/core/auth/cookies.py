"""cookies 模块 — Provider 适配器层。

职责：
    作为 Provider-Evo 项目标准模块，提供 cookies 能力。

本文件为 Provider-Evo 项目标准模块；保持单文件 200-400 行。
修改指引参见文件末尾的"本模块对外契约"章节（共 20 条）。
"""



from typing import Any, Dict, Final

HASH_FIELDS: Final[list] = [
    "ssxmod_itna",
    "ssxmod_itna2",
    "bx-umidtoken",
    "bx-ua",
]


def generate_cookies(fingerprint: str) -> Dict[str, Any]:
    """Return a compatibility cookie mapping.

    The modern Qwen flow can operate without the old SSXMOD cookies, but the
    adapter preserves these keys so legacy code paths do not break.
    """
    return {
        "ssxmod_itna": "",
        "ssxmod_itna2": "",
        "fingerprint": fingerprint,
    }
