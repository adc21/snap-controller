"""
tests/test_irdt_wizard.py
=========================

iRDT 設計ウィザードダイアログのテスト。
純粋ロジック（Step 5 の DamperInsertSpec 変換など）と
Qt UI インスタンス化テストを含む。
"""

from __future__ import annotations

import math
from unittest.mock import MagicMock, patch

import pytest

from app.services.irdt_designer import (
    IrdtFloorAssignment,
    IrdtPlacementPlan,
    fixed_point_optimal,
    design_irdt_placement,
)
from app.services.damper_injector import DamperInsertSpec


def _qt_available() -> bool:
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


# ===========================================================================
# 純粋ロジックテスト（Qt 不要）
# ===========================================================================


class TestPlacementPlanToSpecs:
    """IrdtPlacementPlan → DamperInsertSpec 変換ロジックのテスト。"""

    @staticmethod
    def _make_plan(n_floors: int = 3, mu: float = 0.02) -> IrdtPlacementPlan:
        masses = [1e6] * n_floors
        mode_shape = [
            math.sin((2 * (k + 1) - 1) * math.pi / (2 * n_floors + 1))
            for k in range(n_floors)
        ]
        return design_irdt_placement(
            masses=masses,
            mode_shape=mode_shape,
            target_period=1.0,
            total_mass_ratio=mu,
            target_mode=1,
            distribution="interstory",
        )

    def test_plan_has_floor_assignments(self):
        plan = self._make_plan()
        assert len(plan.floor_plan) == 3
        for a in plan.floor_plan:
            assert a.inertance > 0
            assert a.damping > 0
            assert a.support_stiffness > 0

    def test_unit_conversion_si_to_kn(self):
        """SI (kg, N·s/m, N/m) → kN·s²/m, kN·s/m, kN/m 変換。"""
        plan = self._make_plan()
        a = plan.floor_plan[0]

        mass_kN = a.inertance / 1000.0
        spring_kN = a.support_stiffness / 1000.0
        damping_kN = a.damping / 1000.0

        assert mass_kN > 0
        assert spring_kN > 0
        assert damping_kN > 0
        # kN 値が元値より小さいこと
        assert mass_kN < a.inertance
        assert spring_kN < a.support_stiffness
        assert damping_kN < a.damping

    def test_spec_creation_from_assignment(self):
        """IrdtFloorAssignment から DamperInsertSpec を正しく生成。"""
        a = IrdtFloorAssignment(
            floor=3,
            mode_amplitude=0.5,
            inter_story_mode=0.2,
            mass_ratio_effective=0.01,
            inertance=5000.0,       # kg
            damping=2000.0,         # N·s/m
            support_stiffness=80000.0,  # N/m
        )

        spec = DamperInsertSpec(
            damper_type="iRDT",
            def_name=f"IRDT{a.floor}",
            floor_name=f"{a.floor}F",
            node_i=101,
            node_j=201,
            quantity=1,
            mass_kN_s2_m=a.inertance / 1000.0,
            spring_kN_m=a.support_stiffness / 1000.0,
            damping_kN_s_m=a.damping / 1000.0,
            stroke_m=0.3,
        )

        assert spec.def_name == "IRDT3"
        assert spec.floor_name == "3F"
        assert spec.mass_kN_s2_m == pytest.approx(5.0)
        assert spec.damping_kN_s_m == pytest.approx(2.0)
        assert spec.spring_kN_m == pytest.approx(80.0)

    def test_zero_inertance_floors_skipped(self):
        """慣性質量ゼロの層は挿入対象外。"""
        a = IrdtFloorAssignment(
            floor=1,
            mode_amplitude=0.0,
            inter_story_mode=0.0,
            mass_ratio_effective=0.0,
            inertance=0.0,
            damping=0.0,
            support_stiffness=0.0,
        )
        assert a.inertance <= 0


# ===========================================================================
# Qt UI テスト
# ===========================================================================


@pytest.mark.skipif(not _qt_available(), reason="PySide6 not available")
class TestIrdtWizardDialog:
    """IrdtWizardDialog の UI インスタンス化・操作テスト。"""

    @pytest.fixture(autouse=True)
    def _ensure_qapp(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        yield

    def _create_dialog(self, **kwargs):
        from app.ui.irdt_wizard_dialog import IrdtWizardDialog
        return IrdtWizardDialog(**kwargs)

    def test_instantiation(self):
        dlg = self._create_dialog()
        assert dlg is not None
        assert dlg._stack.count() == 5  # 5 steps

    def test_step_titles_count(self):
        from app.ui.irdt_wizard_dialog import IrdtWizardDialog
        assert len(IrdtWizardDialog._STEP_TITLES) == 5

    def test_step_navigation(self):
        dlg = self._create_dialog()
        assert dlg._stack.currentIndex() == 0

        # Step 1 → 2
        dlg._go_next()
        assert dlg._stack.currentIndex() == 1

        # Step 2 → 3
        dlg._go_next()
        assert dlg._stack.currentIndex() == 2

        # Step 3 → 4 (triggers compute)
        dlg._go_next()
        assert dlg._stack.currentIndex() == 3
        assert dlg._placement_plan is not None

        # Step 4 → 5
        dlg._go_next()
        assert dlg._stack.currentIndex() == 4

        # Step 5 → can't go further
        dlg._go_next()
        assert dlg._stack.currentIndex() == 4

        # Go back to Step 4
        dlg._go_back()
        assert dlg._stack.currentIndex() == 3

    def test_step5_node_rows_populated(self):
        dlg = self._create_dialog(floor_masses=[1e6, 1e6, 1e6])

        # Navigate to Step 4 to compute placement
        dlg._go_next()  # 1→2
        dlg._go_next()  # 2→3
        dlg._go_next()  # 3→4 (computes)

        assert dlg._placement_plan is not None
        n_active = sum(
            1 for a in dlg._placement_plan.floor_plan if a.inertance > 0
        )

        # Navigate to Step 5
        dlg._go_next()  # 4→5
        assert dlg._stack.currentIndex() == 4
        assert len(dlg._node_rows) == n_active

    def test_step5_with_base_path(self):
        dlg = self._create_dialog(
            base_s8i_path="C:/test/model.s8i",
        )
        assert dlg._save_base_path.text() == "C:/test/model.s8i"

    def test_saved_case_initially_none(self):
        dlg = self._create_dialog()
        assert dlg.saved_case is None

    def test_node_row_widget(self):
        from app.ui.irdt_wizard_dialog import _NodeRow
        row = _NodeRow(floor=5)
        assert row.floor == 5
        assert row.node_i == 0
        assert row.node_j == 0
        row._node_i.setValue(101)
        row._node_j.setValue(201)
        assert row.node_i == 101
        assert row.node_j == 201

    def test_save_validation_no_base(self):
        """ベースパス未指定で保存エラー。"""
        dlg = self._create_dialog()
        # Navigate to step 5
        dlg._go_next()  # 1→2
        dlg._go_next()  # 2→3
        dlg._go_next()  # 3→4
        dlg._go_next()  # 4→5

        with patch.object(dlg, '_save_base_path') as mock_path:
            mock_path.text.return_value = ""
            # Should show warning but not crash
            with patch('app.ui.irdt_wizard_dialog.QMessageBox') as mock_mb:
                dlg._save_as_snap_case()
                mock_mb.warning.assert_called_once()
