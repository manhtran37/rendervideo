"""
Biểu thức enable cho overlay/drawtext (nhắc subscribe theo từng «xung»).
"""

from __future__ import annotations


def subscribe_pulses_enable_expr(
    start_sec: float,
    duration_sec: float,
    pause_after_sec: float,
    count: int,
) -> str:
    """
    Nhiều cửa sổ hiển thị: lần k bắt đầu lúc start + k * (duration + pause),
    kết thúc sau duration giây. Dùng trong overlay/drawtext enable='...'
    (dấu phẩy trong between phải là \\,). Cộng các between(...) = OR logic.
    """
    st = max(0.0, float(start_sec))
    d = max(0.05, float(duration_sec))
    pause = max(0.0, float(pause_after_sec))
    n = max(1, min(80, int(count)))
    period = d + pause
    parts: list[str] = []
    for k in range(n):
        s0 = st + k * period
        e0 = s0 + d
        parts.append(f"between(t\\,{s0:.4f}\\,{e0:.4f})")
    return "+".join(parts)


def escape_path_for_filter(path_str: str) -> str:
    """Đường dẫn POSIX trong filter (escape ':' cho ổ Windows)."""
    s = path_str.replace("\\", "/")
    if len(s) > 1 and s[1] == ":":
        return s[0] + "\\:" + s[2:]
    return s
