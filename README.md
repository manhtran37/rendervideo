# Render Video — Desktop (Python)

Ứng dụng ghép **footage ngẫu nhiên** + **avatar** + **overlay** + **audio**, xuất **một file MP4** có thời lượng **khớp đúng audio**. Encode qua **FFmpeg** (ưu tiên **NVIDIA NVENC** nếu có).

## Yêu cầu hệ thống

1. **Python 3.10+**
2. **FFmpeg** (cả `ffmpeg` và `ffprobe`) trong [PATH](https://ffmpeg.org/download.html)
3. (Tùy chọn) **GPU NVIDIA** + driver để dùng `h264_nvenc`
4. (Tùy chọn) **rembg** + **onnxruntime** nếu bật tách nền avatar

## Cài đặt

```bash
cd "c:\Users\Admin\Desktop\RENDER VIDEO"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Tách nền (khi cần):

```bash
pip install rembg onnxruntime
```

Kéo thả file `.mp3` trên Windows:

```bash
pip install windnd
```

## Chạy ứng dụng

```bash
python main.py
```

## Cấu trúc thư mục

- `main.py` — điểm vào
- `ui/app.py` — giao diện CustomTkinter
- `core/` — footage, avatar, pipeline FFmpeg
- `utils/` — ffprobe, GPU, cache

## Ghi chú

- **Đồng bộ audio/video**: pipeline cắt/ghép footage theo tổng thời lượng audio, output dùng `-t <audio_duration>`.
- **Cache**: chỉ áp dụng khi nhập **Seed** cố định (để cùng input + cùng seed cho cùng kết quả).
- **Hủy render**: nút **Hủy** gửi `terminate` tới tiến trình ffmpeg.
- **Preview**: **Mở preview** mở file output bằng ứng dụng mặc định của hệ điều hành.

## Giấy phép

Mã nguồn mẫu cho mục đích sử dụng cá nhân / chỉnh sửa tự do.
