"""
Điều phối pipeline render theo chế độ: ảnh+mp3, footage+mp3, hoặc đủ ba — không overlay/blur.
"""

from __future__ import annotations

import os
import random
import re
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Literal, Optional

from core.avatar_cycle import pick_avatar_path
from core.avatar_prep import (
    avatar_overlay_position,
    collect_avatar_images,
    prepare_avatar_overlay_png,
)
from core.ffmpeg_graph import _ffmpeg_path, build_filter_complex
from core.overlay_text import escape_path_for_filter, subscribe_pulses_enable_expr
from core.footage import build_footage_segments, collect_video_files, probe_all_durations
from core.slides import build_slide_segments
from utils.audio_folder import safe_video_stem_from_audio
from utils.cache import CacheFingerprint, compute_input_hash, find_cached_output, load_cache_map, save_cache_map
from utils.ffmpeg_util import check_ffmpeg_available, probe_duration_seconds, read_stderr_lines, run_ffmpeg
from utils.gpu import GPU_MODE_GPU_ONLY, build_encoder_attempts, encoder_cache_key

LogFn = Callable[[str], None]
ProgressFn = Callable[[float], None]

# Chế độ ghép nội dung
RENDER_SLIDES_MP3 = "slides_mp3"
RENDER_FOOTAGE_MP3 = "footage_mp3"
RENDER_FULL = "full"
RenderMode = Literal["slides_mp3", "footage_mp3", "full"]

ASPECT_16_9 = "16_9"
ASPECT_9_16 = "9_16"
AspectMode = Literal["16_9", "9_16"]

# (chất lượng, tỉ lệ) → (rộng, cao)
_RES: dict[tuple[str, str], tuple[int, int]] = {
    ("480p", ASPECT_16_9): (854, 480),
    ("480p", ASPECT_9_16): (480, 854),
    ("720p", ASPECT_16_9): (1280, 720),
    ("720p", ASPECT_9_16): (720, 1280),
    ("1080p", ASPECT_16_9): (1920, 1080),
    ("1080p", ASPECT_9_16): (1080, 1920),
    ("2K", ASPECT_16_9): (2560, 1440),
    ("2K", ASPECT_9_16): (1440, 2560),
    ("4K", ASPECT_16_9): (3840, 2160),
    ("4K", ASPECT_9_16): (2160, 3840),
}


def output_size(quality: str, aspect: str) -> tuple[int, int]:
    key = (quality, aspect)
    if key not in _RES:
        raise ValueError(f"Không hỗ trợ {quality} + {aspect}")
    return _RES[key]


def _default_subscribe_font() -> Optional[Path]:
    if sys.platform == "win32":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        for name in ("arialbd.ttf", "arial.ttf", "segoeuib.ttf"):
            p = Path(windir) / "Fonts" / name
            if p.is_file():
                return p
    elif sys.platform == "darwin":
        for p in (
            Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
            Path("/Library/Fonts/Arial Bold.ttf"),
        ):
            if p.is_file():
                return p
    else:
        for cand in (
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ):
            pp = Path(cand)
            if pp.is_file():
                return pp
    return None


def _build_subscribe_drawtext_inner(
    textfile: Path,
    enable_expr: str,
    fontfile: Optional[Path],
    fontsize: int,
    y_expr: str,
) -> str:
    tf_esc = escape_path_for_filter(str(textfile.resolve()))
    pieces = [
        f"textfile='{tf_esc}'",
        f"fontsize={fontsize}",
        "fontcolor=white@0.94",
        "borderw=4",
        "bordercolor=black@0.88",
        "shadowx=2",
        "shadowy=2",
        "shadowcolor=black@0.6",
        "x=(w-text_w)/2",
        f"y={y_expr}",
        "fix_bounds=1",
        f"enable='{enable_expr}'",
    ]
    if fontfile is not None and fontfile.is_file():
        ff_esc = escape_path_for_filter(str(fontfile.resolve()))
        pieces.insert(0, f"fontfile='{ff_esc}'")
    return ":".join(pieces)


