"""
Render Video — entry point.
Chạy: python main.py (từ thư mục gốc dự án).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.app import RenderVideoApp  # noqa: E402


def main() -> None:
    app = RenderVideoApp()
    app.mainloop()


if __name__ == "__main__":
    main()
