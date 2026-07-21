from __future__ import annotations

"""Qwen 模型 ID 映射 — 复用 provider_sdk 实现。"""

from typing import Dict, Iterable, List, Tuple, TypeVar

from provider_sdk.model_ids import (
    ModelIdRegistry,
    build_model_id_maps,
    upstream_to_public_id,
)

T = TypeVar("T")


def remap_dict_keys(mapping: Dict[str, T], id_map: Dict[str, str]) -> Dict[str, T]:
    """将 dict 的 key 从上游 ID 重映射为公开 ID。"""
    out: Dict[str, T] = {}
    for key, value in mapping.items():
        public = id_map.get(key, upstream_to_public_id(key))
        out[public] = value
    return out


def load_model_id_map() -> Dict[str, str]:
    """从持久化文件加载 public → upstream 映射（兼容旧调用）。"""
    registry = ModelIdRegistry("qwen")
    registry.load()
    return registry.public_to_upstream


def save_model_id_map(public_to_upstream: Dict[str, str]) -> None:
    """持久化 public → upstream 映射（兼容旧调用）。"""
    registry = ModelIdRegistry("qwen")
    registry.load()
    upstream_ids = sorted(
        {str(v) for k, v in public_to_upstream.items() if k and v}
    )
    if upstream_ids:
        registry.register_many(upstream_ids)


__all__ = [
    "ModelIdRegistry",
    "upstream_to_public_id",
    "build_model_id_maps",
    "remap_dict_keys",
    "load_model_id_map",
    "save_model_id_map",
]
