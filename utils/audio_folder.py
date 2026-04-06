"""
Quét thư mục (đệ quy mọi thư mục con): danh sách .mp3 không trùng (resolve),
sắp xếp ổn định theo đường dẫn tương đối (không phân biệt hoa thường).
"""

from __future__ import annotations

from pathlib import Path


def safe_video_stem_from_audio(stem: str) -> str:
    """Tên file .mp4 an toàn trên Windows (ký tự cấm → _)."""
    bad = '\\/:*?"<>|'
    t = "".join(c if c not in bad and ord(c) >= 32 else "_" for c in stem)
    t = t.strip(". ")
    return t or "audio"


def list_unique_mp3_sorted(folder: Path) -> list[Path]:
    """
    Mọi file .mp3 trong thư mục gốc và mọi thư mục con (đệ quy), không trùng resolve(),
    thứ tự theo đường dẫn tương đối (casefold rồi bản gốc để ổn định).
    """
    if not folder.is_dir():
        return []
    root = folder.resolve()
    seen: set[str] = set()
    candidates: list[Path] = []
    for p in folder.rglob("*"):
        if not p.is_file() or p.suffix.lower() != ".mp3":
            continue
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        candidates.append(p)

    def sort_key(p: Path) -> tuple[str, str]:
        try:
            rel = p.resolve().relative_to(root)
        except ValueError:
            s = p.name
        else:
            s = rel.as_posix()
        return (s.lower(), s)

    candidates.sort(key=sort_key)
    return candidates
