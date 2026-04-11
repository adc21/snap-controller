"""
tests/test_ui_integration.py
=============================

実際のユーザー操作をシミュレートする統合テスト。

ユニットテストでは検出できない問題を捕捉する:
- import 漏れ（QFrame 等）
- 属性の初期化順序バグ
- MainWindow メニューからダイアログを開く際のクラッシュ
- ウィジェット間の接続不整合
"""

from __future__ import annotations

import pytest


def _qt_available() -> bool:
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.fixture(scope="module")
def qapp():
    """モジュール全体で共有する QApplication。"""
    if not _qt_available():
        pytest.skip("PySide6 not available")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# ===================================================================
# 全ダイアログ・ウィジェットのインスタンス化テスト
# （実際のアプリ起動でクラッシュしないことを保証）
# ===================================================================


@pytest.mark.skipif(not _qt_available(), reason="PySide6 not available")
class TestDialogInstantiation:
    """全ダイアログが正常にインスタンス化できることを確認。"""

    def test_optimizer_dialog(self, qapp):
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert dlg is not None

    def test_irdt_wizard_dialog(self, qapp):
        from app.ui.irdt_wizard_dialog import IrdtWizardDialog
        dlg = IrdtWizardDialog()
        assert dlg is not None
        assert dlg._stack.count() == 5

    def test_minimizer_dialog(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(
            n_positions=5,
            position_labels=[f"{i+1}F" for i in range(5)],
        )
        assert dlg is not None

    def test_damper_injector_dialog(self, qapp):
        from app.ui.damper_injector_dialog import DamperInjectorDialog
        dlg = DamperInjectorDialog()
        assert dlg is not None
        assert len(dlg._spec_rows) == 1

    def test_damper_catalog_dialog(self, qapp):
        from app.ui.damper_catalog_dialog import DamperCatalogDialog
        dlg = DamperCatalogDialog()
        assert dlg is not None

    def test_sweep_dialog(self, qapp):
        from app.ui.sweep_dialog import SweepDialog
        dlg = SweepDialog()
        assert dlg is not None
        assert len(dlg._param_rows) >= 1
        assert hasattr(dlg, "_add_param_btn")

    def test_criteria_dialog(self, qapp):
        from app.ui.criteria_dialog import CriteriaDialog
        dlg = CriteriaDialog()
        assert dlg is not None

    def test_case_compare_dialog(self, qapp):
        from app.ui.case_compare_dialog import CaseCompareDialog
        dlg = CaseCompareDialog(cases=[])
        assert dlg is not None

    def test_case_compare_dialog_with_cases(self, qapp):
        from app.ui.case_compare_dialog import CaseCompareDialog
        from app.models import AnalysisCase
        c1 = AnalysisCase(name="A")
        c2 = AnalysisCase(name="B")
        dlg = CaseCompareDialog(cases=[c1, c2])
        assert dlg is not None

    def test_transfer_function_widget(self, qapp):
        from app.ui.transfer_function_widget import TransferFunctionWidget
        w = TransferFunctionWidget()
        assert w is not None

    def test_hysteresis_widget(self, qapp):
        from app.ui.hysteresis_widget import HysteresisWidget
        w = HysteresisWidget()
        assert w is not None

    def test_mode_shape_widget(self, qapp):
        from app.ui.mode_shape_widget import ModeShapeWidget
        w = ModeShapeWidget()
        assert w is not None

    def test_modal_properties_widget(self, qapp):
        from app.ui.modal_properties_widget import ModalPropertiesWidget
        w = ModalPropertiesWidget()
        assert w is not None


# ===================================================================
# ダイアログ内部操作テスト
# （ボタン押下・コンボ選択など実際の操作をシミュレート）
# ===================================================================


@pytest.mark.skipif(not _qt_available(), reason="PySide6 not available")
class TestDialogInteractions:
    """ダイアログ内部のUI操作が正常に機能することを確認。"""

    def test_optimizer_has_all_combos(self, qapp):
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        # 探索手法コンボ
        assert dlg._method_combo.count() >= 3
        # 目的関数コンボ
        assert dlg._obj_combo.count() >= 1
        # ダンパー種別コンボ
        assert dlg._damper_combo.count() >= 1

    def test_optimizer_method_switch(self, qapp):
        """探索手法を切り替えてもクラッシュしないこと。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        for i in range(dlg._method_combo.count()):
            dlg._method_combo.setCurrentIndex(i)

    def test_optimizer_damper_type_switch(self, qapp):
        """全ダンパー種類を切り替えてもクラッシュしないこと。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        for i in range(dlg._damper_combo.count()):
            dlg._damper_combo.setCurrentIndex(i)
            # パラメータウィジェットが更新されること
            assert isinstance(dlg._param_widgets, list)

    def test_optimizer_damper_type_roundtrip(self, qapp):
        """ダンパー種類を往復切替してもウィジェットリークしないこと。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        n = dlg._damper_combo.count()
        for _ in range(3):
            for i in range(n):
                dlg._damper_combo.setCurrentIndex(i)
        # クラッシュせず、パラメータウィジェットが正常な数であること
        dtype = dlg._damper_combo.currentText()
        from app.ui.optimizer_dialog import _DAMPER_PARAM_PRESETS
        expected = len(_DAMPER_PARAM_PRESETS.get(dtype, []))
        assert len(dlg._param_widgets) == expected

    def test_optimizer_composite_toggle(self, qapp):
        """複合目的関数チェックボックスのトグルがクラッシュしないこと。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        # 非表示ダイアログでは isVisible() は常にFalseなので isVisibleTo() を使用
        assert not dlg._composite_panel.isVisibleTo(dlg)
        dlg._composite_check.setChecked(True)
        assert dlg._composite_panel.isVisibleTo(dlg)
        assert not dlg._obj_combo.isEnabled()
        dlg._composite_check.setChecked(False)
        assert not dlg._composite_panel.isVisibleTo(dlg)
        assert dlg._obj_combo.isEnabled()

    def test_optimizer_guide_panel_toggle(self, qapp):
        """ガイドパネルの開閉でクラッシュしないこと、状態が切り替わること。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        initial = dlg._guide_panel.isHidden()
        dlg._toggle_guide_panel()
        assert dlg._guide_panel.isHidden() != initial
        dlg._toggle_guide_panel()
        assert dlg._guide_panel.isHidden() == initial

    def test_optimizer_build_config(self, qapp):
        """_build_config()が正常にOptimizationConfigを返すこと。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        config = dlg._build_config()
        assert config.parameters is not None
        assert len(config.parameters) >= 1
        assert config.method in ("grid", "random", "bayesian", "ga", "sa")
        assert config.damper_type == dlg._damper_combo.currentText()

    def test_optimizer_estimate_grid_runs(self, qapp):
        """推定試行数が正の整数であること。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        n = dlg._estimate_grid_runs()
        assert isinstance(n, int)
        assert n >= 1

    def test_optimizer_iter_spin_enabled_by_method(self, qapp):
        """反復数スピンボックスがメソッドに応じて有効/無効になること。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        # グリッドサーチ: 無効
        dlg._method_combo.setCurrentIndex(0)
        assert not dlg._iter_spin.isEnabled()
        # ランダムサーチ: 有効
        dlg._method_combo.setCurrentIndex(1)
        assert dlg._iter_spin.isEnabled()

    def test_optimizer_initial_button_states(self, qapp):
        """初期状態で各ボタンの有効/無効が正しいこと。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert dlg._run_btn.isEnabled()
        assert not dlg._cancel_btn.isEnabled()
        assert not dlg._apply_btn.isEnabled()
        assert not dlg._export_csv_btn.isEnabled()
        assert not dlg._best_summary_card.isVisibleTo(dlg)

    def test_optimizer_clear_layout_safety(self, qapp):
        """_clear_layout静的メソッドが空レイアウトでクラッシュしないこと。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from PySide6.QtWidgets import QVBoxLayout, QWidget
        layout = QVBoxLayout()
        # 空レイアウトのクリア
        OptimizerDialog._clear_layout(layout)
        assert layout.count() == 0
        # ウィジェット入りレイアウトのクリア
        layout.addWidget(QWidget())
        layout.addWidget(QWidget())
        assert layout.count() == 2
        OptimizerDialog._clear_layout(layout)
        assert layout.count() == 0

    def test_minimizer_strategy_switch(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(
            n_positions=5,
            position_labels=[f"{i+1}F" for i in range(5)],
        )
        assert dlg._combo_strategy.count() >= 2
        for i in range(dlg._combo_strategy.count()):
            dlg._combo_strategy.setCurrentIndex(i)

    def test_sweep_add_remove_params(self, qapp):
        from app.ui.sweep_dialog import SweepDialog
        dlg = SweepDialog()
        initial = len(dlg._param_rows)
        dlg._on_add_param_clicked()
        assert len(dlg._param_rows) == initial + 1
        dlg._on_add_param_clicked()
        assert len(dlg._param_rows) == initial + 2

    def test_injector_add_remove_rows(self, qapp):
        from app.ui.damper_injector_dialog import DamperInjectorDialog
        dlg = DamperInjectorDialog()
        assert len(dlg._spec_rows) == 1
        dlg._add_spec_row()
        assert len(dlg._spec_rows) == 2
        dlg._remove_last_spec_row()
        assert len(dlg._spec_rows) == 1

    def test_injector_spec_type_switch(self, qapp):
        """iRDT/iOD 切替でクラッシュしないこと。"""
        from app.ui.damper_injector_dialog import DamperInjectorDialog
        dlg = DamperInjectorDialog()
        row = dlg._spec_rows[0]
        row._type_combo.setCurrentText("iOD")
        assert row._spring.value() == 0.0
        row._type_combo.setCurrentText("iRDT")

    def test_irdt_wizard_full_navigation(self, qapp):
        """5ステップ全てを往復できること。"""
        from app.ui.irdt_wizard_dialog import IrdtWizardDialog
        dlg = IrdtWizardDialog(floor_masses=[1e6, 1e6, 1e6])
        # Forward
        for i in range(4):
            dlg._go_next()
        assert dlg._stack.currentIndex() == 4
        # Backward
        for i in range(4):
            dlg._go_back()
        assert dlg._stack.currentIndex() == 0
        # Forward again — recompute should not crash
        for i in range(4):
            dlg._go_next()
        assert dlg._placement_plan is not None
        assert len(dlg._node_rows) > 0

    def test_irdt_wizard_mu_slider_sync(self, qapp):
        """μスライダーとスピンボックスが同期すること。"""
        from app.ui.irdt_wizard_dialog import IrdtWizardDialog
        dlg = IrdtWizardDialog()
        dlg._go_next()  # Step 2
        dlg._mu_slider.setValue(50)  # 0.050
        assert dlg._mu_spin.value() == pytest.approx(0.050, abs=0.001)
        dlg._mu_spin.setValue(0.100)
        assert dlg._mu_slider.value() == 100


# ===================================================================
# MainWindow 統合テスト
# （メニューから各機能にアクセスできることを確認）
# ===================================================================


@pytest.mark.skipif(not _qt_available(), reason="PySide6 not available")
class TestMainWindowIntegration:
    """MainWindow のメニュー・タブ統合が正常に機能することを確認。"""

    @pytest.fixture()
    def main_window(self, qapp):
        from app.ui.main_window import MainWindow
        return MainWindow()

    def test_instantiation(self, main_window):
        assert main_window is not None

    def test_analysis_menu_exists(self, main_window):
        """解析メニューが存在し、最適化関連のアクションが全てあること。"""
        mb = main_window.menuBar()
        all_actions = []
        for action in mb.actions():
            menu = action.menu()
            if menu:
                for sub in menu.actions():
                    if not sub.isSeparator():
                        all_actions.append(sub.text())

        # 必須メニュー項目の確認
        action_texts = " ".join(all_actions)
        assert "最適化" in action_texts or "O)" in action_texts
        assert "iRDT" in action_texts
        assert "最小化" in action_texts or "M)" in action_texts
        assert "挿入" in action_texts or "J)" in action_texts
        assert "スイープ" in action_texts or "W)" in action_texts
        assert "カタログ" in action_texts or "K)" in action_texts

    def test_all_menu_actions_enabled(self, main_window):
        """全メニューアクションが有効であること。"""
        mb = main_window.menuBar()
        disabled = []
        for action in mb.actions():
            menu = action.menu()
            if menu:
                for sub in menu.actions():
                    if not sub.isSeparator() and not sub.isEnabled():
                        disabled.append(sub.text())
        assert disabled == [], f"Disabled actions: {disabled}"

    def test_analysis_widgets_in_tabs(self, main_window):
        """解析ウィジェットがタブに統合されていること。"""
        assert hasattr(main_window, "_transfer_function_widget")
        assert hasattr(main_window, "_mode_shape_widget")
        assert hasattr(main_window, "_hysteresis_widget")
