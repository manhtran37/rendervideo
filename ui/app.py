"""
Cửa sổ chính: chế độ render, audio file/thư mục, tỉ lệ 16:9 / 9:16, GPU/CPU.
"""

from __future__ import annotations

import queue
import threading
import tkinter.messagebox as messagebox
from pathlib import Path
from typing import List, Optional

import customtkinter as ctk

from core.renderer import (
    ASPECT_16_9,
    ASPECT_9_16,
    RENDER_FOOTAGE_MP3,
    RENDER_FULL,
    RENDER_SLIDES_MP3,
    RenderController,
    RenderResult,
    RenderSettings,
    preview_last_frame_or_open,
    render_folder_mp3_batch,
    render_video,
)
from utils.audio_folder import list_unique_mp3_sorted
from utils.gpu import GPU_MODE_CPU_ONLY, GPU_MODE_GPU_ONLY, GPU_MODE_GPU_THEN_CPU

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

RENDER_MODE_LABELS = {
    "slides_mp3": "Ảnh + MP3 (slideshow)",
    "footage_mp3": "Footage + MP3",
    "full": "Ảnh + Footage + MP3 (avatar trên video)",
}

# (nhãn UI, giá trị ffmpeg xfade — None = concat)
TRANSITION_CHOICES: list[tuple[str, Optional[str]]] = [
    ("Không (nối thẳng)", None),
    ("fade", "fade"),
    ("dissolve", "dissolve"),
    ("wipeleft", "wipeleft"),
    ("wiperight", "wiperight"),
    ("slideleft", "slideleft"),
    ("slideright", "slideright"),
    ("circleopen", "circleopen"),
    ("radial", "radial"),
    ("diagtl", "diagtl"),
]


class RenderVideoApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Render Video — FFmpeg")
        self.geometry("1140x980")
        self.minsize(980, 820)

        self._audio_path: Optional[Path] = None
        self._audio_folder: Optional[Path] = None
        self._audio_files_list: List[Path] = []
        self._footage_dir: Optional[Path] = None
        self._images_dir: Optional[Path] = None
        self._last_output: Optional[Path] = None
        self._logo_path_ui: Optional[Path] = None
        self._subscribe_anim_path_ui: Optional[Path] = None

        self._log_queue: queue.Queue = queue.Queue()
        self._render_controller = RenderController()
        self._busy = False

        self._build_ui()
        self._sync_mode_ui()
        self._poll_queue()
        self._try_hook_drag_drop()

    def _render_mode_key(self) -> str:
        for k, lab in RENDER_MODE_LABELS.items():
            if lab == self._cb_render_mode.get():
                return k
        return "footage_mp3"

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(self, corner_radius=14, fg_color=("gray85", "gray17"))
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        ctk.CTkLabel(
            head,
            text="Render video tự động (FFmpeg)",
            font=ctk.CTkFont(size=22, weight="bold"),
        ).pack(anchor="w", padx=18, pady=(14, 4))
        ctk.CTkLabel(
            head,
            text="Chọn chế độ: slideshow ảnh, chỉ footage, hoặc footage + avatar — 16:9 / 9:16",
            font=ctk.CTkFont(size=13),
            text_color="gray70",
        ).pack(anchor="w", padx=18, pady=(0, 14))

        body = ctk.CTkFrame(self, corner_radius=14, fg_color=("gray90", "gray20"))
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
        body.grid_columnconfigure(0, weight=2)
        body.grid_columnconfigure(1, weight=3)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkScrollableFrame(body, corner_radius=12, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)

        ctk.CTkLabel(left, text="Chế độ render", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=4)
        self._cb_render_mode = ctk.CTkComboBox(
            left,
            values=list(RENDER_MODE_LABELS.values()),
            width=320,
            command=lambda _: self._sync_mode_ui(),
        )
        self._cb_render_mode.set(RENDER_MODE_LABELS["footage_mp3"])
        self._cb_render_mode.pack(anchor="w", padx=4, pady=(4, 12))

        ctk.CTkLabel(left, text="Tỉ lệ khung hình", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=4)
        self._var_aspect = ctk.StringVar(value=ASPECT_16_9)
        af = ctk.CTkFrame(left, fg_color="transparent")
        af.pack(anchor="w", padx=4, pady=4)
        ctk.CTkRadioButton(
            af, text="16:9 (ngang)", variable=self._var_aspect, value=ASPECT_16_9
        ).pack(side="left", padx=(0, 16))
        ctk.CTkRadioButton(af, text="9:16 (dọc TikTok/Shorts)", variable=self._var_aspect, value=ASPECT_9_16).pack(
            side="left"
        )

        ctk.CTkLabel(left, text="Encode (GPU / CPU)", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=4, pady=(12, 2)
        )
        self._var_gpu = ctk.StringVar(value=GPU_MODE_GPU_THEN_CPU)
        gf = ctk.CTkFrame(left, fg_color="transparent")
        gf.pack(anchor="w", padx=4, pady=4)
        ctk.CTkRadioButton(
            gf,
            text="GPU (NVENC)",
            variable=self._var_gpu,
            value=GPU_MODE_GPU_ONLY,
        ).pack(anchor="w")
        ctk.CTkRadioButton(
            gf,
            text="CPU (libx264)",
            variable=self._var_gpu,
            value=GPU_MODE_CPU_ONLY,
        ).pack(anchor="w")
        ctk.CTkRadioButton(
            gf,
            text="GPU trước — lỗi thì CPU",
            variable=self._var_gpu,
            value=GPU_MODE_GPU_THEN_CPU,
        ).pack(anchor="w")

        # --- Audio ---
        ctk.CTkLabel(left, text="Audio MP3", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=4, pady=(14, 4)
        )
        self._var_audio_src = ctk.StringVar(value="file")
        aud_mode = ctk.CTkFrame(left, fg_color="transparent")
        aud_mode.pack(anchor="w", padx=4, pady=2)
        ctk.CTkRadioButton(
            aud_mode,
            text="Chọn một file",
            variable=self._var_audio_src,
            value="file",
            command=self._on_audio_src_change,
        ).pack(side="left", padx=(0, 12))
        ctk.CTkRadioButton(
            aud_mode,
            text="Thư mục: mỗi .mp3 → 1 video .mp4 cùng tên",
            variable=self._var_audio_src,
            value="folder",
            command=self._on_audio_src_change,
        ).pack(side="left")

        self._frm_audio_file = ctk.CTkFrame(left, fg_color="transparent")
        self._row_file_picker(self._frm_audio_file, "File audio", self._pick_audio, self._lbl_audio)
        self._frm_audio_file.pack(fill="x", pady=4)

        self._frm_audio_folder = ctk.CTkFrame(left, fg_color="transparent")
        row = ctk.CTkFrame(self._frm_audio_folder, fg_color="transparent")
        row.pack(fill="x", pady=4)
        ctk.CTkLabel(row, text="Thư mục MP3", font=ctk.CTkFont(weight="bold")).pack(anchor="w")
        r2 = ctk.CTkFrame(row, fg_color="transparent")
        r2.pack(fill="x", pady=4)
        self._lbl_audio_dir = ctk.CTkLabel(r2, text="Chưa chọn thư mục", anchor="w", text_color="gray65")
        self._lbl_audio_dir.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(r2, text="Chọn thư mục…", width=120, command=self._pick_audio_folder).pack(side="right")
        self._lbl_mp3_batch = ctk.CTkLabel(
            row,
            text="0 file .mp3 — quét cả thư mục con, A→Z theo đường dẫn, không trùng path",
            anchor="w",
            text_color="gray60",
            wraplength=400,
        )
        self._lbl_mp3_batch.pack(anchor="w", pady=(8, 4))

        # Khối nguồn động (luôn nằm trên «output») — con của _frm_dynamic
        self._frm_dynamic = ctk.CTkFrame(left, fg_color="transparent")
        self._frm_dynamic.pack(fill="x", pady=4)

        self._frm_footage = ctk.CTkFrame(self._frm_dynamic, fg_color="transparent")
        self._row_folder_picker(self._frm_footage, "Thư mục footage (video)", self._pick_footage, self._lbl_footage)

        self._frm_images = ctk.CTkFrame(self._frm_dynamic, fg_color="transparent")
        self._lbl_images_title = ctk.CTkLabel(
            self._frm_images, text="Thư mục ảnh", font=ctk.CTkFont(weight="bold")
        )
        self._lbl_images_title.pack(anchor="w")
        self._row_folder_picker_images(self._frm_images, self._pick_images, self._lbl_images)

        self._frm_avatar_opts = ctk.CTkFrame(self._frm_dynamic, fg_color="transparent")
        ctk.CTkLabel(self._frm_avatar_opts, text="Avatar (chế độ đủ 3 nguồn)", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=4, pady=(8, 4)
        )
        ctk.CTkLabel(self._frm_avatar_opts, text="Vị trí avatar").pack(anchor="w", padx=4)
        self._var_pos = ctk.StringVar(value="bottom_right")
        ctk.CTkComboBox(
            self._frm_avatar_opts,
            values=["bottom_left", "bottom_right", "center"],
            variable=self._var_pos,
            width=280,
        ).pack(anchor="w", padx=4, pady=4)
        self._chk_rembg = ctk.CTkCheckBox(self._frm_avatar_opts, text="Tách nền avatar (rembg)")
        self._chk_rembg.pack(anchor="w", padx=4, pady=4)
        self._chk_avatar_cycle = ctk.CTkCheckBox(
            self._frm_avatar_opts, text="Avatar: không lặp cho đến khi dùng hết"
        )
        self._chk_avatar_cycle.pack(anchor="w", padx=4, pady=4)

        ctk.CTkLabel(
            left,
            text="Output: mỗi file .mp4 ghi cùng thư mục với file audio tương ứng (tên trùng stem).",
            text_color="gray60",
            wraplength=400,
            justify="left",
        ).pack(anchor="w", padx=4, pady=(8, 4))

        ctk.CTkLabel(left, text="Logo & gợi ý đăng ký", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=4, pady=(12, 4)
        )
        self._frm_brand = ctk.CTkFrame(left, fg_color="transparent")
        self._frm_brand.pack(fill="x", padx=4, pady=4)
        self._chk_logo = ctk.CTkCheckBox(
            self._frm_brand, text="Thêm logo đã tách nền (PNG / WebP trong suốt)"
        )
        self._chk_logo.pack(anchor="w", pady=(0, 4))
        rlogo = ctk.CTkFrame(self._frm_brand, fg_color="transparent")
        rlogo.pack(fill="x", pady=2)
        self._lbl_logo_path = ctk.CTkLabel(
            rlogo, text="Chưa chọn file logo", anchor="w", text_color="gray65", wraplength=360
        )
        self._lbl_logo_path.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(rlogo, text="Chọn logo…", width=110, command=self._pick_logo).pack(side="right")
        ctk.CTkLabel(self._frm_brand, text="Vị trí logo (cố định góc trên)").pack(anchor="w", pady=(6, 0))
        self._cb_logo_pos = ctk.CTkComboBox(
            self._frm_brand,
            values=["Góc trên trái", "Góc trên phải"],
            width=280,
        )
        self._cb_logo_pos.set("Góc trên trái")
        self._cb_logo_pos.pack(anchor="w", pady=4)
        ctk.CTkLabel(self._frm_brand, text="Độ rộng logo so với chiều ngang video (%)").pack(
            anchor="w", pady=(4, 0)
        )
        self._slider_logo_pct = ctk.CTkSlider(self._frm_brand, from_=5, to=45, number_of_steps=40)
        self._slider_logo_pct.set(14)
        self._slider_logo_pct.pack(fill="x", pady=4)
        self._lbl_logo_pct = ctk.CTkLabel(self._frm_brand, text="14%")
        self._lbl_logo_pct.pack(anchor="w")
        self._slider_logo_pct.configure(
            command=lambda v: self._lbl_logo_pct.configure(text=f"{float(v):.0f}%")
        )

        self._chk_subscribe = ctk.CTkCheckBox(
            self._frm_brand,
            text="Gợi ý subscribe: ảnh/clip động giữa khung (GIF, WebP, MP4…)",
        )
        self._chk_subscribe.pack(anchor="w", pady=(10, 4))
        rsub = ctk.CTkFrame(self._frm_brand, fg_color="transparent")
        rsub.pack(fill="x", pady=2)
        self._lbl_subscribe_anim = ctk.CTkLabel(
            rsub, text="Chưa chọn ảnh động", anchor="w", text_color="gray65", wraplength=340
        )
        self._lbl_subscribe_anim.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(rsub, text="Chọn ảnh động…", width=130, command=self._pick_subscribe_anim).pack(
            side="right"
        )
        ctk.CTkLabel(self._frm_brand, text="Rộng ảnh động so với chiều ngang video (%)").pack(
            anchor="w", pady=(6, 0)
        )
        self._slider_subscribe_pct = ctk.CTkSlider(self._frm_brand, from_=10, to=65, number_of_steps=55)
        self._slider_subscribe_pct.set(32)
        self._slider_subscribe_pct.pack(fill="x", pady=4)
        self._lbl_subscribe_pct = ctk.CTkLabel(self._frm_brand, text="32%")
        self._lbl_subscribe_pct.pack(anchor="w")
        self._slider_subscribe_pct.configure(
            command=lambda v: self._lbl_subscribe_pct.configure(text=f"{float(v):.0f}%")
        )
        for lab, attr, default in (
            ("Lần đầu xuất hiện sau (giây, từ đầu video)", "_ent_sub_start", "5"),
            ("Số lần xuất hiện", "_ent_sub_count", "3"),
            ("Mỗi lần hiển thị (giây)", "_ent_sub_show", "4"),
            ("Nghỉ sau mỗi lần, trước lần kế (giây)", "_ent_sub_pause", "15"),
        ):
            row_s = ctk.CTkFrame(self._frm_brand, fg_color="transparent")
            row_s.pack(fill="x", pady=2)
            ctk.CTkLabel(row_s, text=lab, anchor="w", wraplength=360).pack(anchor="w")
            e = ctk.CTkEntry(row_s, width=90)
            e.insert(0, default)
            e.pack(anchor="w", pady=(2, 0))
            setattr(self, attr, e)
        ctk.CTkLabel(self._frm_brand, text="Chữ kèm theo (tuỳ chọn; dưới ảnh hoặc giữa nếu không có ảnh)").pack(
            anchor="w", pady=(8, 0)
        )
        self._ent_subscribe_text = ctk.CTkEntry(self._frm_brand, width=300)
        self._ent_subscribe_text.pack(anchor="w", pady=4)

        ctk.CTkLabel(left, text="Chuyển cảnh giữa các đoạn", font=ctk.CTkFont(weight="bold")).pack(
            anchor="w", padx=4, pady=(12, 2)
        )
        self._cb_transition = ctk.CTkComboBox(
            left,
            values=[t[0] for t in TRANSITION_CHOICES],
            width=320,
        )
        self._cb_transition.set(TRANSITION_CHOICES[0][0])
        self._cb_transition.pack(anchor="w", padx=4, pady=4)
        ctk.CTkLabel(left, text="Độ dài mỗi lần chuyển (giây)").pack(anchor="w", padx=4, pady=(6, 2))
        self._slider_trans = ctk.CTkSlider(left, from_=0.2, to=1.5, number_of_steps=26)
        self._slider_trans.set(0.5)
        self._slider_trans.pack(fill="x", padx=4, pady=4)
        self._lbl_trans_dur = ctk.CTkLabel(left, text="0.50 s")
        self._lbl_trans_dur.pack(anchor="w", padx=4)
        self._slider_trans.configure(
            command=lambda v: self._lbl_trans_dur.configure(text=f"{float(v):.2f} s")
        )

        ctk.CTkLabel(left, text="Chất lượng", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=4, pady=(12, 2))
        self._var_quality = ctk.StringVar(value="1080p")
        ctk.CTkComboBox(
            left,
            values=["480p", "720p", "1080p", "2K", "4K"],
            variable=self._var_quality,
            width=280,
        ).pack(anchor="w", padx=4, pady=4)

        ctk.CTkLabel(left, text="Số luồng FFmpeg").pack(anchor="w", padx=4, pady=(10, 2))
        self._var_threads = ctk.StringVar(value="auto")
        ctk.CTkComboBox(
            left,
            values=["auto", "2", "4", "6", "8", "12", "16"],
            variable=self._var_threads,
            width=280,
        ).pack(anchor="w", padx=4, pady=4)

        self._chk_footage_unique = ctk.CTkCheckBox(
            left, text="Footage/ảnh: không trùng lặp cho đến khi dùng hết"
        )
        self._chk_footage_unique.pack(anchor="w", padx=4, pady=(8, 4))
        self._chk_no_consecutive = ctk.CTkCheckBox(left, text="Không lặp 2 đoạn liên tiếp (cùng file)")
        self._chk_no_consecutive.select()
        self._chk_no_consecutive.pack(anchor="w", padx=4, pady=4)

        self._chk_cache = ctk.CTkCheckBox(left, text="Cache (cần Seed cố định)")
        self._chk_cache.pack(anchor="w", padx=4, pady=4)

        ctk.CTkLabel(left, text="Random seed (cache / tái lập)").pack(anchor="w", padx=4, pady=(12, 2))
        self._ent_seed = ctk.CTkEntry(left, placeholder_text="Để trống = ngẫu nhiên", width=280)
        self._ent_seed.pack(anchor="w", padx=4, pady=4)

        btn_row = ctk.CTkFrame(left, fg_color="transparent")
        btn_row.pack(fill="x", pady=(16, 8))
        self._btn_render = ctk.CTkButton(
            btn_row,
            text="Render",
            height=44,
            corner_radius=12,
            font=ctk.CTkFont(size=16, weight="bold"),
            command=self._on_render,
        )
        self._btn_render.pack(side="left", padx=(0, 8), expand=True, fill="x")
        self._btn_cancel = ctk.CTkButton(
            btn_row,
            text="Hủy",
            height=44,
            fg_color=("gray70", "gray35"),
            command=self._on_cancel,
        )
        self._btn_cancel.pack(side="left", padx=(0, 8), expand=True, fill="x")
        self._btn_preview = ctk.CTkButton(btn_row, text="Mở preview", height=44, command=self._on_preview)
        self._btn_preview.pack(side="left", expand=True, fill="x")

        right = ctk.CTkFrame(body, corner_radius=12, fg_color=("gray88", "gray22"))
        right.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(right, text="Tiến độ", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=14, pady=(12, 4)
        )
        self._progress = ctk.CTkProgressBar(right, corner_radius=8, height=16)
        self._progress.grid(row=0, column=0, sticky="ew", padx=(14, 6), pady=(36, 12))
        self._progress.set(0)
        self._lbl_progress_pct = ctk.CTkLabel(
            right, text="0%", width=44, font=ctk.CTkFont(size=14, weight="bold")
        )
        self._lbl_progress_pct.grid(row=0, column=1, sticky="e", padx=(0, 14), pady=(36, 12))

        ctk.CTkLabel(right, text="Nhật ký", font=ctk.CTkFont(size=15, weight="bold")).grid(
            row=1, column=0, columnspan=2, sticky="nw", padx=14, pady=(4, 4)
        )
        self._log = ctk.CTkTextbox(right, corner_radius=10, font=ctk.CTkFont(family="Consolas", size=12))
        self._log.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=14, pady=(32, 14))
        self._append_log("Sẵn sàng. Cần FFmpeg trong PATH.\n")

        ctk.CTkLabel(
            self,
            text="Kéo thả file audio lên cửa sổ (windnd) — chỉ áp khi chế độ «Chọn một file».",
            text_color="gray55",
            font=ctk.CTkFont(size=11),
        ).grid(row=2, column=0, pady=(0, 10))

        self._on_audio_src_change()

    def _sync_mode_ui(self) -> None:
        self._frm_footage.pack_forget()
        self._frm_images.pack_forget()
        self._frm_avatar_opts.pack_forget()
        m = self._render_mode_key()
        if m == RENDER_SLIDES_MP3:
            self._lbl_images_title.configure(text="Thư mục ảnh (slideshow)")
            self._frm_images.pack(fill="x", pady=4)
        elif m == RENDER_FOOTAGE_MP3:
            self._frm_footage.pack(fill="x", pady=4)
        else:
            self._lbl_images_title.configure(text="Thư mục ảnh (avatar)")
            self._frm_footage.pack(fill="x", pady=4)
            self._frm_images.pack(fill="x", pady=4)
            self._frm_avatar_opts.pack(fill="x", pady=8)

    def _row_file_picker(self, parent, title: str, cmd, label_factory) -> None:
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", pady=6)
        ctk.CTkLabel(f, text=title, font=ctk.CTkFont(weight="bold")).pack(anchor="w")
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x", pady=4)
        lbl = ctk.CTkLabel(row, text="Chưa chọn", anchor="w", text_color="gray65")
        lbl.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="Chọn…", width=88, command=cmd).pack(side="right")
        label_factory(lbl)

    def _row_folder_picker(self, parent, title: str, cmd, label_factory) -> None:
        self._row_file_picker(parent, title, cmd, label_factory)

    def _row_folder_picker_images(self, parent, cmd, label_factory) -> None:
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.pack(fill="x", pady=6)
        row = ctk.CTkFrame(f, fg_color="transparent")
        row.pack(fill="x", pady=4)
        lbl = ctk.CTkLabel(row, text="Chưa chọn", anchor="w", text_color="gray65")
        lbl.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(row, text="Chọn…", width=88, command=cmd).pack(side="right")
        label_factory(lbl)

    def _lbl_audio(self, w: ctk.CTkLabel) -> None:
        self._lbl_audio_w = w

    def _lbl_footage(self, w: ctk.CTkLabel) -> None:
        self._lbl_footage_w = w

    def _lbl_images(self, w: ctk.CTkLabel) -> None:
        self._lbl_images_w = w

    def _transition_effect_value(self) -> Optional[str]:
        cur = self._cb_transition.get()
        for label, val in TRANSITION_CHOICES:
            if label == cur:
                return val
        return None

    def _on_audio_src_change(self) -> None:
        if self._var_audio_src.get() == "file":
            self._frm_audio_folder.pack_forget()
            self._frm_audio_file.pack(fill="x", pady=4)
        else:
            self._frm_audio_file.pack_forget()
            self._frm_audio_folder.pack(fill="x", pady=4)

    def _pick_audio(self) -> None:
        from tkinter import filedialog

        p = filedialog.askopenfilename(filetypes=[("Audio", "*.mp3 *.wav *.m4a"), ("All", "*.*")])
        if p:
            self._audio_path = Path(p)
            self._lbl_audio_w.configure(text=p)

    def _pick_audio_folder(self) -> None:
        from tkinter import filedialog

        p = filedialog.askdirectory()
        if not p:
            return
        self._audio_folder = Path(p)
        self._lbl_audio_dir.configure(text=p)
        self._audio_files_list = list_unique_mp3_sorted(self._audio_folder)
        n = len(self._audio_files_list)
        if n == 0:
            messagebox.showwarning(
                "Thư mục",
                "Không có file .mp3 trong thư mục (đã quét cả thư mục con, không trùng path).",
            )
            self._lbl_mp3_batch.configure(text="0 file .mp3")
            self._audio_path = None
            return
        root_r = self._audio_folder.resolve()

        def _rel_preview(pp: Path) -> str:
            try:
                return pp.resolve().relative_to(root_r).as_posix()
            except ValueError:
                return pp.name

        order_preview = ", ".join(_rel_preview(x) for x in self._audio_files_list[:5])
        if n > 5:
            order_preview += f", … (+{n - 5})"
        self._lbl_mp3_batch.configure(text=f"{n} file .mp3 — {order_preview}")
        self._audio_path = self._audio_files_list[0]

    def _pick_footage(self) -> None:
        from tkinter import filedialog

        p = filedialog.askdirectory()
        if p:
            self._footage_dir = Path(p)
            self._lbl_footage_w.configure(text=p)

    def _pick_images(self) -> None:
        from tkinter import filedialog

        p = filedialog.askdirectory()
        if p:
            self._images_dir = Path(p)
            self._lbl_images_w.configure(text=p)

    def _pick_logo(self) -> None:
        from tkinter import filedialog

        p = filedialog.askopenfilename(
            title="Chọn logo (PNG/WebP có alpha)",
            filetypes=[
                ("Ảnh logo", "*.png *.webp"),
                ("PNG", "*.png"),
                ("WebP", "*.webp"),
                ("All", "*.*"),
            ],
        )
        if p:
            self._logo_path_ui = Path(p)
            self._lbl_logo_path.configure(text=p)

    def _logo_position_key(self) -> str:
        lab = self._cb_logo_pos.get()
        if "phải" in lab:
            return "top_right"
        return "top_left"

    def _pick_subscribe_anim(self) -> None:
        from tkinter import filedialog

        p = filedialog.askopenfilename(
            title="Chọn ảnh/clip động (subscribe)",
            filetypes=[
                ("GIF / WebP / Video", "*.gif *.webp *.mp4 *.webm *.mov *.mkv"),
                ("GIF", "*.gif"),
                ("WebP", "*.webp"),
                ("Video", "*.mp4 *.webm *.mov *.mkv"),
                ("All", "*.*"),
            ],
        )
        if p:
            self._subscribe_anim_path_ui = Path(p)
            self._lbl_subscribe_anim.configure(text=p)

    def _parse_subscribe_params(self) -> Optional[tuple[float, int, float, float]]:
        try:
            start = float(self._ent_sub_start.get().strip())
            show = float(self._ent_sub_show.get().strip())
            pause = float(self._ent_sub_pause.get().strip())
            count = int(self._ent_sub_count.get().strip())
        except ValueError:
            messagebox.showerror("Subscribe", "Nhập số hợp lệ (thời gian = số thập phân, số lần = số nguyên).")
            return None
        if show <= 0:
            messagebox.showerror("Subscribe", "«Mỗi lần hiển thị» phải > 0 giây.")
            return None
        if start < 0 or pause < 0:
            messagebox.showerror("Subscribe", "Thời gian không được âm.")
            return None
        if count < 1 or count > 80:
            messagebox.showerror("Subscribe", "Số lần xuất hiện từ 1 đến 80.")
            return None
        return (start, count, show, pause)

    def _append_log(self, s: str) -> None:
        self._log.insert("end", s)
        self._log.see("end")

    def _try_hook_drag_drop(self) -> None:
        try:
            import windnd  # type: ignore

            def on_files(paths) -> None:
                if self._busy or self._var_audio_src.get() != "file":
                    return
                for raw in paths:
                    p = Path(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                    if p.suffix.lower() in {".mp3", ".wav", ".m4a"}:
                        self._audio_path = p
                        self._lbl_audio_w.configure(text=str(p))
                        self._append_log(f"[drop] Audio: {p}\n")
                        break

            windnd.hook_dropfiles(self, func=on_files)
            self._append_log("[ui] Kéo thả file audio (windnd) đã bật.\n")
        except Exception:
            self._append_log("[ui] windnd: pip install windnd (Windows).\n")

    def _parse_seed(self) -> Optional[int]:
        s = self._ent_seed.get().strip()
        if not s:
            return None
        try:
            return int(s)
        except ValueError:
            messagebox.showerror("Seed", "Seed phải là số nguyên.")
            return None

    def _on_cancel(self) -> None:
        self._render_controller.cancel()
        self._append_log("[ui] Đã gửi hủy…\n")

    def _on_preview(self) -> None:
        target = self._last_output
        if target is None or not target.is_file():
            messagebox.showinfo("Preview", "Chưa có file output.")
            return

        def log_local(msg: str) -> None:
            self._log_queue.put(("log", msg + "\n"))

        preview_last_frame_or_open(target, log_local)

    def _poll_queue(self) -> None:
        try:
            while True:
                kind, data = self._log_queue.get_nowait()
                if kind == "log":
                    self._append_log(data)
                elif kind == "prog":
                    pct = max(0.0, min(100.0, float(data)))
                    self._progress.set(pct / 100.0)
                    self._lbl_progress_pct.configure(text=f"{int(round(pct))}%")
                elif kind == "done":
                    self._on_render_done(data)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _on_render_done(self, res: RenderResult) -> None:
        self._busy = False
        self._btn_render.configure(state="normal")
        self._progress.set(1.0 if res.ok else 0.0)
        self._lbl_progress_pct.configure(text="100%" if res.ok else "0%")
        if res.batch_total > 1:
            self._last_output = res.output_path
            msg = res.message
            if res.batch_errors:
                msg += "\n\n" + "\n".join(res.batch_errors[:12])
            if res.ok:
                messagebox.showinfo("Batch", f"{msg}\n\nMỗi .mp4 nằm cạnh file .mp3 tương ứng.")
            else:
                messagebox.showwarning("Batch (một phần lỗi)", msg)
            return
        if res.ok and res.output_path:
            self._last_output = res.output_path
            msg = res.message
            if res.from_cache:
                msg += "\n(File từ cache.)"
            messagebox.showinfo("Xong", f"{msg}\n\n{res.output_path}")
        else:
            messagebox.showerror("Lỗi", res.message)

    def _resolve_mp3_paths(self) -> Optional[List[Path]]:
        if self._var_audio_src.get() == "file":
            if self._audio_path and self._audio_path.is_file():
                return [self._audio_path]
            return None
        if self._audio_folder is None or not self._audio_folder.is_dir():
            return None
        lst = list_unique_mp3_sorted(self._audio_folder)
        return lst if lst else None

    def _on_render(self) -> None:
        if self._busy:
            return

        mp3_paths = self._resolve_mp3_paths()
        if not mp3_paths:
            messagebox.showwarning(
                "Audio",
                "Chọn một file audio, hoặc thư mục chứa ít nhất một file .mp3 (kể cả trong thư mục con).",
            )
            return

        seed = self._parse_seed()
        if seed is None and self._ent_seed.get().strip():
            return

        if bool(self._chk_logo.get()) and (
            self._logo_path_ui is None or not self._logo_path_ui.is_file()
        ):
            messagebox.showwarning("Logo", "Đã bật logo nhưng chưa chọn file hợp lệ.")
            return

        sub_start, sub_count, sub_show, sub_pause = 5.0, 3, 4.0, 15.0
        if bool(self._chk_subscribe.get()):
            has_anim = self._subscribe_anim_path_ui is not None and self._subscribe_anim_path_ui.is_file()
            has_txt = bool(self._ent_subscribe_text.get().strip())
            if not has_anim and not has_txt:
                messagebox.showwarning(
                    "Subscribe",
                    "Chọn file ảnh/clip động hoặc nhập chữ kèm theo (ít nhất một trong hai).",
                )
                return
            parsed = self._parse_subscribe_params()
            if parsed is None:
                return
            sub_start, sub_count, sub_show, sub_pause = parsed

        mode = self._render_mode_key()
        is_folder_batch = self._var_audio_src.get() == "folder"
        audio_template = mp3_paths[0]

        base_kw = dict(
            aspect=self._var_aspect.get(),
            gpu_mode=self._var_gpu.get(),
            audio_path=audio_template,
            quality=self._var_quality.get(),
            seed=seed,
            threads_mode=self._var_threads.get(),
            use_cache=bool(self._chk_cache.get()),
            footage_no_repeat=bool(self._chk_footage_unique.get()),
            no_consecutive_footage=bool(self._chk_no_consecutive.get()),
            transition_effect=self._transition_effect_value(),
            transition_duration_sec=float(self._slider_trans.get()),
            logo_path=self._logo_path_ui,
            logo_enabled=bool(self._chk_logo.get()),
            logo_position=self._logo_position_key(),
            logo_width_frac=float(self._slider_logo_pct.get()) / 100.0,
            logo_margin_frac=0.025,
            subscribe_enabled=bool(self._chk_subscribe.get()),
            subscribe_animation_path=self._subscribe_anim_path_ui,
            subscribe_anim_width_frac=float(self._slider_subscribe_pct.get()) / 100.0,
            subscribe_text=self._ent_subscribe_text.get().strip(),
            subscribe_start_sec=sub_start,
            subscribe_duration_sec=sub_show,
            subscribe_show_count=sub_count,
            subscribe_pause_sec=sub_pause,
        )

        if mode == RENDER_SLIDES_MP3:
            if self._images_dir is None or not self._images_dir.is_dir():
                messagebox.showwarning("Ảnh", "Chọn thư mục ảnh cho slideshow.")
                return
            settings = RenderSettings(render_mode=RENDER_SLIDES_MP3, images_dir=self._images_dir, **base_kw)
        elif mode == RENDER_FOOTAGE_MP3:
            if self._footage_dir is None or not self._footage_dir.is_dir():
                messagebox.showwarning("Footage", "Chọn thư mục footage.")
                return
            settings = RenderSettings(render_mode=RENDER_FOOTAGE_MP3, footage_dir=self._footage_dir, **base_kw)
        else:
            if self._footage_dir is None or not self._footage_dir.is_dir():
                messagebox.showwarning("Footage", "Chọn thư mục footage.")
                return
            if self._images_dir is None or not self._images_dir.is_dir():
                messagebox.showwarning("Ảnh", "Chọn thư mục ảnh (avatar).")
                return
            settings = RenderSettings(
                render_mode=RENDER_FULL,
                footage_dir=self._footage_dir,
                images_dir=self._images_dir,
                avatar_position=self._var_pos.get(),
                rembg=bool(self._chk_rembg.get()),
                avatar_no_repeat_cycle=bool(self._chk_avatar_cycle.get()),
                **base_kw,
            )

        self._busy = True
        self._btn_render.configure(state="disabled")
        self._progress.set(0)
        self._lbl_progress_pct.configure(text="0%")

        def log_fn(msg: str) -> None:
            self._log_queue.put(("log", msg + "\n"))

        def prog_fn(p: float) -> None:
            self._log_queue.put(("prog", p))

        def worker() -> None:
            if is_folder_batch:
                res = render_folder_mp3_batch(mp3_paths, settings, log_fn, prog_fn, self._render_controller)
            else:
                res = render_video(settings, log_fn, prog_fn, self._render_controller)
            self._log_queue.put(("done", res))

        threading.Thread(target=worker, daemon=True).start()
