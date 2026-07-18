"""cdn 模块 — Provider 适配器层。

职责：
    作为 Provider-Evo 项目标准模块，提供 cdn 能力。

本文件为 Provider-Evo 项目标准模块；保持单文件 200-400 行。
修改指引参见文件末尾的"本模块对外契约"章节（共 20 条）。
"""



from ..config.endpts import VIDEO_CDN_BASE


def build_cdn_video_url(
    user_id: str,
    video_type: str,
    message_id: str,
    task_id: str,
    token: str,
) -> str:
    """Build the fallback CDN URL for a generated video."""
    return (
        f"{VIDEO_CDN_BASE}/{user_id}/{video_type}/{message_id}/{task_id}.mp4?key={token}"
    )
