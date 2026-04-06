"""
Chuẩn bị ảnh avatar: tùy chọn tách nền (rembg), mask tròn, viền glow nhẹ.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter

LogFn = Callable[[str], None]


def collect_avatar_images(folder: Path) -> list[Path]:
    """Liệt kê ảnh trong thư mục (một cấp)."""
    exts = {".png", ".jpg", ".jpeg", ".webp"}
    if not folder.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(folder.iterdir()):
        if p.is_file() and p.suffix.lower() in exts:
            out.append(p)
    return out


def _remove_background_rgba(im: Image.Image, log: LogFn) -> Image.Image:
    """Dùng rembg nếu có; nếu không thì trả về RGBA từ RGB."""
    try:
        from rembg import remove  # type: ignore

        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="PNG")
        buf.seek(0)
        out = remove(buf.read())
        return Image.open(io.BytesIO(out)).convert("RGBA")
    except ImportError:
        log("[avatar] rembg chưa cài — bỏ qua tách nền (cài: pip install rembg onnxruntime).")
        return im.convert("RGBA")
    except Exception as e:
        log(f"[avatar] rembg lỗi: {e} — dùng ảnh gốc.")
        return im.convert("RGBA")


def _fit_square(im: Image.Image, box: int) -> Image.Image:
    """Cắt vuông giữa và resize về box x box."""
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    crop = im.crop((left, top, left + side, top + side))
    return crop.resize((box, box), Image.Resampling.LANCZOS)


def prepare_avatar_overlay_png(
    src: Path,
    out_path: Path,
    diameter_px: int,
    *,
    use_rembg: bool,
    log: LogFn,
) -> None:
    """
    Tạo PNG RGBA: hình tròn + glow nhẹ quanh, kích thước canvas = diameter_px.
    """
    base = Image.open(src).convert("RGBA")
    if use_rembg:
        base = _remove_background_rgba(base, log)

    inner = max(32, int(diameter_px * 0.82))
    fitted = _fit_square(base, inner)

    canvas = Image.new("RGBA", (diameter_px, diameter_px), (0, 0, 0, 0))
    ox = (diameter_px - inner) // 2
    oy = (diameter_px - inner) // 2
    canvas.paste(fitted, (ox, oy), fitted)

    # Mask tròn
    mask = Image.new("L", (diameter_px, diameter_px), 0)
    draw = ImageDraw.Draw(mask)
    margin = int(diameter_px * 0.06)
    draw.ellipse((margin, margin, diameter_px - margin, diameter_px - margin), fill=255)

    rgba = Image.new("RGBA", (diameter_px, diameter_px), (0, 0, 0, 0))
    rgba.paste(canvas, (0, 0), mask)

    # Glow nhẹ: làm mờ bản sao rồi ghép phía dưới
    glow = rgba.filter(ImageFilter.GaussianBlur(radius=max(4, diameter_px // 40)))
    r, g, b, a = glow.split()
    a = a.point(lambda p: min(255, int(p * 0.32)))
    glow = Image.merge("RGBA", (r, g, b, a))
    final = Image.alpha_composite(glow, rgba)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    final.save(out_path, format="PNG")


def avatar_overlay_position(
    main_w: int,
    main_h: int,
    av_size: int,
    position: str,
    margin: int = 24,
) -> tuple[int, int]:
    """
    Vị trí góc trái trên của overlay (ffmpeg overlay=x:y).
    position: 'bottom_left' | 'bottom_right' | 'center'
    """
    if position == "bottom_left":
        return margin, main_h - av_size - margin
    if position == "bottom_right":
        return main_w - av_size - margin, main_h - av_size - margin
    if position == "center":
        return (main_w - av_size) // 2, (main_h - av_size) // 2
    return margin, main_h - av_size - margin
