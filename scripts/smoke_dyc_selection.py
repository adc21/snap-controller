"""Verify CaseDycSelectorWidget produces correct DycSelection list with mocked cases."""
import os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QTreeWidgetItem  # noqa: F401

from app.models import AnalysisCase, AnalysisCaseStatus
from app.ui.case_dyc_selector_widget import CaseDycSelectorWidget, DycSelection


def make_case(name, dyc_defs):
    c = AnalysisCase(name=name, status=AnalysisCaseStatus.COMPLETED)
    c.result_summary = {"max_drift": 0.01}
    c.dyc_results = []
    for i, (cname, run, has_res) in enumerate(dyc_defs):
        c.dyc_results.append({
            "case_no": i + 1,
            "case_name": cname,
            "run_flag": 1 if run else 0,
            "has_result": has_res,
            "result_data": {"max_disp": {1: 0.01, 2: 0.02}} if has_res else {},
            "result_summary": {"max_drift": 0.01} if has_res else {},
            "result_dir": f"/tmp/case{i}_dyc{i}" if has_res else "",
        })
    return c


def main():
    app = QApplication.instance() or QApplication([])

    sel_widget = CaseDycSelectorWidget()
    case_a = make_case("CaseA", [("固有値解析", True, True), ("時刻歴応答", True, True)])
    case_b = make_case("CaseB", [("固有値解析", True, True), ("時刻歴応答", False, False)])
    sel_widget.set_cases([case_a, case_b])

    # Simulate checking CaseA node (parent)
    item_a = sel_widget._tree.topLevelItem(0)
    item_a.setCheckState(0, Qt.Checked)

    selections = sel_widget.current_selections()
    print(f"After checking CaseA parent: {len(selections)} selections")
    for s in selections:
        print(f"  - {s.display_name} (dyc_idx={s.dyc_index}, dir={s.result_dir})")

    # Unselect then check one DYC child of CaseB
    item_a.setCheckState(0, Qt.Unchecked)
    item_b = sel_widget._tree.topLevelItem(1)
    child_b0 = item_b.child(0)
    child_b1 = item_b.child(1)
    print(f"\nBefore check: b0 state={child_b0.checkState(0)}, checkable={bool(child_b0.flags() & Qt.ItemIsUserCheckable)}, enabled={bool(child_b0.flags() & Qt.ItemIsEnabled)}")
    print(f"Before check: b1 state={child_b1.checkState(0)}, checkable={bool(child_b1.flags() & Qt.ItemIsUserCheckable)}, enabled={bool(child_b1.flags() & Qt.ItemIsEnabled)}")
    child_b0.setCheckState(0, Qt.Checked)
    print(f"After check: b0 state={child_b0.checkState(0)}, item_b state={item_b.checkState(0)}")
    # Also re-check states via internal build
    raw_sels = sel_widget._build_selections()
    print(f"_build_selections direct: {len(raw_sels)}")
    selections = sel_widget.current_selections()
    print(f"After checking only CaseB.D1: {len(selections)} selections")
    for s in selections:
        print(f"  - {s.display_name} (dyc_idx={s.dyc_index})")

    # select_case API
    sel_widget.select_case(case_a.id, exclusive=True)
    selections = sel_widget.current_selections()
    print(f"\nAfter select_case(CaseA.id, exclusive): {len(selections)} selections")
    for s in selections:
        print(f"  - {s.display_name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