@dataclass
class RenderSettings:
    """Cấu hình một lần render. File .mp4 luôn ghi cạnh file audio: {stem}.mp4."""

    render_mode: RenderMode
    aspect: AspectMode
    gpu_mode: str  # gpu_only | cpu_only | gpu_then_cpu
    audio_path: Path
    quality: str
    seed: Optional[int]
    threads_mode: str
    use_cache: bool
    # Tùy chế độ
    footage_dir: Optional[Path] = None
    images_dir: Optional[Path] = None
    avatar_position: str = "bottom_right"
    rembg: bool = False
    footage_no_repeat: bool = False
    no_consecutive_footage: bool = True
    avatar_no_repeat_cycle: bool = False
    # Chuyển cảnh giữa các đoạn footage/slide (ffmpeg xfade); None hoặc "" = không
    transition_effect: Optional[str] = None
    transition_duration_sec: float = 0.5
    # Logo PNG (nền trong suốt), góc trên; kích thước theo tỉ lệ chiều ngang output
    logo_path: Optional[Path] = None
    logo_enabled: bool = False
    logo_position: str = "top_left"  # top_left | top_right
    logo_width_frac: float = 0.14
    logo_margin_frac: float = 0.025
    # Gợi ý subscribe: overlay ảnh/clip động giữa khung + chữ tuỳ chọn (cùng lịch xung)
    subscribe_enabled: bool = False
    subscribe_animation_path: Optional[Path] = None
    subscribe_anim_width_frac: float = 0.32
    subscribe_text: str = ""
    subscribe_start_sec: float = 5.0
    subscribe_duration_sec: float = 4.0
    subscribe_show_count: int = 3
    subscribe_pause_sec: float = 15.0


@dataclass
class RenderResult:
    ok: bool
    output_path: Optional[Path]
    message: str
    from_cache: bool = False
    batch_ok: int = 0
    batch_total: int = 0
    batch_errors: Optional[list[str]] = None


def _parse_time_progress(line: str) -> Optional[float]:
    m = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", line)
    if not m:
        return None
    h, m_, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return h * 3600 + m_ * 60 + s


class RenderController:
    def __init__(self) -> None:
        self._proc = None
        self._lock = threading.Lock()
        self._stop_batch = False

    def attach(self, proc) -> None:
        with self._lock:
            self._proc = proc

    def reset_batch_cancel(self) -> None:
        with self._lock:
            self._stop_batch = False

    def stop_batch_requested(self) -> bool:
        with self._lock:
            return self._stop_batch

    def cancel(self) -> None:
        with self._lock:
            self._stop_batch = True
            p = self._proc
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=3)
            except Exception:
                p.kill()


