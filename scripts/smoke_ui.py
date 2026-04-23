"""Quick smoke test — boot MainWindow offscreen and verify tab layout."""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication


def main() -> int:
    app = QApplication([])
    from app.ui.main_window import MainWindow
    w = MainWindow()
    print("MainWindow created OK")
    print("Right tabs count:", w._right_tabs.count())
    for i in range(w._right_tabs.count()):
        print(f"  tab[{i}]: {w._right_tabs.tabText(i)}")
    print("Case DYC selector present:", w._case_dyc_selector is not None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
