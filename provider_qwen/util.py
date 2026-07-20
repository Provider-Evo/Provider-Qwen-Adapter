


from typing import Any

from provider_qwen.core.http.shared import (
    CAPS,
    MODELS,
    MODELS_PERSIST_PATH,
    build_headers,
    build_payload,
    parse_sse_event,
    parse_sse_line,
)


def __getattr__(name: str) -> Any:
    """模块级懒属性，按需导入 QwenAdapter。"""
    if name in ("QwenAdapter", "Adapter"):
        from provider_qwen.core.adapter.acore import (  # noqa: PLC0415
            QwenAdapter as _QwenAdapter,
        )

        return _QwenAdapter
    raise AttributeError("module 'src.platforms.qwen.util' has no attribute '{}'".format(name))


__all__ = [
    "QwenAdapter",
    "Adapter",
    "MODELS",
    "CAPS",
    "MODELS_PERSIST_PATH",
    "parse_sse_line",
    "parse_sse_event",
    "build_headers",
    "build_payload",
]