def render_video(
    settings: RenderSettings,
    log: LogFn,
    progress: ProgressFn,
    controller: RenderController,
) -> RenderResult:
    ok_ff, msg_ff = check_ffmpeg_available()
    if not ok_ff:
        return RenderResult(False, None, msg_ff)

    audio = settings.audio_path.expanduser().resolve()
    out_dir = audio.parent

    if not audio.is_file():
        return RenderResult(False, None, f"Không tìm thấy file audio: {audio}")

    mode = settings.render_mode
    footage_dir = settings.footage_dir.expanduser().resolve() if settings.footage_dir else None
    images_dir = settings.images_dir.expanduser().resolve() if settings.images_dir else None

    if mode == RENDER_SLIDES_MP3:
        if images_dir is None or not images_dir.is_dir():
            return RenderResult(False, None, "Chế độ ảnh + MP3 cần thư mục ảnh.")
    elif mode == RENDER_FOOTAGE_MP3:
        if footage_dir is None or not footage_dir.is_dir():
            return RenderResult(False, None, "Chế độ footage + MP3 cần thư mục footage.")
    elif mode == RENDER_FULL:
        if footage_dir is None or not footage_dir.is_dir():
            return RenderResult(False, None, "Cần thư mục footage.")
        if images_dir is None or not images_dir.is_dir():
            return RenderResult(False, None, "Cần thư mục ảnh (avatar).")
    else:
        return RenderResult(False, None, f"Chế độ không hợp lệ: {mode}")

    try:
        out_w, out_h = output_size(settings.quality, settings.aspect)
    except ValueError as e:
        return RenderResult(False, None, str(e))

    gm = settings.gpu_mode.strip().lower()
    encoder_attempts = build_encoder_attempts(gm)
    if gm == GPU_MODE_GPU_ONLY and not encoder_attempts:
        return RenderResult(
            False,
            None,
            "Chế độ chỉ GPU: FFmpeg không có h264_nvenc hoặc driver không hỗ trợ.",
        )

    log(f"[encode] Thử lần lượt: {', '.join(n for n, _ in encoder_attempts)}")

    tm = settings.threads_mode.strip().lower()
    if tm == "auto":
        threads_arg = ["-threads", "0"]
    else:
        try:
            threads_arg = ["-threads", str(max(1, int(settings.threads_mode)))]
        except ValueError:
            threads_arg = ["-threads", "0"]

    logo_p_resolved: Optional[Path] = None
    if settings.logo_enabled and settings.logo_path is not None:
        lp = settings.logo_path.expanduser().resolve()
        if lp.is_file():
            logo_p_resolved = lp

    subscribe_anim_resolved: Optional[Path] = None
    if settings.subscribe_enabled and settings.subscribe_animation_path is not None:
        sp = settings.subscribe_animation_path.expanduser().resolve()
        if sp.is_file():
            subscribe_anim_resolved = sp

    if settings.subscribe_enabled:
        has_anim = subscribe_anim_resolved is not None
        has_txt = bool((settings.subscribe_text or "").strip())
        if not has_anim and not has_txt:
            return RenderResult(
                False,
                None,
                "Subscribe: chọn file ảnh/clip động (GIF/WebP/MP4…) hoặc nhập chữ kèm theo.",
            )

    fp = CacheFingerprint(
        render_mode=mode,
        aspect=settings.aspect,
        gpu_mode=gm,
        audio_path=str(audio),
        footage_dir=str(footage_dir) if footage_dir else "",
        images_dir=str(images_dir) if images_dir else "",
        quality=settings.quality,
        avatar_position=settings.avatar_position,
        rembg=settings.rembg,
        footage_no_repeat=settings.footage_no_repeat,
        no_consecutive_footage=settings.no_consecutive_footage,
        avatar_no_repeat_cycle=settings.avatar_no_repeat_cycle,
        seed=settings.seed,
        encoder=encoder_cache_key(gm),
        threads=settings.threads_mode,
        transition_effect=(settings.transition_effect or "") or "none",
        transition_duration=round(float(settings.transition_duration_sec), 4),
        logo_path=str(logo_p_resolved) if logo_p_resolved else "",
        logo_position=(settings.logo_position or "top_left").strip(),
        logo_width_frac=round(float(settings.logo_width_frac), 4),
        logo_margin_frac=round(float(settings.logo_margin_frac), 4),
        subscribe_enabled=bool(settings.subscribe_enabled),
        subscribe_animation_path=str(subscribe_anim_resolved) if subscribe_anim_resolved else "",
        subscribe_anim_width_frac=round(float(settings.subscribe_anim_width_frac), 4),
        subscribe_text=(settings.subscribe_text or "").strip(),
        subscribe_start_sec=round(float(settings.subscribe_start_sec), 4),
        subscribe_duration_sec=round(float(settings.subscribe_duration_sec), 4),
        subscribe_show_count=max(1, int(settings.subscribe_show_count)),
        subscribe_pause_sec=round(float(settings.subscribe_pause_sec), 4),
    )
    input_hash = compute_input_hash(
        audio,
        fp,
        footage_dir=footage_dir if mode != RENDER_SLIDES_MP3 else None,
        images_dir=images_dir if mode != RENDER_FOOTAGE_MP3 else None,
    )

    use_cache_effective = settings.use_cache and settings.seed is not None

    if use_cache_effective:
        cached = find_cached_output(out_dir, input_hash)
        if cached is not None:
            log(f"[cache] Trùng input — bỏ qua render: {cached}")
            progress(100.0)
            return RenderResult(
                True,
                cached,
                "Đã dùng cache (cùng input + cấu hình).",
                from_cache=True,
                batch_ok=1,
                batch_total=1,
            )
    elif settings.use_cache and settings.seed is None:
        log("[cache] Seed trống → không dùng cache.")

    try:
        audio_dur = probe_duration_seconds(audio)
    except Exception as e:
        return RenderResult(False, None, f"Không đọc được thời lượng audio: {e}")

    seed_val = settings.seed if settings.seed is not None else int(time.time() * 1000) % (2**31)
    rng = random.Random(seed_val)
    log(f"[random] seed={seed_val}")

    segment_media: Literal["video", "image"]
    segments: list[tuple[Path, float]]
    with_avatar = mode == RENDER_FULL
    avatar_png: Optional[Path] = None
    av_path: Optional[Path] = None

    if mode == RENDER_SLIDES_MP3:
        assert images_dir is not None
        imgs = collect_avatar_images(images_dir)
        if not imgs:
            return RenderResult(False, None, "Không có ảnh trong thư mục.")
        segment_media = "image"
        segments = build_slide_segments(
            imgs,
            audio_dur,
            rng,
            no_repeat_until_exhausted=settings.footage_no_repeat,
            no_two_consecutive_same=settings.no_consecutive_footage,
            log=log,
        )
    else:
        assert footage_dir is not None
        vids = collect_video_files(footage_dir)
        if not vids:
            return RenderResult(False, None, "Không có file video trong thư mục footage.")
        log("[footage] Đang đo thời lượng clip…")
        durations = probe_all_durations(vids, log)
        if not durations:
            return RenderResult(False, None, "Không probe được clip nào.")
        segments = build_footage_segments(
            vids,
            durations,
            audio_dur,
            rng,
            no_repeat_until_exhausted=settings.footage_no_repeat,
            no_two_consecutive_same=settings.no_consecutive_footage,
            log=log,
        )
        segment_media = "video"

    if len(segments) > 220:
        return RenderResult(
            False,
            None,
            "Quá nhiều đoạn ghép (>220). Audio ngắn hơn hoặc nguồn dài hơn.",
        )

    trans_raw = (settings.transition_effect or "").strip()
    td_req = max(0.0, float(settings.transition_duration_sec))
    nseg = len(segments)
    xfade_trans: Optional[str] = None
    xfade_td = 0.0
    if trans_raw and nseg > 1 and td_req > 0:
        segments_unscaled = list(segments)
        d0 = [float(d) for _, d in segments]
        sum0 = sum(d0)
        min_d = min(d0) if d0 else 0.0
        td = min(td_req, min_d * 0.38)
        td = max(0.05, td)
        if sum0 > 0 and (nseg - 1) * td < sum0 * 0.95:
            target_linear = audio_dur + (nseg - 1) * td
            sc = target_linear / sum0
            scaled = [(p, d * sc) for p, d in segments]
            min_after = min(d for _, d in scaled)
            if min_after >= td * 1.02:
                segments = scaled
                xfade_trans = trans_raw
                xfade_td = td
                log(
                    f"[transition] «{xfade_trans}», {xfade_td:.2f}s giữa {nseg} đoạn "
                    f"(scale timeline {sc:.4f}).\n"
                )
            else:
                segments = segments_unscaled
                log("[transition] Đoạn quá ngắn sau scale — dùng nối thẳng.\n")
        else:
            log("[transition] Bỏ qua chuyển cảnh (không đủ chỗ).\n")

    if with_avatar:
        assert images_dir is not None
        avatars = collect_avatar_images(images_dir)
        if not avatars:
            return RenderResult(False, None, "Không có ảnh avatar.")
        av_path = pick_avatar_path(
            avatars,
            rng,
            cycle_without_repeat=settings.avatar_no_repeat_cycle,
            output_dir=out_dir,
        )
        log(f"[avatar] Đã chọn: {av_path.name}")

    av_diameter = max(120, int(min(out_w, out_h) * 0.22))
    av_x, av_y = avatar_overlay_position(out_w, out_h, av_diameter, settings.avatar_position)

    final_name = f"{safe_video_stem_from_audio(audio.stem)}.mp4"
    output_path = out_dir / final_name
    out_dir.mkdir(parents=True, exist_ok=True)

    if settings.logo_enabled and settings.logo_path is not None and logo_p_resolved is None:
        log("[logo] Đã bật logo nhưng file không tồn tại — bỏ qua.\n")

    with tempfile.TemporaryDirectory(prefix="rv_render_") as tmp:
        tmp_path = Path(tmp)
        n = len(segments)
        avatar_png: Optional[Path] = None
        avatar_input_index: Optional[int] = None
        next_idx = n

        if with_avatar and av_path is not None:
            avatar_png = tmp_path / "avatar_overlay.png"
            log("[avatar] Đang xử lý avatar (mask tròn / rembg)…")
            prepare_avatar_overlay_png(
                av_path,
                avatar_png,
                av_diameter,
                use_rembg=settings.rembg,
                log=log,
            )
            avatar_input_index = next_idx
            next_idx += 1

        logo_input_index: Optional[int] = None
        logo_target_w = 0
        logo_xy = "24:24"
        if logo_p_resolved is not None:
            logo_input_index = next_idx
            next_idx += 1
            wfrac = float(settings.logo_width_frac)
            wfrac = max(0.04, min(0.55, wfrac))
            logo_target_w = max(32, int(out_w * wfrac))
            m = max(4, int(min(out_w, out_h) * float(settings.logo_margin_frac)))
            pos = (settings.logo_position or "top_left").strip().lower()
            if pos == "top_right":
                logo_xy = f"W-w-{m}:{m}"
            else:
                logo_xy = f"{m}:{m}"
            log(f"[logo] scale≈{logo_target_w}px ngang, vị trí {pos}.\n")

        subscribe_anim_input_index: Optional[int] = None
        subscribe_anim_tw = 0
        subscribe_en = "0"
        subscribe_inner: Optional[str] = None

        if settings.subscribe_enabled:
            subscribe_en = subscribe_pulses_enable_expr(
                float(settings.subscribe_start_sec),
                float(settings.subscribe_duration_sec),
                float(settings.subscribe_pause_sec),
                int(settings.subscribe_show_count),
            )
            if subscribe_anim_resolved is not None:
                subscribe_anim_input_index = next_idx
                next_idx += 1
                aw = float(settings.subscribe_anim_width_frac)
                aw = max(0.08, min(0.75, aw))
                subscribe_anim_tw = max(64, int(out_w * aw))
                log(
                    f"[subscribe] ảnh động giữa khung, rộng≈{subscribe_anim_tw}px, "
                    f"{int(settings.subscribe_show_count)} lần, "
                    f"mỗi lần {float(settings.subscribe_duration_sec):.1f}s, "
                    f"nghỉ {float(settings.subscribe_pause_sec):.1f}s, "
                    f"bắt đầu {float(settings.subscribe_start_sec):.1f}s.\n"
                )
            sub_txt = (settings.subscribe_text or "").strip()
            if sub_txt:
                stf = tmp_path / "subscribe_title.txt"
                stf.write_text(sub_txt + "\n", encoding="utf-8")
                sub_font = _default_subscribe_font()
                if sub_font is None:
                    log("[subscribe] Không tìm thấy font hệ thống — drawtext có thể lỗi.\n")
                fsize = max(14, min(88, int(out_h * 0.038)))
                if subscribe_anim_resolved is not None:
                    y_expr = "h*0.62"
                else:
                    y_expr = "(h-text_h)/2"
                subscribe_inner = _build_subscribe_drawtext_inner(
                    stf, subscribe_en, sub_font, fsize, y_expr=y_expr
                )
            if subscribe_anim_resolved is None and sub_txt:
                log(
                    f"[subscribe] Chỉ chữ (giữa khung), {int(settings.subscribe_show_count)} lần, "
                    f"{float(settings.subscribe_duration_sec):.1f}s/lần.\n"
                )

        fc = build_filter_complex(
            segments,
            out_w,
            out_h,
            fps=30,
            segment_media=segment_media,
            avatar_input_index=avatar_input_index,
            avatar_x=av_x,
            avatar_y=av_y,
            xfade_transition=xfade_trans,
            xfade_duration=xfade_td,
            logo_input_index=logo_input_index,
            logo_target_width=logo_target_w,
            logo_overlay_xy=logo_xy,
            subscribe_anim_input_index=subscribe_anim_input_index,
            subscribe_anim_target_width=subscribe_anim_tw,
            subscribe_enable_expr=subscribe_en,
            subscribe_drawtext_inner=subscribe_inner,
        )

        script_path = tmp_path / "filter.txt"
        script_path.write_text(fc, encoding="utf-8")

        aud_idx = next_idx

        base_tail = [
            "-filter_complex_script",
            str(script_path).replace("\\", "/"),
            "-map",
            "[outv]",
            "-map",
            f"{aud_idx}:a:0",
            "-t",
            f"{audio_dur:.6f}",
            *threads_arg,
        ]
        audio_tail = [
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            _ffmpeg_path(output_path),
        ]

        log(f"[ffmpeg] Encode → {output_path.name}\n")
        progress(0.0)

        def on_line(line: str) -> None:
            if line:
                if "error" in line.lower() or "warning" in line.lower() or line.startswith("frame="):
                    log(line)
            t = _parse_time_progress(line)
            if t is not None and audio_dur > 0:
                pct = min(99.0, max(0.0, (t / audio_dur) * 100.0))
                progress(pct)

        for enc_name, enc_args in encoder_attempts:
            log(f"[encode] Đang chạy: {enc_name}")
            args: list[str] = ["-y"]
            for p, _ in segments:
                if segment_media == "image":
                    args.extend(["-loop", "1", "-i", _ffmpeg_path(p)])
                else:
                    args.extend(["-i", _ffmpeg_path(p)])
            if avatar_png is not None:
                args.extend(["-stream_loop", "-1", "-i", _ffmpeg_path(avatar_png)])
            if logo_p_resolved is not None:
                args.extend(["-stream_loop", "-1", "-i", _ffmpeg_path(logo_p_resolved)])
            if subscribe_anim_resolved is not None:
                args.extend(["-stream_loop", "-1", "-i", _ffmpeg_path(subscribe_anim_resolved)])
            args.extend(["-i", _ffmpeg_path(audio)])
            args.extend([*base_tail, *enc_args, *audio_tail])

            try:
                proc = run_ffmpeg(args, cwd=tmp_path)
            except Exception as e:
                return RenderResult(False, None, f"Lỗi khởi chạy ffmpeg: {e}")

            controller.attach(proc)
            code = read_stderr_lines(proc, on_line)
            controller.attach(None)

            if code == 0:
                if (
                    enc_name == "libx264"
                    and encoder_attempts
                    and encoder_attempts[0][0] == "h264_nvenc"
                ):
                    log("[encode] NVENC lỗi trước đó — file dùng libx264 (CPU).\n")
                progress(100.0)
                break

            if output_path.is_file():
                try:
                    output_path.unlink()
                except OSError:
                    pass

            if enc_name == "h264_nvenc" and len(encoder_attempts) > 1:
                log("[encode] NVENC lỗi — thử libx264…\n")
                progress(0.0)
                continue

            if gm == GPU_MODE_GPU_ONLY:
                return RenderResult(
                    False,
                    None,
                    "Chỉ GPU: encode thất bại. Thử chế độ GPU→CPU hoặc chỉ CPU.",
                )

            return RenderResult(False, None, f"ffmpeg thoát mã {code}. Xem log.")

    if use_cache_effective:
        m = load_cache_map(out_dir)
        m[input_hash] = final_name
        save_cache_map(out_dir, m)

    return RenderResult(True, output_path, "Hoàn thành render.", batch_ok=1, batch_total=1)


