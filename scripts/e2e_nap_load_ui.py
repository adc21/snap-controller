"""実 UI 経由の NAP 読み込み E2E 検証。

MainWindow をインスタンス化して ``_load_s8i_from_path`` に example_3D.NAP を渡し、
project.s8i_path / s8i_model が期待通りに入ることを確認する。

手動 UI 操作の代わりに、最終的なコード経路 (ドラッグ&ドロップ発火 → load_s8i)
を完全に通す。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

NAP = Path(__file__).resolve().parent.parent / "example_model" / "example_3D" / "example_3D.NAP"


def main() -> int:
    if not NAP.exists():
        print(f"FAIL: NAP が無い: {NAP}")
        return 1

    # 安全のため、既存 s8i があれば削除して「変換が実際に走った」ことを検証可能にする
    sibling_s8i = NAP.with_suffix(".s8i")
    had_existing = sibling_s8i.exists()
    existing_bytes = sibling_s8i.read_bytes() if had_existing else None

    app = QApplication.instance() or QApplication(sys.argv)

    from app.ui.main_window import MainWindow

    print("[1] MainWindow instantiation")
    w = MainWindow()
    w.show()
    app.processEvents()

    print(f"[2] _load_s8i_from_path: {NAP}")
    t0 = time.time()
    # ドラッグ&ドロップ経路を直接呼ぶ
    w._load_s8i_from_path(str(NAP))
    elapsed = time.time() - t0
    print(f"   完了 ({elapsed:.1f}s)")

    proj = w._project
    assert proj is not None, "project is None"
    print(f"[3] project.s8i_path = {proj.s8i_path}")
    print(f"    model.num_nodes  = {proj.s8i_model.num_nodes if proj.s8i_model else None}")
    print(f"    model.num_floors = {proj.s8i_model.num_floors if proj.s8i_model else None}")
    print(f"    model.num_dampers= {proj.s8i_model.num_dampers if proj.s8i_model else None}")

    assert proj.s8i_model is not None, "s8i_model が None"
    assert proj.s8i_model.num_nodes == 916, f"節点数不一致: {proj.s8i_model.num_nodes}"
    assert proj.s8i_model.num_floors == 21, f"層数不一致: {proj.s8i_model.num_floors}"
    assert proj.s8i_model.num_dampers == 240, f"ダンパー数不一致: {proj.s8i_model.num_dampers}"
    assert proj.s8i_path.lower().endswith(".s8i"), f"s8i_path が .s8i で終わらない: {proj.s8i_path}"

    print("[4] PASS - NAP->s8i->UI reflected through full path")

    # 後始末: 既存 s8i があった場合は元に戻す (テスト汚染を避ける)
    if had_existing and existing_bytes is not None:
        sibling_s8i.write_bytes(existing_bytes)
        print(f"[5] 既存 {sibling_s8i.name} を復元")

    w.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
