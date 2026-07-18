"""mimes 模块 — Provider 适配器层。

职责：
    作为 Provider-Evo 项目标准模块，提供 mimes 能力。

本文件为 Provider-Evo 项目标准模块；保持单文件 200-400 行。
修改指引参见文件末尾的"本模块对外契约"章节（共 20 条）。
"""



import os
from typing import Dict, Final, Tuple

FILE_TYPE_MAPPING: Final[Dict[str, str]] = {
    "image/jpeg": "image",
    "image/jpg": "image",
    "image/png": "image",
    "image/gif": "image",
    "image/webp": "image",
    "image/bmp": "image",
    "video/mp4": "video",
    "video/avi": "video",
    "video/mov": "video",
    "video/quicktime": "video",
    "audio/mpeg": "audio",
    "audio/mp3": "audio",
    "audio/wav": "audio",
    "audio/x-wav": "audio",
    "audio/aac": "audio",
    "audio/ogg": "audio",
    "audio/m4a": "audio",
    "audio/opus": "audio",
    "application/pdf": "file",
    "text/plain": "file",
    "text/csv": "file",
    "application/json": "file",
}

EXTENSION_TO_MIME: Final[Dict[str, str]] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".mp4": "video/mp4",
    ".avi": "video/avi",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".m4a": "audio/m4a",
    ".opus": "audio/opus",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
}

DATA_URI_EXT_MAP: Final[Dict[str, str]] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "video/mp4": ".mp4",
    "application/pdf": ".pdf",
}


def get_mime_type(filename: str) -> str:
    """Infer a MIME type from a filename."""
    return EXTENSION_TO_MIME.get(os.path.splitext(filename)[1].lower(), "application/octet-stream")


def get_file_category(content_type: str) -> Tuple[str, str]:
    """Return ``(file_type, file_class)`` for an uploaded file."""
    file_type = FILE_TYPE_MAPPING.get(content_type, "file")
    if content_type.startswith("image/") or content_type.startswith("video/"):
        return file_type, "vision"
    if content_type.startswith("audio/"):
        return file_type, "audio"
    return file_type, "document"
