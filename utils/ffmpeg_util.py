"""
Tiện ích gọi ffprobe/ffmpeg: thời lượng, encode, log stderr.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def find_ffmpeg() -> Optional[str]:
    """Trả về đường dẫn ffmpeg trong PATH hoặc None."""
    return shutil.which("ffmpeg")


def find_ffprobe() -> Optional[str]:
    """Trả về đường dẫn ffprobe trong PATH hoặc None."""
    return shutil.which("ffprobe")


def check_ffmpeg_available() -> tuple[bool, str]:
    """Kiểm tra ffmpeg + ffprobe có sẵn."""
    ff = find_ffmpeg()
    fp = find_ffprobe()
    if not ff:
        return False, "Không tìm thấy ffmpeg trong PATH. Cài đặt FFmpeg và thêm vào PATH."
    if not fp:
        return False, "Không tìm thấy ffprobe trong PATH."
    return True, ""


def probe_duration_seconds(path: Path) -> float:
    """
    Đọc thời lượng (giây) của file media bằng ffprobe.
    """
    ffprobe = find_ffprobe()
    if not ffprobe:
        raise RuntimeError("ffprobe không có trong PATH")

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        str(path),
    ]
    out = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="replace")
    data = json.loads(out)
    dur = float(data.get("format", {}).get("duration", 0) or 0)
    if dur <= 0:
        raise ValueError(f"Không đọc được duration: {path}")
    return dur


def run_ffmpeg(
    args: list[str],
    on_line: Optional[Callable[[str], None]] = None,
    cwd: Optional[Path] = None,
) -> subprocess.Popen:
    """
    Chạy ffmpeg; trả về Popen để caller có thể terminate().
    Gọi communicate() hoặc đọc stderr trong thread.
    """
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("ffmpeg không có trong PATH")

    full = [ffmpeg, *args]
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]

    proc = subprocess.Popen(
        full,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd) if cwd else None,
        creationflags=creationflags,
    )

    if on_line and proc.stderr:
        # Đọc stderr trong thread gọi từ bên ngoài (renderer)
        pass
    return proc


def read_stderr_lines(proc: subprocess.Popen, on_line: Callable[[str], None]) -> int:
    """Đọc toàn bộ stderr của process; gọi on_line từng dòng. Trả về returncode."""
    assert proc.stderr is not None
    for line in proc.stderr:
        on_line(line.rstrip())
    return proc.wait()
