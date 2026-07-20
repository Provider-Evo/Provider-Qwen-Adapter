

import json
from typing import Any, Dict, List, Optional, Union


def _safe_loads(data_str: str) -> Optional[Any]:
    if not data_str or data_str == "[DONE]":
        return None
    try:
        return json.loads(data_str)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _parse_head_event(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if "error" in data:
        error = data["error"]
        if isinstance(error, dict):
            return {"type": "error", "message": error}
        return {"type": "error", "message": str(error)}
    created = data.get("response.created")
    if isinstance(created, dict):
        return {"type": "response_created", "response_id": created.get("response_id", "")}
    return None


def _build_answer(delta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    content = delta.get("content")
    if content and delta.get("status") != "finished":
        return {"type": "answer", "content": content}
    return None


def _build_thinking(delta: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "thinking_summary",
        "status": delta.get("status") or "",
        "extra": delta.get("extra", {}),
    }


def _build_image_tool(delta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if delta.get("role") != "function" or delta.get("status") != "finished":
        return None
    extra = delta.get("extra", {})
    imgs = extra.get("image_list", extra.get("tool_result", []))
    urls = [
        img.get("image", "")
        for img in imgs
        if isinstance(img, dict) and img.get("image")
    ]
    if not urls:
        return None
    return {"type": "image_gen_tool", "urls": urls}


def _build_image_gen(delta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    content = delta.get("content")
    if not content:
        return None
    return {"type": "image_gen", "content": content, "extra": delta.get("extra", {})}


def _build_video_gen(delta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    content = delta.get("content")
    if not content:
        return None
    return {"type": "video_gen", "content": content}


def _build_other(delta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    phase = delta.get("phase")
    content = delta.get("content")
    status = delta.get("status")
    if phase is not None and phase != "" and content and status != "finished":
        return {"type": "other", "content": content}
    return None


def _dispatch_phase(delta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    phase = delta.get("phase")
    if phase == "answer":
        return _build_answer(delta)
    if phase == "think":
        content = delta.get("content")
        return {"type": "thinking", "content": content} if content else None
    if phase == "thinking_summary":
        return _build_thinking(delta)
    if phase == "image_gen_tool":
        return _build_image_tool(delta)
    if phase == "image_gen":
        return _build_image_gen(delta)
    if phase == "video_gen":
        return _build_video_gen(delta)
    return _build_other(delta)


def _parse_choice_event(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    usage = data.get("usage")
    choices = data.get("choices", [])
    if not choices:
        return {"type": "usage", "data": usage} if usage else None
    delta = choices[0].get("delta", {})
    result = _dispatch_phase(delta)
    if usage:
        if result is None:
            return {"type": "usage", "data": usage}
        result["usage"] = usage
    return result


def parse_sse_event(data_str: str) -> Optional[Dict[str, Any]]:
    """Parse one SSE ``data`` line into a structured event."""
    data = _safe_loads(data_str)
    if data is None:
        return None
    head = _parse_head_event(data)
    if head is not None:
        return head
    return _parse_choice_event(data)


def parse_sse_line(data_str: str) -> Optional[Union[str, Dict[str, Any]]]:
    """Map a raw SSE line into the public stream protocol."""
    event = parse_sse_event(data_str)
    if event is None:
        return None
    if event["type"] == "answer":
        return event.get("content", "")
    if event["type"] == "thinking":
        return {"thinking": event.get("content", "")}
    if event["type"] == "thinking_summary":
        extra = event.get("extra", {})
        titles: List[str] = extra.get("summary_title", {}).get("content", [])
        thoughts: List[str] = extra.get("summary_thought", {}).get("content", [])
        if not titles and not thoughts:
            return None
        parts: List[str] = []
        for index in range(max(len(titles), len(thoughts))):
            title = titles[index] if index < len(titles) else ""
            thought = thoughts[index] if index < len(thoughts) else ""
            if title or thought:
                parts.append(f"{title}: {thought}" if title else thought)
        return {"thinking": "\n".join(parts)} if parts else None
    if event["type"] == "usage":
        return {"usage": event.get("data", {})}
    return None

# =======================================================================
# 重导出 — 同包内协同模块的公共符号（保持外部 ``from .. import`` 路径稳定）
# =======================================================================

from .headers import (
    make_request_id,
    make_timezone,
    build_cookie_string,
    merge_session_cookies,
    build_login_headers,
    build_headers,
    build_stop_headers,
)

from .payload import (
    build_payload,
    build_new_chat_payload,
    build_stop_payload,
    build_i2v_payload,
    build_tts_payload,
    build_replace_content_payload,
)

__all__ = [
    "make_request_id",
    "make_timezone",
    "build_cookie_string",
    "merge_session_cookies",
    "build_login_headers",
    "build_headers",
    "build_stop_headers",
    "build_payload",
    "build_new_chat_payload",
    "build_stop_payload",
    "build_i2v_payload",
    "build_tts_payload",
    "build_replace_content_payload",
]
