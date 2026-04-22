"""snap-controller を起動し、example_3D.NAP を自動ロードして表示する。

目視確認用。Qt イベントループに入るので、ウィンドウが閉じられるまでブロックする。
自動終了させたい場合は環境変数 ``SNAPC_SMOKE_EXIT=5`` 等で秒数を指定。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

NAP = Path(__file__).resolve().parent.parent / "example_model" / "example_3D" / "example_3D.NAP"


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    from app.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    app.processEvents()

    # NAP を自動ロード (ドラッグ&ドロップ経路)
    if NAP.exists():
        print(f"Loading NAP: {NAP}")
        w._load_s8i_from_path(str(NAP))
    else:
        print(f"NAP not found: {NAP}")
        return 1

    # 読み込み完了後に snap-controller を前面化 (変換中は SNAP.exe が
    # フォアグラウンドを奪うので、変換完了後に確実に取り戻す)
    try:
        import win32gui
        import win32con

        hwnd = int(w.winId())
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
        w.raise_()
        w.activateWindow()
        print(f"foreground set: HWND=0x{hwnd:X}")
    except Exception as e:
        print(f"foreground failed: {e}")

    # スモーク終了: 環境変数で秒数を指定されていれば自動クローズ
    smoke_exit = os.environ.get("SNAPC_SMOKE_EXIT")
    if smoke_exit:
        delay_ms = int(float(smoke_exit) * 1000)
        print(f"Auto-close scheduled: {smoke_exit}s")
        QTimer.singleShot(delay_ms, app.quit)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
