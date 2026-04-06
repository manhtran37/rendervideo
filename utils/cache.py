"""
Cache render: cùng input + cấu hình → có thể bỏ qua render.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class CacheFingerprint:
    render_mode: str
    aspect: str
    gpu_mode: str
    audio_path: str
    footage_dir: str
    images_dir: str
    quality: str
    avatar_position: str
    rembg: bool
    footage_no_repeat: bool
    no_consecutive_footage: bool
    avatar_no_repeat_cycle: bool
    seed: Optional[int]
    encoder: str
    threads: str
    transition_effect: str
    transition_duration: float
    logo_path: str
    logo_position: str
    logo_width_frac: float
    logo_margin_frac: float
    subscribe_enabled: bool
    subscribe_animation_path: str
    subscribe_anim_width_frac: float
    subscribe_text: str
    subscribe_start_sec: float
    subscribe_duration_sec: float
    subscribe_show_count: int
    subscribe_pause_sec: float

    def stable_dict(self) -> dict[str, Any]:
        return asdict(self)


def _file_token(p: Path) -> str:
    try:
        st = p.stat()
        return f"{p.resolve()}|{st.st_mtime_ns}|{st.st_size}"
    except OSError:
        return f"{p}|missing"


def _dir_fingerprint(dir_path: Path, extensions: set[str]) -> str:
    if not dir_path.is_dir():
        return "nodir"
    parts: list[str] = []
    for root, _, files in os.walk(dir_path):
        for name in sorted(files):
            suf = Path(name).suffix.lower()
            if suf in extensions:
                parts.append(_file_token(Path(root) / name))
    return "\n".join(parts)


def compute_input_hash(
    audio: Path,
    fp: CacheFingerprint,
    *,
    footage_dir: Optional[Path],
    images_dir: Optional[Path],
) -> str:
    h = hashlib.sha256()
    h.update(_file_token(audio).encode("utf-8", errors="replace"))
    h.update(b"|")
    if footage_dir and footage_dir.is_dir():
        h.update(
            _dir_fingerprint(footage_dir, {".mp4", ".mov", ".mkv", ".webm", ".avi"}).encode(
                "utf-8", errors="replace"
            )
        )
    else:
        h.update(b"no_footage")
    h.update(b"|")
    if images_dir and images_dir.is_dir():
        h.update(
            _dir_fingerprint(images_dir, {".png", ".jpg", ".jpeg", ".webp"}).encode(
                "utf-8", errors="replace"
            )
        )
    else:
        h.update(b"no_images")
    h.update(b"|")
    if fp.logo_path:
        logo_p = Path(fp.logo_path)
        if logo_p.is_file():
            h.update(_file_token(logo_p).encode("utf-8", errors="replace"))
        else:
            h.update(b"logo_missing")
    else:
        h.update(b"no_logo")
    h.update(b"|")
    if fp.subscribe_animation_path:
        sap = Path(fp.subscribe_animation_path)
        if sap.is_file():
            h.update(_file_token(sap).encode("utf-8", errors="replace"))
        else:
            h.update(b"sub_anim_missing")
    else:
        h.update(b"no_sub_anim")
    h.update(b"|")
    h.update(json.dumps(fp.stable_dict(), sort_keys=True).encode("utf-8"))
    return h.hexdigest()


def cache_meta_path(output_dir: Path) -> Path:
    return output_dir / ".render_cache.json"


def load_cache_map(output_dir: Path) -> dict[str, str]:
    path = cache_meta_path(output_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def save_cache_map(output_dir: Path, mapping: dict[str, str]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_meta_path(output_dir).write_text(json.dumps(mapping, indent=2), encoding="utf-8")


def find_cached_output(output_dir: Path, input_hash: str) -> Optional[Path]:
    mapping = load_cache_map(output_dir)
    rel = mapping.get(input_hash)
    if not rel:
        return None
    p = output_dir / rel
    if p.is_file():
        return p
    return None
