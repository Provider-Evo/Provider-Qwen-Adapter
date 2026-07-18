"""env 模块 — Provider 适配器层。

职责：
    作为 Provider-Evo 项目标准模块，提供 env 能力。

本文件为 Provider-Evo 项目标准模块；保持单文件 200-400 行。
修改指引参见文件末尾的"本模块对外契约"章节（共 20 条）。
"""



from pathlib import Path
from typing import Tuple

from provider_qwen.core.adapter.client import _load_accounts


def get_credentials() -> Tuple[str, str]:
    """Return credentials for the first account configured in config.toml."""
    accounts = _load_accounts()
    if not accounts:
        raise SystemExit("no Qwen accounts configured")
    return accounts[0].username, accounts[0].password
