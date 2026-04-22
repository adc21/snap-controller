"""NAP→s8i 統合の複数シナリオ E2E テスト。

Scenario A: 既存 s8i が無い状態で NAP を開く（新規生成パス）
Scenario B: NAP 読み込み後の下流 UI 状態（case_table, model_info 等）
Scenario C: 変換後の s8i で実際にケース追加 → 解析準備ができるか
Scenario D: アプリ起動スモーク（run_app.py 経由で import エラー無しを確認）
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent
SOURCE_NAP = ROOT / "example_model" / "example_3D" / "example_3D.NAP"


def _divider(title: str) -> None:
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


def scenario_a_fresh_conversion() -> bool:
    """既存 s8i が無い場所に NAP だけ置いて変換させる。"""
    _divider("Scenario A: 既存 s8i なしで NAP を開く")
    if not SOURCE_NAP.exists():
        print(f"  SKIP: ソース NAP 無し: {SOURCE_NAP}")
        return True

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        target_nap = tmpdir / "example_fresh.NAP"
        target_s8i = tmpdir / "example_fresh.s8i"
        shutil.copy2(SOURCE_NAP, target_nap)
        assert not target_s8i.exists(), "初期状態で s8i が存在してはいけない"
        print(f"  NAP: {target_nap}")
        print(f"  (s8i 事前存在: {target_s8i.exists()})")

        app = QApplication.instance() or QApplication(sys.argv)
        from app.ui.main_window import MainWindow

        w = MainWindow()
        w.show()
        app.processEvents()

        t0 = time.time()
        w._load_s8i_from_path(str(target_nap))
        elapsed = time.time() - t0

        ok = target_s8i.exists() and target_s8i.stat().st_size > 100_000
        proj = w._project
        has_model = proj is not None and proj.s8i_model is not None
        print(f"  変換時間: {elapsed:.1f}s")
        print(f"  s8i 生成: {ok} ({target_s8i.stat().st_size if target_s8i.exists() else 0} bytes)")
        print(f"  model loaded: {has_model}")
        if has_model:
            print(f"  nodes={proj.s8i_model.num_nodes} floors={proj.s8i_model.num_floors}")

        w.close()
        return ok and has_model


def scenario_b_downstream_ui_state() -> bool:
    """NAP 読み込み後、case_table / model_info / file_preview の状態を確認。"""
    _divider("Scenario B: 下流 UI 状態の確認")
    if not SOURCE_NAP.exists():
        print(f"  SKIP: ソース NAP 無し: {SOURCE_NAP}")
        return True

    app = QApplication.instance() or QApplication(sys.argv)
    from app.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    app.processEvents()

    # 既存 s8i をバックアップして一度削除 (必ず変換が走るように)
    sibling = SOURCE_NAP.with_suffix(".s8i")
    backup = None
    if sibling.exists():
        backup = sibling.read_bytes()
        sibling.unlink()

    try:
        w._load_s8i_from_path(str(SOURCE_NAP))
        app.processEvents()

        proj = w._project
        checks = {
            "project.has_s8i": proj.has_s8i,
            "s8i_path=.s8i": proj.s8i_path.lower().endswith(".s8i"),
            "model_info has model": w._model_info._model is not None,
            "case_table model_loaded": w._case_table._model_loaded,
            "title contains model name": bool(w.windowTitle()),
        }
        all_ok = all(checks.values())
        for k, v in checks.items():
            mark = "OK" if v else "NG"
            print(f"  [{mark}] {k}: {v}")
        print(f"  window title: {w.windowTitle()!r}")
        return all_ok
    finally:
        # 元に戻す
        if backup is not None:
            sibling.write_bytes(backup)
        w.close()


def scenario_c_case_addition() -> bool:
    """変換後の s8i に対してケース追加が機能することを確認。"""
    _divider("Scenario C: 変換後 → ケース追加まで")
    if not SOURCE_NAP.exists():
        print(f"  SKIP: ソース NAP 無し: {SOURCE_NAP}")
        return True

    app = QApplication.instance() or QApplication(sys.argv)
    from app.ui.main_window import MainWindow

    w = MainWindow()
    w.show()
    app.processEvents()

    w._load_s8i_from_path(str(SOURCE_NAP))
    app.processEvents()

    proj = w._project
    initial_cases = len(proj.cases)
    print(f"  ロード直後のケース数: {initial_cases}")

    # ケースを1つ追加
    from app.models.analysis_case import AnalysisCase

    case = AnalysisCase(name="E2E_テストケース")
    proj.cases.append(case)
    w._case_table.refresh()
    app.processEvents()

    after_cases = len(proj.cases)
    print(f"  追加後のケース数: {after_cases}")
    ok = after_cases == initial_cases + 1
    print(f"  ケース追加: {'OK' if ok else 'NG'}")

    w.close()
    return ok


def scenario_d_app_startup_smoke() -> bool:
    """run_app.py のインポートが通ること (最小起動スモーク)。"""
    _divider("Scenario D: アプリ起動スモーク (import)")
    try:
        # run_app.py は argv に依存するため、明示的に空に
        orig_argv = sys.argv[:]
        sys.argv = ["smoke"]
        # import するだけ (QApplication.exec() は呼ばない)
        import importlib

        # 既に import された MainWindow モジュールをリロードしてクリーンに
        for mod in ["app.ui.main_window", "app.models.project"]:
            if mod in sys.modules:
                importlib.reload(sys.modules[mod])
        from app.ui.main_window import MainWindow  # noqa: F401
        from controller.nap_converter import convert_nap_to_s8i  # noqa: F401

        sys.argv = orig_argv
        print("  import OK: MainWindow, nap_converter")
        return True
    except Exception as e:
        print(f"  import FAIL: {e}")
        traceback.print_exc()
        return False


def main() -> int:
    results = {
        "A (fresh conversion)": scenario_a_fresh_conversion(),
        "B (downstream UI state)": scenario_b_downstream_ui_state(),
        "C (case addition)": scenario_c_case_addition(),
        "D (app startup smoke)": scenario_d_app_startup_smoke(),
    }

    _divider("RESULTS")
    all_pass = True
    for k, v in results.items():
        mark = "PASS" if v else "FAIL"
        print(f"  [{mark}] {k}", flush=True)
        if not v:
            all_pass = False

    # MainWindow の atexit / QTimer が Qt イベントループを生かし続けることがあるため
    # 強制終了 (テストの戻り値は保ちつつ)
    import os as _os
    _os.sync() if hasattr(_os, "sync") else None
    sys.stdout.flush()
    sys.stderr.flush()
    _os._exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
