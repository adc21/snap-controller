"""空状態 (NAP/s8i 未ロード) のスクリーンショット。UI ラベル確認用。"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import ImageGrab
from PySide6.QtWidgets import QApplication

OUT_PNG = Path(__file__).resolve().parent.parent / "tmp" / "nap_cli_test" / "app_ui_labels.png"


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from app.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    app.processEvents()

    import win32con
    import win32gui

    own = int(w.winId())

    def cb(h, _):
        if h == own or not win32gui.IsWindowVisible(h):
            return True
        if win32gui.GetParent(h) != 0:
            return True
        t = win32gui.GetWindowText(h)
        c = win32gui.GetClassName(h)
        if not t or c in ("Progman", "WorkerW", "Shell_TrayWnd"):
            return True
        try:
            win32gui.ShowWindow(h, win32con.SW_MINIMIZE)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(cb, None)
    time.sleep(0.8)
    win32gui.ShowWindow(own, win32con.SW_SHOWMAXIMIZED)
    win32gui.SetForegroundWindow(own)
    time.sleep(1.0)

    # ワークスペース表示に切替 → STEP1（モデル設定）
    try:
        w._main_stack.setCurrentIndex(1)
        w._sidebar.set_current_step(0)
        app.processEvents()
        time.sleep(0.5)
    except Exception as e:
        print(f"nav error: {e}")

    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab().save(str(OUT_PNG))
    print(f"saved: {OUT_PNG}")
    return 0


if __name__ == "__main__":
    main()
    os._exit(0)
