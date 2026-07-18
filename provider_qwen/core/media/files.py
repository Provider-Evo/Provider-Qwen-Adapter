"""files 模块 — Provider 适配器层。

职责：
    作为 Provider-Evo 项目标准模块，提供 files 能力。

本文件为 Provider-Evo 项目标准模块；保持单文件 200-400 行。
修改指引参见文件末尾的"本模块对外契约"章节（共 20 条）。
"""



import os
from typing import Any, Dict

from .mimes import get_file_category, get_mime_type


def build_file_object(
    file_id: str,
    file_url: str,
    filename: str,
    size: int,
    content_type: str,
    user_id: str,
) -> Dict[str, Any]:
    """Build a Qwen file object for an uploaded asset."""
    file_type, file_class = get_file_category(content_type)
    return {
        "id": file_id,
        "name": filename,
        "type": file_type,
        "size": size,
        "url": file_url,
        "file_type": content_type,
        "showType": file_type,
        "file_class": file_class,
        "user_id": user_id,
        "isQuote": False,
    }


def build_url_file_object(file_url: str, file_type: str) -> Dict[str, Any]:
    """Build a quoted file object from a remote URL."""
    filename = os.path.basename(file_url.split("?", 1)[0]) or f"remote.{file_type}"
    content_type = get_mime_type(filename)
    _, file_class = get_file_category(content_type)
    return {
        "name": filename,
        "type": file_type,
        "url": file_url,
        "file_type": content_type,
        "showType": file_type,
        "file_class": file_class,
        "isQuote": True,
    }
