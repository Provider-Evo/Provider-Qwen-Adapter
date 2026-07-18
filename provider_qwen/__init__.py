from __future__ import annotations

"""Qwen 平台适配器模块。

导出 QwenAdapter 与 Adapter 类，供注册表自动发现和加载。
"""

from typing import Any


def __getattr__(name: str) -> Any:
    """模块级懒属性，按需导入适配器类。"""
    if name in ("QwenAdapter", "Adapter"):
        from provider_qwen.core.adapter.acore import (  # noqa: PLC0415
            QwenAdapter as _QwenAdapter,
        )

        return _QwenAdapter
    raise AttributeError(
        "module 'src.platforms.qwen' has no attribute '{}'".format(name)
    )


__all__ = ["QwenAdapter", "Adapter"]
