"""
Encoder GPU (NVENC) / CPU (libx264) theo chế độ: chỉ GPU, chỉ CPU, hoặc GPU rồi CPU.
"""

from __future__ import annotations

import subprocess
import sys

from utils.ffmpeg_util import find_ffmpeg

# Giá trị từ UI / RenderSettings
GPU_MODE_GPU_ONLY = "gpu_only"
GPU_MODE_CPU_ONLY = "cpu_only"
GPU_MODE_GPU_THEN_CPU = "gpu_then_cpu"


def nvidia_nvenc_available() -> bool:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    try:
        r = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=creationflags,
        )
        return "h264_nvenc" in (r.stdout or "") + (r.stderr or "")
    except (subprocess.TimeoutExpired, OSError):
        return False


def libx264_args() -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
    ]


def nvenc_args_minimal() -> list[str]:
    return [
        "-c:v",
        "h264_nvenc",
        "-preset",
        "fast",
        "-pix_fmt",
        "yuv420p",
    ]


def build_encoder_attempts(gpu_mode: str) -> list[tuple[str, list[str]]]:
    """
    gpu_only → chỉ NVENC (rỗng nếu không có encoder).
    cpu_only → chỉ libx264.
    gpu_then_cpu → NVENC rồi libx264 nếu lỗi.
    """
    mode = (gpu_mode or GPU_MODE_GPU_THEN_CPU).strip().lower()
    out: list[tuple[str, list[str]]] = []

    if mode == GPU_MODE_CPU_ONLY:
        out.append(("libx264", libx264_args()))
        return out

    if mode == GPU_MODE_GPU_ONLY:
        if nvidia_nvenc_available():
            out.append(("h264_nvenc", nvenc_args_minimal()))
        return out

    # gpu_then_cpu (mặc định)
    if nvidia_nvenc_available():
        out.append(("h264_nvenc", nvenc_args_minimal()))
    out.append(("libx264", libx264_args()))
    return out


def encoder_cache_key(gpu_mode: str) -> str:
    return (gpu_mode or GPU_MODE_GPU_THEN_CPU).strip().lower()


def pick_video_encoder(prefer_gpu: bool = True) -> tuple[str, list[str]]:
    """API cũ — map sang chế độ đơn giản."""
    if prefer_gpu and nvidia_nvenc_available():
        return ("h264_nvenc", nvenc_args_minimal())
    return ("libx264", libx264_args())
