"""
Logic chọn và ghép footage ngẫu nhiên theo thời lượng mục tiêu.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Callable, List, Tuple

from utils.ffmpeg_util import probe_duration_seconds


def collect_video_files(folder: Path) -> List[Path]:
    """Liệt kê file video hợp lệ trong thư mục (không đệ quy — chỉ cấp một)."""
    exts = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
    if not folder.is_dir():
        return []
    out: List[Path] = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in exts:
            out.append(p)
    return out


def probe_all_durations(
    paths: List[Path],
    log: Callable[[str], None],
) -> dict[Path, float]:
    """Đo duration từng clip (có thể chậm nếu nhiều file)."""
    d: dict[Path, float] = {}
    for i, p in enumerate(paths):
        try:
            d[p] = probe_duration_seconds(p)
        except Exception as e:
            log(f"[footage] Bỏ qua (lỗi probe): {p.name} — {e}")
        if (i + 1) % 10 == 0:
            log(f"[footage] Đã probe {i + 1}/{len(paths)} clip…")
    return d


def build_footage_segments(
    paths: List[Path],
    durations: dict[Path, float],
    target_duration_sec: float,
    rng: random.Random,
    *,
    no_repeat_until_exhausted: bool,
    no_two_consecutive_same: bool,
    log: Callable[[str], None],
) -> List[Tuple[Path, float]]:
    """
    Tạo danh sách (path, seconds_to_use) sao cho tổng ≈ target_duration_sec.

    - no_repeat_until_exhausted: xáo trộn toàn bộ, lấy lần lượt; hết bộ thì xáo lại.
    - no_two_consecutive_same: không chọn cùng file hai lần liên tiếp (khi có >1 file).
    """
    usable = [p for p in paths if p in durations and durations[p] > 0.01]
    if not usable:
        raise ValueError("Không có clip video hợp lệ sau khi probe.")

    segments: List[Tuple[Path, float]] = []
    remaining = float(target_duration_sec)
    last: Path | None = None

    deck: List[Path] = []
    deck_idx = 0

    def refill_deck() -> None:
        nonlocal deck, deck_idx
        deck = usable.copy()
        rng.shuffle(deck)
        deck_idx = 0

    if no_repeat_until_exhausted:
        refill_deck()

    safety = 0
    max_iters = max(10000, len(usable) * 500)
    while remaining > 1e-3 and safety < max_iters:
        safety += 1

        if no_repeat_until_exhausted:
            if deck_idx >= len(deck):
                refill_deck()
            # Tránh trùng liên tiếp nếu bật và có thể
            if no_two_consecutive_same and last is not None and len(usable) > 1:
                attempts = 0
                while deck[deck_idx] == last and attempts < len(deck):
                    deck_idx += 1
                    if deck_idx >= len(deck):
                        refill_deck()
                    attempts += 1
                if deck[deck_idx] == last:
                    # Hoán vị với phần tử kế tiếp khác last
                    for j in range(deck_idx + 1, len(deck)):
                        if deck[j] != last:
                            deck[deck_idx], deck[j] = deck[j], deck[deck_idx]
                            break
            choice = deck[deck_idx]
            deck_idx += 1
        else:
            candidates = usable
            if no_two_consecutive_same and last is not None and len(usable) > 1:
                others = [p for p in usable if p != last]
                if others:
                    candidates = others
            choice = rng.choice(candidates)

        dur_full = durations[choice]
        take = min(dur_full, remaining)
        segments.append((choice, take))
        last = choice
        remaining -= take

    if remaining > 0.05:
        log(f"[footage] Cảnh báo: thiếu ~{remaining:.2f}s footage (clip quá ngắn?).")

    total = sum(s for _, s in segments)
    log(f"[footage] {len(segments)} đoạn, tổng ~{total:.2f}s (mục tiêu {target_duration_sec:.2f}s).")
    return segments
