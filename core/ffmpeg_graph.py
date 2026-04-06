"""
Filter graph: preprocess → concat/xfade → avatar → logo → subscribe (overlay ảnh động + chữ tuỳ chọn).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

SegmentMedia = Literal["video", "image"]


def _ffmpeg_path(p: Path) -> str:
    s = str(p.resolve())
    return s.replace("\\", "/")


def build_filter_complex(
    segments: list[tuple[Path, float]],
    out_w: int,
    out_h: int,
    fps: int,
    *,
    segment_media: SegmentMedia,
    avatar_input_index: Optional[int],
    avatar_x: int = 0,
    avatar_y: int = 0,
    xfade_transition: Optional[str] = None,
    xfade_duration: float = 0.0,
    logo_input_index: Optional[int] = None,
    logo_target_width: int = 0,
    logo_overlay_xy: str = "24:24",
    subscribe_anim_input_index: Optional[int] = None,
    subscribe_anim_target_width: int = 0,
    subscribe_enable_expr: str = "0",
    subscribe_drawtext_inner: Optional[str] = None,
) -> str:
    """
    xfade_transition: tên hiệu ứng ffmpeg xfade (fade, wipeleft, …); None hoặc rỗng → concat đơn.
    xfade_duration: thời lượng chuyển cảnh (giây), đồng nhất mọi cặp; segments đã scale tổng thời lượng trước.
    """
    n = len(segments)
    if n == 0:
        raise ValueError("Không có đoạn nguồn.")

    durs = [float(d) for _, d in segments]
    parts: list[str] = []

    for i, (_, dur) in enumerate(segments):
        if segment_media == "video":
            parts.append(
                f"[{i}:v]trim=start=0:duration={dur:.6f},setpts=PTS-STARTPTS,"
                f"fps={fps},scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,setsar=1[v{i}]"
            )
        else:
            parts.append(
                f"[{i}:v]fps={fps},scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
                f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,"
                f"trim=start=0:duration={dur:.6f},setpts=PTS-STARTPTS,setsar=1[v{i}]"
            )

    trans = (xfade_transition or "").strip()
    use_xfade = bool(trans) and n >= 2 and xfade_duration > 1e-6
    td = float(xfade_duration)

    if use_xfade:
        # Cùng một td mọi cặp — khớp với bước scale timeline trong renderer (tổng sau xfade = audio).
        cur = "[v0]"
        acc = durs[0]
        for i in range(1, n):
            off = acc - td
            off = max(0.0, off)
            out_lab = f"[xa{i}]"
            # Không dùng format= trong xfade — nhiều bản FFmpeg không có option này; ép yuv420p sau chuỗi.
            parts.append(
                f"{cur}[v{i}]xfade=transition={trans}:duration={td:.4f}:offset={off:.4f}{out_lab}"
            )
            cur = out_lab
            acc += durs[i] - td
        parts.append(f"{cur}format=yuv420p,setsar=1[base1]")
    else:
        concat_in = "".join(f"[v{i}]" for i in range(n))
        parts.append(f"{concat_in}concat=n={n}:v=1:a=0[base0]")
        parts.append("[base0]format=yuv420p[base1]")

    cur = "[base1]"
    if avatar_input_index is not None:
        parts.append(
            f"{cur}[{avatar_input_index}:v]overlay={avatar_x}:{avatar_y}:format=yuv420[ov_a]"
        )
        parts.append("[ov_a]format=yuv420p,setsar=1[vin]")
        cur = "[vin]"

    if logo_input_index is not None and logo_target_width > 0:
        lw = int(logo_target_width)
        parts.append(f"[{logo_input_index}:v]scale={lw}:-2:flags=lanczos[lg]")
        parts.append(f"{cur}[lg]overlay={logo_overlay_xy}:format=yuv420[ov_lg]")
        parts.append("[ov_lg]format=yuv420p,setsar=1[vin2]")
        cur = "[vin2]"

    if subscribe_anim_input_index is not None and subscribe_anim_target_width > 0:
        sw = int(subscribe_anim_target_width)
        ee = subscribe_enable_expr
        parts.append(
            f"[{subscribe_anim_input_index}:v]scale={sw}:-2:flags=lanczos,"
            f"fps={fps},setsar=1[sub_sc]"
        )
        parts.append(
            f"{cur}[sub_sc]overlay=(W-w)/2:(H-h)/2:enable='{ee}':format=yuv420[sub_ov]"
        )
        parts.append("[sub_ov]format=yuv420p,setsar=1[vin3]")
        cur = "[vin3]"

    if subscribe_drawtext_inner:
        parts.append(f"{cur}drawtext={subscribe_drawtext_inner}[sub1]")
        parts.append("[sub1]format=yuv420p,setsar=1[outv]")
    else:
        parts.append(f"{cur}format=yuv420p,setsar=1[outv]")

    return ";".join(parts)
