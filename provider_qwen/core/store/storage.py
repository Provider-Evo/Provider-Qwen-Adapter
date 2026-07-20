


import struct
import time
import uuid
from pathlib import Path
from typing import Final, Optional

from ..config.endpts import GENERATED_IMAGE_DIR, GENERATED_VIDEO_DIR, TTS_DIR

_IMAGE_EXTENSIONS: Final[dict] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def build_wav_from_pcm(
    pcm_data: bytes,
    sample_rate: int = 24000,
    channels: int = 1,
    bits_per_sample: int = 16,
) -> bytes:
    """Wrap PCM audio bytes in a WAV container."""
    data_size = len(pcm_data)
    byte_rate = sample_rate * channels * bits_per_sample // 8
    block_align = channels * bits_per_sample // 8
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits_per_sample,
        b"data",
        data_size,
    )
    return header + pcm_data


def _make_path(save_dir: str, prefix: str, ext: str) -> Path:
    Path(save_dir).mkdir(parents=True, exist_ok=True)
    return Path(save_dir) / f"{prefix}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}{ext}"


def save_wav_file(pcm_data: bytes, save_dir: str = TTS_DIR) -> Optional[str]:
    """Save PCM bytes as a WAV file and return the path."""
    path = _make_path(save_dir, "tts", ".wav")
    path.write_bytes(build_wav_from_pcm(pcm_data))
    return str(path)


def save_image_file(
    image_data: bytes,
    content_type: str = "image/png",
    save_dir: str = GENERATED_IMAGE_DIR,
) -> Optional[str]:
    """Save image bytes and return the local path."""
    ext = _IMAGE_EXTENSIONS.get(content_type.split(";", 1)[0].strip(), ".png")
    path = _make_path(save_dir, "generated", ext)
    path.write_bytes(image_data)
    return str(path)


def save_video_file(video_data: bytes, save_dir: str = GENERATED_VIDEO_DIR) -> Optional[str]:
    """Save video bytes and return the local path."""
    path = _make_path(save_dir, "video", ".mp4")
    path.write_bytes(video_data)
    return str(path)
