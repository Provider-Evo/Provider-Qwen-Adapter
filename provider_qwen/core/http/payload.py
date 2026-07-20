from __future__ import annotations

"""Request-payload builders aligned with the current Qwen web protocol."""

import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from ..config.endpts import API_VERSION, USE_LOCAL_MODE

DEFAULT_FEATURE_CONFIG: Dict[str, Any] = {
    "thinking_enabled": True,
    "output_schema": "phase",
    "research_mode": "normal",
    "auto_thinking": False,
    "thinking_mode": "Thinking",
    "thinking_format": "raw",
    "auto_search": False,
}


def _extract_url(url_obj: Any) -> str:
    if isinstance(url_obj, dict):
        return str(url_obj.get("url", "") or "")
    if isinstance(url_obj, str):
        return url_obj
    return ""


def _append_media_file(
    extra_files: List[Dict[str, Any]],
    url: str,
    media_type: str,
) -> None:
    from ..media.files import build_url_file_object
    if url and not url.startswith("data:"):
        extra_files.append(build_url_file_object(url, media_type))


def _collect_content_part(
    part: Any,
    text_parts: List[str],
    extra_files: List[Dict[str, Any]],
) -> None:
    if not isinstance(part, dict):
        return
    part_type = part.get("type")
    if part_type == "text":
        text_parts.append(str(part.get("text", "")))
        return
    if part_type == "image_url":
        _append_media_file(extra_files, _extract_url(part.get("image_url")), "image")
        return
    if part_type == "video_url":
        _append_media_file(extra_files, _extract_url(part.get("video_url")), "video")
        return
    if part_type != "input_audio":
        return
    audio_obj = part.get("input_audio") or {}
    url = _extract_url(audio_obj.get("url") if isinstance(audio_obj, dict) else "")
    _append_media_file(extra_files, url, "audio")


def _collect_user_content(messages: List[Dict[str, Any]]) -> Tuple[List[str], List[Dict[str, Any]]]:
    text_parts: List[str] = []
    extra_files: List[Dict[str, Any]] = []
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content", "")
        if isinstance(content, str):
            text_parts.append(content)
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            _collect_content_part(part, text_parts, extra_files)
    return text_parts, extra_files


def _build_feature_config(
    thinking_enabled: bool,
    auto_thinking: bool,
    thinking_mode: str,
    thinking_format: str,
    auto_search: bool,
) -> Dict[str, Any]:
    feature_config = dict(DEFAULT_FEATURE_CONFIG)
    feature_config.update(
        {
            "thinking_enabled": thinking_enabled,
            "auto_thinking": auto_thinking,
            "thinking_mode": thinking_mode,
            "thinking_format": thinking_format,
            "auto_search": auto_search,
        }
    )
    return feature_config


def _build_user_message(
    *,
    content: str,
    all_files: List[Dict[str, Any]],
    model: str,
    parent_id: Optional[str],
    chat_type: str,
    effective_sub_chat_type: str,
    feature_config: Dict[str, Any],
    timestamp: int,
    assistant_fid: str,
) -> Dict[str, Any]:
    return {
        "fid": str(uuid.uuid4()),
        "parentId": parent_id,
        "childrenIds": [assistant_fid],
        "role": "user",
        "content": content,
        "user_action": "chat",
        "files": all_files,
        "timestamp": timestamp,
        "models": [model],
        "chat_type": chat_type,
        "feature_config": feature_config,
        "extra": {"meta": {"subChatType": effective_sub_chat_type}},
        "sub_chat_type": effective_sub_chat_type,
    }


def build_payload(
    messages: List[Dict[str, Any]],
    model: str,
    chat_id: str,
    *,
    files: Optional[List[Dict[str, Any]]] = None,
    chat_type: str = "t2t",
    sub_chat_type: Optional[str] = None,
    parent_id: Optional[str] = None,
    thinking_enabled: bool = True,
    auto_thinking: bool = False,
    thinking_mode: str = "Thinking",
    thinking_format: str = "raw",
    auto_search: bool = False,
    stream: bool = True,
) -> Dict[str, Any]:
    """Build the current chat-completions payload."""
    effective_sub_chat_type = sub_chat_type or chat_type
    text_parts, extra_files = _collect_user_content(messages)
    content = "\n".join(part for part in text_parts if part)
    all_files = list(files or []) + extra_files
    assistant_fid = str(uuid.uuid4())
    timestamp = int(time.time() * 1000)
    feature_config = _build_feature_config(
        thinking_enabled, auto_thinking, thinking_mode, thinking_format, auto_search,
    )
    user_message = _build_user_message(
        content=content,
        all_files=all_files,
        model=model,
        parent_id=parent_id,
        chat_type=chat_type,
        effective_sub_chat_type=effective_sub_chat_type,
        feature_config=feature_config,
        timestamp=timestamp,
        assistant_fid=assistant_fid,
    )
    return {
        "stream": stream,
        "version": API_VERSION,
        "incremental_output": True,
        "chat_id": chat_id,
        "chat_mode": "local" if USE_LOCAL_MODE else "normal",
        "model": model,
        "parent_id": parent_id,
        "messages": [user_message],
        "timestamp": timestamp,
        "stream_options": {"include_usage": True},
    }


def build_new_chat_payload(model: str, chat_type: str = "t2t") -> Dict[str, Any]:
    """Build the create-chat payload."""
    return {
        "title": "新建对话",
        "models": [model],
        "chat_mode": "local" if USE_LOCAL_MODE else "normal",
        "chat_type": chat_type,
        "timestamp": int(time.time() * 1000),
        "project_id": "",
    }


def build_stop_payload(chat_id: str, response_id: str = "") -> Dict[str, Any]:
    """Build the stop-generation payload."""
    payload: Dict[str, Any] = {"chat_id": chat_id}
    if response_id:
        payload["response_id"] = response_id
    return payload


def build_i2v_payload(
    prompt: str,
    chat_id: str,
    model: str,
    image_url: str,
    image_name: str,
    size: str,
    parent_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the image-to-video payload."""
    file_obj = {
        "type": "image",
        "name": image_name,
        "file_type": "image/png",
        "showType": "image",
        "file_class": "vision",
        "url": image_url,
        "isQuote": True,
    }
    payload = build_payload(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        chat_id=chat_id,
        files=[file_obj],
        chat_type="i2v",
        sub_chat_type="i2v",
        parent_id=parent_id,
        thinking_enabled=True,
        auto_thinking=False,
        thinking_mode="Thinking",
        thinking_format="raw",
        auto_search=False,
        stream=False,
    )
    payload["chat_mode"] = "normal"
    payload["size"] = size
    payload["messages"][0]["extra"] = {"meta": {"subChatType": "i2v", "size": size}}
    return payload


def build_tts_payload(chat_id: str, response_id: str) -> Dict[str, Any]:
    """Build the TTS payload."""
    return {
        "chat_id": chat_id,
        "timestamp": int(time.time()),
        "messages": [{"id": response_id, "role": "assistant", "sub_chat_type": "tts"}],
    }


def build_replace_content_payload(new_content: str, origin_content: str) -> Dict[str, Any]:
    """Build the content-replacement payload used before TTS."""
    return {
        "content_list": [
            {
                "content": new_content,
                "phase": "answer",
                "status": "finished",
                "extra": None,
                "role": "assistant",
                "usage": {
                    "input_tokens": max(1, len(origin_content) // 3),
                    "output_tokens": max(1, len(new_content) // 3),
                    "total_tokens": max(1, (len(origin_content) + len(new_content)) // 3),
                    "prompt_tokens_details": {"cached_tokens": 0},
                },
            }
        ]
    }
