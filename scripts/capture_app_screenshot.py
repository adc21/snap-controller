"""snap-controller を起動 → NAP ロード後、他ウィンドウを最小化してスクショ。

他アプリ (Claude 等) の上から snap-controller を前面に出すには
SetForegroundWindow だけでは Windows の制限で無効化されることがあるため、
他の可視トップレベルウィンドウを全部最小化する戦略を使う。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import ImageGrab
from PySide6.QtWidgets import QApplication

NAP = Path(__file__).resolve().parent.parent / "example_model" / "example_3D" / "example_3D.NAP"
OUT_PNG = Path(__file__).resolve().parent.parent / "tmp" / "nap_cli_test" / "app_foreground.png"


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    from app.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    app.processEvents()

    print(f"Loading NAP: {NAP}")
    w._load_s8i_from_path(str(NAP))
    app.processEvents()

    # snap-controller 以外の可視ウィンドウを最小化
    import win32con
    import win32gui

    own_hwnd = int(w.winId())
    minimized = []

    def enum_cb(h, _):
        if h == own_hwnd or not win32gui.IsWindowVisible(h):
            return True
        # 自分の子は飛ばす
        if win32gui.GetParent(h) != 0:
            return True
        title = win32gui.GetWindowText(h)
        cls = win32gui.GetClassName(h)
        # 無題 / システム系はスキップ
        if not title or cls in ("Progman", "WorkerW", "Shell_TrayWnd"):
            return True
        try:
            win32gui.ShowWindow(h, win32con.SW_MINIMIZE)
            minimized.append((h, title))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(enum_cb, None)
    print(f"minimized {len(minimized)} windows")

    time.sleep(1.0)
    app.processEvents()

    # snap-controller を前面化 & 最大化
    win32gui.ShowWindow(own_hwnd, win32con.SW_SHOWMAXIMIZED)
    win32gui.SetForegroundWindow(own_hwnd)
    time.sleep(1.0)

    # スクショ
    OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    ImageGrab.grab().save(str(OUT_PNG))
    print(f"screenshot saved: {OUT_PNG}")

    return 0


if __name__ == "__main__":
    import os
    sys.stdout.flush()
    main()
    os._exit(0)
