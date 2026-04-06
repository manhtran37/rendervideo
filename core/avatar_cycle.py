"""
Trạng thái chọn avatar theo chu kỳ (không lặp cho đến khi dùng hết).
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import List, Optional, cast

STATE_NAME = ".avatar_cycle_state.json"


def _state_path(output_dir: Path) -> Path:
    return output_dir / STATE_NAME


def _folder_key(avatar_dir: Path) -> str:
    try:
        return str(avatar_dir.resolve())
    except OSError:
        return str(avatar_dir)


def load_state(output_dir: Path) -> dict:
    p = _state_path(output_dir)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(output_dir: Path, data: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _state_path(output_dir).write_text(json.dumps(data, indent=2), encoding="utf-8")


def pick_avatar_path(
    image_paths: List[Path],
    rng: random.Random,
    *,
    cycle_without_repeat: bool,
    output_dir: Path,
) -> Path:
    """
    Chọn một ảnh avatar.

    - cycle_without_repeat: lần lượt hết danh sách đã xáo; hết thì xáo lại (lưu state theo thư mục).
    - Ngược lại: random độc lập mỗi lần render.
    """
    if not image_paths:
        raise ValueError("Thư mục avatar trống.")

    if not cycle_without_repeat:
        return rng.choice(image_paths)

    key = _folder_key(image_paths[0].parent)
    state = load_state(output_dir)
    entry = state.get(key) or {}
    raw_order = entry.get("order")
    order: Optional[List[str]] = (
        cast(List[str], raw_order) if isinstance(raw_order, list) else None
    )
    idx: int = int(entry.get("idx", 0))

    # Nếu danh sách file thay đổi, reset chu kỳ
    names = sorted(str(p.resolve()) for p in image_paths)
    if order is None or set(order) != set(names):
        order = names.copy()
        rng.shuffle(order)
        idx = 0

    if idx >= len(order):
        order = names.copy()
        rng.shuffle(order)
        idx = 0

    chosen_str = order[idx]
    idx += 1
    entry = {"order": order, "idx": idx}
    state[key] = entry
    save_state(output_dir, state)

    for p in image_paths:
        if str(p.resolve()) == chosen_str:
            return p
    return Path(chosen_str)
