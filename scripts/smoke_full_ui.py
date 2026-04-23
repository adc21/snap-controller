"""End-to-end smoke: boot MainWindow, inject mocked cases with dyc_results, exercise selector and tabs."""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


def _fake_case(project, name: str, dyc_defs):
    """Create an AnalysisCase + prepopulate dyc_results as if analysis finished.

    dyc_defs: list of (case_name, run_flag, has_result, result_dir)
    """
    from app.models import AnalysisCase, AnalysisCaseStatus

    c = AnalysisCase(
        name=name,
        status=AnalysisCaseStatus.COMPLETED,
        model_path=str(Path("example_model/example_3D/example_3D.s8i").resolve()),
        snap_exe_path=r"C:\Program Files\SNAP Ver.8\Snap.exe",
        output_dir=r"D:\Kakemoto\kozosystem\SNAPV8\work\example_3D",
    )
    c.result_summary = {"max_drift": 0.012, "max_accel": 550.0}
    c.dyc_results = []
    for i, (cname, run, has_res, rdir) in enumerate(dyc_defs):
        c.dyc_results.append({
            "case_no": i + 1,
            "case_name": cname,
            "run_flag": 1 if run else 0,
            "has_result": has_res,
            "result_data": {"max_disp": {1: 0.01, 2: 0.02, 3: 0.03}} if has_res else {},
            "result_summary": {"max_drift": 0.01 + i * 0.002, "max_accel": 500.0 + i * 10} if has_res else {},
            "result_dir": str(rdir) if has_res and rdir else "",
        })
    project.add_case(c)
    return c


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)

    from app.ui.main_window import MainWindow
    from app.models import Project

    w = MainWindow()
    print(f"[OK] MainWindow created, {w._right_tabs.count()} right tabs")

    # Create in-memory project and seed cases
    project = Project(name="smoke_test")
    base_work = Path(r"D:\Kakemoto\kozosystem\SNAPV8\work\example_3D")
    case_a = _fake_case(project, "ケースA", [
        ("固有値解析", True, True, base_work / "D1"),
        ("時刻歴応答_BCJL2", True, True, base_work / "D2"),
        ("応答スペクトル", True, False, None),
    ])
    case_b = _fake_case(project, "ケースB", [
        ("固有値解析", True, True, base_work / "D1"),
        ("時刻歴応答_告示波", True, True, base_work / "D4"),
    ])
    case_c = _fake_case(project, "ケースC(失敗)", [
        ("固有値解析", True, False, None),
    ])

    # Wire the project into MainWindow
    w._project = project
    w._case_table.set_project(project)
    w._case_dyc_selector.set_cases(project.cases)
    print(f"[OK] Seeded {len(project.cases)} cases into selector")

    tree = w._case_dyc_selector._tree
    print(f"  tree top-level items: {tree.topLevelItemCount()}")
    for i in range(tree.topLevelItemCount()):
        it = tree.topLevelItem(i)
        print(f"    [{i}] '{it.text(0)}' children={it.childCount()} enabled={bool(it.flags() & Qt.ItemIsEnabled)}")
        for j in range(it.childCount()):
            c = it.child(j)
            print(f"         └ '{c.text(0)}' checkable={bool(c.flags() & Qt.ItemIsUserCheckable)} enabled={bool(c.flags() & Qt.ItemIsEnabled)}")

    # Scenario 1: check ケースA parent → expect 2 selections (2 checkable DYCs)
    item_a = tree.topLevelItem(0)
    item_a.setCheckState(0, Qt.Checked)
    sels = w._case_dyc_selector.current_selections()
    print(f"\n[Scenario 1] check parent ケースA → {len(sels)} selections (expected 2)")
    assert len(sels) == 2, f"expected 2, got {len(sels)}"

    # Scenario 2: also check one child of ケースB
    item_b = tree.topLevelItem(1)
    item_b.child(1).setCheckState(0, Qt.Checked)  # D2 時刻歴応答_告示波
    sels = w._case_dyc_selector.current_selections()
    print(f"[Scenario 2] +ケースB/D2 → {len(sels)} selections (expected 3)")
    assert len(sels) == 3, f"expected 3, got {len(sels)}"

    # Scenario 3: exclusive select_case on ケースB → expect both checkable DYCs of B only
    w._case_dyc_selector.select_case(case_b.id, exclusive=True)
    sels = w._case_dyc_selector.current_selections()
    names = [s.display_name for s in sels]
    print(f"[Scenario 3] select_case(B, exclusive) → {len(sels)} sels: {names}")
    assert len(sels) == 2, f"expected 2, got {len(sels)}"
    assert all("ケースB" in n for n in names), f"expected only ケースB, got {names}"

    # Scenario 4: click through each result tab to make sure no exception is raised
    print("\n[Scenario 4] cycle through all right_tabs tabs:")
    for i in range(w._right_tabs.count()):
        label = w._right_tabs.tabText(i)
        try:
            w._right_tabs.setCurrentIndex(i)
            app.processEvents()
            print(f"  tab[{i}] '{label}' → OK")
        except Exception as e:
            print(f"  tab[{i}] '{label}' → EXCEPTION: {e}")
            traceback.print_exc()
            return 1

    # Scenario 5: clear all → 0 selections, widgets should handle empty
    w._case_dyc_selector._clear_all()
    sels = w._case_dyc_selector.current_selections()
    print(f"\n[Scenario 5] clear_all → {len(sels)} selections (expected 0)")
    assert len(sels) == 0

    for i in range(w._right_tabs.count()):
        w._right_tabs.setCurrentIndex(i)
        app.processEvents()
    print("[OK] all tabs render with empty selection")

    # Scenario 6: set_cases preserves selection after re-check
    item_a = tree.topLevelItem(0)
    item_a.setCheckState(0, Qt.Checked)
    pre = {(s.case.id, s.dyc_index) for s in w._case_dyc_selector.current_selections()}
    w._case_dyc_selector.set_cases(project.cases)  # rebuild
    post = {(s.case.id, s.dyc_index) for s in w._case_dyc_selector.current_selections()}
    print(f"\n[Scenario 6] rebuild preserves selection: pre={len(pre)} post={len(post)} equal={pre == post}")
    assert pre == post, f"selection not preserved: {pre} vs {post}"

    print("\n[ALL OK] full UI smoke passed")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as e:
        print(f"\n[FAIL] {e}")
        traceback.print_exc()
        raise SystemExit(2)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        traceback.print_exc()
        raise SystemExit(3)
