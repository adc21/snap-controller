"""NAP 変換中の進捗ダイアログを画面に出して、変換中のスクショを撮る。"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import ImageGrab
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

NAP = Path(__file__).resolve().parent.parent / "example_model" / "example_3D" / "example_3D.NAP"
OUT_PNG = Path(__file__).resolve().parent.parent / "tmp" / "nap_cli_test" / "app_conversion_dialog.png"


def _minimize_others(own_hwnd: int) -> None:
    import win32con
    import win32gui

    def cb(h, _):
        if h == own_hwnd or not win32gui.IsWindowVisible(h):
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


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from app.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    app.processEvents()

    import win32con
    import win32gui

    own = int(w.winId())
    _minimize_others(own)
    time.sleep(0.6)
    win32gui.ShowWindow(own, win32con.SW_SHOWMAXIMIZED)
    win32gui.SetForegroundWindow(own)
    app.processEvents()
    time.sleep(0.4)

    # ワークスペース / STEP1 表示
    try:
        w._main_stack.setCurrentIndex(1)
        w._sidebar.set_current_step(0)
        app.processEvents()
    except Exception:
        pass

    # スレッドからスクショ — Qt イベントループがブロックされていても撮れる
    def _capture_worker():
        # 変換開始 (load 発火) を 1.5s 後と想定 → +8s でスクショ (SNAP.exe 起動中)
        time.sleep(9.5)
        OUT_PNG.parent.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab().save(str(OUT_PNG))
        print(f"[capture] saved: {OUT_PNG}", flush=True)

    threading.Thread(target=_capture_worker, daemon=True).start()

    # 同期的に load_s8i を呼ぶ (ダイアログは show+processEvents で描画される)
    def _trigger():
        try:
            w._load_s8i_from_path(str(NAP))
            print("[trigger] load finished", flush=True)
        except Exception as e:
            print(f"[trigger] error: {e}", flush=True)

    QTimer.singleShot(1500, _trigger)

    # 50 秒で終了
    QTimer.singleShot(50000, app.quit)
    app.exec()
    return 0


if __name__ == "__main__":
    main()
    os._exit(0)
