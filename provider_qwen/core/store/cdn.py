


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
