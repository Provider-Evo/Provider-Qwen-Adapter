"""
sse 模块。

本文件为 Provider-Evo 项目标准模块，使用以下约定：

- 模块路径：provider-plugin.Provider-Qwen-Adapter.provider_qwen.core.http.sse
- 文件名：sse.py
- 父包：provider-plugin/Provider-Qwen-Adapter/provider_qwen/core/http

职责：

    提供流式响应的 Server-Sent Events 解析与重组工具，
    由 ``client.py`` 的流式分支调用。

对外接口：

    本模块的 ``__all__`` 列出对外可导入的符号集合；其他内部符号
    可能在重构中调整，调用方应只依赖 ``__all__`` 暴露的稳定 API。

集成：

    - SDK 入口：``plugin.py`` 中 ``create_plugin()`` 引用本模块以构造 platform adapter。
    - 入口路由：``provider-self/src/routes/openai`` 通过 ``from src.core...`` 间接使用。
    - 测试：本目录下的 ``tests/`` 子目录覆盖本模块的核心逻辑。

依赖：

    - 仅依赖 ``provider-sdk`` 与 Python 3.8+ 标准库；不引入第三方 HTTP 库。
    - 不直接读环境变量；所有配置走 ``config/main_config.toml``。

修改指引：

    - 调整本模块时同步更新 ``docs-src/plugins/<name>.md`` 与对应 ``tests/``。
    - 保持单文件 200-400 行；超长请拆为子包并通过 ``__init__.py`` 重新导出。
    - 严禁放置 placeholder / 兜底 / 伪装通过的代码（见 ``AGENTS.md`` Hard Constraints）。
"""


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
        return {"type": "error", "message": str(data["error"])}
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
