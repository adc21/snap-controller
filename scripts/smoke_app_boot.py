"""Boot the real app for a few seconds (visible window) and verify no crash."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


def main() -> int:
    app = QApplication(sys.argv)
    from app.ui.main_window import MainWindow

    w = MainWindow()
    w.show()

    # Schedule close after 2 seconds, return 0 if still alive
    def _close():
        print(f"[OK] App alive after 2s, right_tabs={w._right_tabs.count()}")
        # Verify selector present
        assert w._case_dyc_selector is not None
        assert w._step4_split is not None
        print(f"     selector={w._case_dyc_selector.__class__.__name__}")
        print(f"     splitter sizes={w._step4_split.sizes()}")
        app.quit()

    QTimer.singleShot(2000, _close)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