def render_folder_mp3_batch(
    mp3_paths: list[Path],
    settings_template: RenderSettings,
    log: LogFn,
    progress: ProgressFn,
    controller: RenderController,
) -> RenderResult:
    """
    Mỗi file MP3 → {stem}.mp4 trong cùng thư mục với file đó, theo thứ tự danh sách.
    """
    n = len(mp3_paths)
    if n == 0:
        return RenderResult(False, None, "Thư mục không có file .mp3 hợp lệ.")

    controller.reset_batch_cancel()
    ok_paths: list[Path] = []
    errors: list[str] = []

    for i, ap in enumerate(mp3_paths):
        if controller.stop_batch_requested():
            errors.append("(Đã hủy — dừng batch.)")
            break

        log(f"\n[batch] ({i + 1}/{n}) {ap.name}\n")

        def sub_progress(p: float) -> None:
            progress(((i + p / 100.0) / n) * 100.0)

        s = replace(settings_template, audio_path=ap.expanduser().resolve())
        r = render_video(s, log, sub_progress, controller)
        if r.ok and r.output_path:
            ok_paths.append(r.output_path)
        else:
            errors.append(f"{ap.name}: {r.message}")

    total_done = len(ok_paths)
    last = ok_paths[-1] if ok_paths else None
    all_ok = total_done == n and not errors
    msg = f"Hoàn thành {n} video." if all_ok else f"Kết quả: {total_done}/{n} video."
    if errors:
        msg += "\n" + "\n".join(errors)
    return RenderResult(
        all_ok,
        last,
        msg,
        batch_ok=total_done,
        batch_total=n,
        batch_errors=errors if errors else None,
    )


def preview_last_frame_or_open(output: Path, log: LogFn) -> None:
    import os
    import sys

    if not output.is_file():
        log("[preview] Chưa có file.")
        return
    try:
        if sys.platform == "win32":
            os.startfile(output)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            import subprocess

            subprocess.Popen(["open", str(output)])
        else:
            import subprocess

            subprocess.Popen(["xdg-open", str(output)])
    except Exception as e:
        log(f"[preview] Không mở được file: {e}")
