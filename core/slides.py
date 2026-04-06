"""
Ghép ảnh tĩnh thành timeline video (thời lượng ngẫu nhiên mỗi ảnh) cho tới khi đủ audio.
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Callable, List, Tuple

LogFn = Callable[[str], None]


def build_slide_segments(
    paths: List[Path],
    target_duration_sec: float,
    rng: random.Random,
    *,
    no_repeat_until_exhausted: bool,
    no_two_consecutive_same: bool,
    log: LogFn,
) -> List[Tuple[Path, float]]:
    """
    (ảnh, số giây hiển thị). Mỗi lần chọn ảnh, thời lượng đoạn ~2–5s (cắt đoạn cuối cho khớp).
    """
    usable = list(paths)
    if not usable:
        raise ValueError("Không có ảnh hợp lệ.")

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

    def pick_chunk_duration() -> float:
        if remaining <= 0.01:
            return remaining
        if remaining <= 2.5:
            return remaining
        lo, hi = 2.0, min(5.0, remaining)
        return float(rng.uniform(lo, hi))

    safety = 0
    max_iters = max(10000, len(usable) * 500)
    while remaining > 1e-3 and safety < max_iters:
        safety += 1

        if no_repeat_until_exhausted:
            if deck_idx >= len(deck):
                refill_deck()
            if no_two_consecutive_same and last is not None and len(usable) > 1:
                attempts = 0
                while deck[deck_idx] == last and attempts < len(deck):
                    deck_idx += 1
                    if deck_idx >= len(deck):
                        refill_deck()
                    attempts += 1
                if deck[deck_idx] == last:
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

        chunk = pick_chunk_duration()
        take = min(chunk, remaining)
        segments.append((choice, take))
        last = choice
        remaining -= take

    if remaining > 0.05:
        log(f"[slides] Cảnh báo: thiếu ~{remaining:.2f}s (lỗi làm tròn?).")

    total = sum(s for _, s in segments)
    log(f"[slides] {len(segments)} ảnh-segment, tổng ~{total:.2f}s (mục tiêu {target_duration_sec:.2f}s).")
    return segments
