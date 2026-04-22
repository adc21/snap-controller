"""ケース比較タブが 1 件でも有効になることを確認。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from app.ui.main_window import MainWindow

    w = MainWindow()
    tabs = w._right_tabs

    # タブ一覧を表示
    print("=== 右パネルタブ一覧 ===")
    for i in range(tabs.count()):
        req, min_r = w._tab_result_requirements.get(i, (False, 0))
        label = tabs.tabText(i)
        print(f"  [{i}] {label!r}  req={req} min_r={min_r}")

    # ケース比較タブのインデックスを特定
    compare_idx = None
    for i in range(tabs.count()):
        if "ケース比較" in tabs.tabText(i):
            compare_idx = i
            break

    if compare_idx is None:
        print("[FAIL] 'ケース比較' タブが見つからない")
        return 1

    print(f"\n=== 'ケース比較' タブ検証 (index={compare_idx}) ===")

    # result_count=0 → 無効
    w._update_result_tabs(result_count=0)
    enabled_0 = tabs.isTabEnabled(compare_idx)
    tip_0 = tabs.tabBar().tabToolTip(compare_idx)
    print(f"  result_count=0 → enabled={enabled_0}  tip={tip_0!r}")

    # result_count=1 → ★ここで有効になるべき★
    w._update_result_tabs(result_count=1)
    enabled_1 = tabs.isTabEnabled(compare_idx)
    tip_1 = tabs.tabBar().tabToolTip(compare_idx)
    print(f"  result_count=1 → enabled={enabled_1}  tip={tip_1!r}")

    # result_count=2 → 有効のまま
    w._update_result_tabs(result_count=2)
    enabled_2 = tabs.isTabEnabled(compare_idx)
    print(f"  result_count=2 → enabled={enabled_2}")

    ok = (not enabled_0) and enabled_1 and enabled_2

    # タブ切替が crash しないか (1 ケース状態で)
    w._update_result_tabs(result_count=1)
    try:
        tabs.setCurrentIndex(compare_idx)
        app.processEvents()
        current = tabs.currentIndex()
        switch_ok = current == compare_idx
        print(f"\n=== タブ切替テスト (1 ケース) ===")
        print(f"  setCurrentIndex({compare_idx}) → 実際のcurrent={current} ok={switch_ok}")
    except Exception as e:
        print(f"  [FAIL] 切替でエラー: {e}")
        switch_ok = False

    ok = ok and switch_ok
    print(f"\n=== 結果: {'PASS' if ok else 'FAIL'} ===")
    return 0 if ok else 1


if __name__ == "__main__":
    rc = main()
    os._exit(rc)
