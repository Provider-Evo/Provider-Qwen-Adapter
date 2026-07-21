


from pathlib import Path
from typing import Optional

from src.foundation.config.reader import get_config_reader

_PLUGIN_DIR = Path(__file__).resolve().parents[3]


def load_use_proxy(default: bool = True) -> bool:
    """读取插件 config.toml 中的 use_proxy。"""
    reader = get_config_reader()
    config, _schema, _raw = reader.get_plugin_config(_PLUGIN_DIR)
    return bool(config.get("use_proxy", default))


class ProxyState:
    """Track whether proxy use is forced on, forced off, or inherited."""

    def __init__(self) -> None:
        self.override: Optional[bool] = None

    def set_enabled(self, enabled: bool) -> None:
        """Force proxy on or off."""
        self.override = bool(enabled)

    def load(self, override: Optional[bool]) -> None:
        """Restore the persisted override state."""
        self.override = override

    def is_enabled(self) -> bool:
        """Return whether proxy is currently forced on."""
        return bool(self.override)

    def to_dict(self) -> dict:
        """Serialize the state for persistence."""
        return {"enabled": self.override}

# =======================================================================
# 重导出 — 同包内协同模块的公共符号（保持外部 ``from .. import`` 路径稳定）
# =======================================================================

__all__ = [
]
