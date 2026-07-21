
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

_KEYS: Tuple[str, ...] = ("id", "modelId", "model_id", "name")


def _yield_dict_id(item: dict) -> Optional[str]:
    for key in _KEYS:
        value = item.get(key)
        if isinstance(value, str):
            return value
    return None


def _iter_ids(items: Iterable[Any]) -> Iterable[str]:
    for item in items:
        if isinstance(item, str):
            yield item
            continue
        if not isinstance(item, dict):
            continue
        value = _yield_dict_id(item)
        if value is not None:
            yield value


def iter_catalog_items(raw: Any) -> List[Dict[str, Any]]:
    """Normalize Qwen /v2/models payloads into a list of model dicts."""
    if not isinstance(raw, dict):
        return []
    if raw.get("success") is False:
        return []

    blocks: List[List[Any]] = []
    data = raw.get("data")
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, list):
            blocks.append(inner)
        models = data.get("models")
        if isinstance(models, list):
            blocks.append(models)
    elif isinstance(data, list):
        blocks.append(data)

    simple = raw.get("models")
    if isinstance(simple, list):
        blocks.append(simple)

    out: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for block in blocks:
        for item in block:
            if not isinstance(item, dict):
                continue
            model_id = _yield_dict_id(item)
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            out.append(item)
    return out


def extract_model_ids(raw: Any) -> List[str]:
    """Extract de-duplicated model identifiers from heterogeneous payloads."""
    ids: List[str] = []
    seen: Set[str] = set()
    for item in iter_catalog_items(raw):
        model_id = _yield_dict_id(item)
        if model_id and model_id not in seen:
            seen.add(model_id)
            ids.append(model_id)
    return ids


def _bool_caps(meta: Dict[str, Any]) -> Dict[str, bool]:
    """Map Qwen model meta to Provider candidate capabilities."""
    caps: Dict[str, bool] = {"chat": True, "completions": True}

    api_caps = meta.get("capabilities")
    if isinstance(api_caps, dict):
        if api_caps.get("vision"):
            caps["vision"] = True
        if api_caps.get("video"):
            caps["video_gen"] = True
        if api_caps.get("audio"):
            caps["audio_in"] = True
            caps["audio_gen"] = True
        if api_caps.get("document"):
            caps["upload"] = True
        if api_caps.get("thinking"):
            caps["thinking"] = True
        if api_caps.get("search"):
            caps["search"] = True
        if api_caps.get("citations"):
            caps["research"] = True

    chat_types = meta.get("chat_type")
    if isinstance(chat_types, list):
        if "t2i" in chat_types:
            caps["image_gen"] = True
        if "image_edit" in chat_types:
            caps["image_edit"] = True
        if "t2v" in chat_types:
            caps["video_gen"] = True
        if "artifacts" in chat_types or "web_dev" in chat_types:
            caps["artifacts"] = True
        if "deep_research" in chat_types:
            caps["research"] = True
        if "search" in chat_types:
            caps["search"] = True

    modality = meta.get("modality")
    if isinstance(modality, list):
        if "image" in modality:
            caps["vision"] = True
        if "video" in modality:
            caps["video_gen"] = True
        if "audio" in modality:
            caps["audio_in"] = True

    mcp = meta.get("mcp")
    if isinstance(mcp, list) and mcp:
        caps["tools"] = True
        if "code-interpreter" in mcp:
            caps["code_exec"] = True

    return caps


def _context_length(meta: Dict[str, Any]) -> Optional[int]:
    for key in (
        "max_context_length",
        "context_length",
        "max_summary_generation_length",
    ):
        raw = meta.get(key)
        if isinstance(raw, int) and raw > 0:
            return raw
        if isinstance(raw, str) and raw.isdigit():
            return int(raw)
    return None


def _build_model_info(item: Dict[str, Any]) -> Dict[str, Any]:
    info = item.get("info") if isinstance(item.get("info"), dict) else {}
    meta = info.get("meta") if isinstance(info.get("meta"), dict) else {}
    record: Dict[str, Any] = {
        "id": item.get("id") or info.get("id"),
        "name": item.get("name") or info.get("name"),
        "object": item.get("object"),
        "owned_by": item.get("owned_by"),
        "preset": item.get("preset"),
        "action_ids": item.get("action_ids"),
        "user_id": info.get("user_id"),
        "base_model_id": info.get("base_model_id"),
        "access_control": info.get("access_control"),
        "is_active": info.get("is_active"),
        "is_visitor_active": info.get("is_visitor_active"),
        "updated_at": info.get("updated_at"),
        "created_at": info.get("created_at"),
        "profile_image_url": meta.get("profile_image_url"),
        "description": meta.get("description"),
        "short_description": meta.get("short_description"),
        "capabilities": meta.get("capabilities"),
        "abilities": meta.get("abilities"),
        "modality": meta.get("modality"),
        "chat_type": meta.get("chat_type"),
        "mcp": meta.get("mcp"),
        "max_context_length": meta.get("max_context_length"),
        "max_summary_generation_length": meta.get("max_summary_generation_length"),
        "max_thinking_generation_length": meta.get("max_thinking_generation_length"),
        "max_generation_length": meta.get("max_generation_length"),
        "auto_thinking": meta.get("auto_thinking"),
        "auto_search": meta.get("auto_search"),
        "thinking_format": meta.get("thinking_format"),
        "think_skip": meta.get("think_skip"),
        "file_limits": meta.get("file_limits"),
    }
    return {key: value for key, value in record.items() if value is not None}


def parse_model_catalog(
    raw: Any,
) -> Tuple[List[str], Dict[str, Dict[str, bool]], Dict[str, int], Dict[str, Any]]:
    """Parse catalog into ids, per-model caps, context lengths, and rich info."""
    ids: List[str] = []
    model_capabilities: Dict[str, Dict[str, bool]] = {}
    model_context: Dict[str, int] = {}
    model_info: Dict[str, Any] = {}
    seen: Set[str] = set()

    for item in iter_catalog_items(raw):
        model_id = _yield_dict_id(item)
        if not model_id or model_id in seen:
            continue

        info = item.get("info") if isinstance(item.get("info"), dict) else {}
        if info.get("is_active") is False:
            continue

        seen.add(model_id)
        ids.append(model_id)

        meta = info.get("meta") if isinstance(info.get("meta"), dict) else {}
        model_capabilities[model_id] = _bool_caps(meta)
        ctx = _context_length(meta)
        if ctx is not None:
            model_context[model_id] = ctx
        model_info[model_id] = _build_model_info(item)

    return ids, model_capabilities, model_context, model_info


__all__ = [
    "extract_model_ids",
    "iter_catalog_items",
    "parse_model_catalog",
]
