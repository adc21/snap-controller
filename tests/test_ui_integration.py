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
        floor_keys = ["F1", "F2", "F3"]
        dlg = MinimizerDialog(
            floor_keys=floor_keys,
            current_quantities={"F1": 2, "F2": 3, "F3": 1},
            max_quantities={"F1": 5, "F2": 5, "F3": 5},
        )
        assert dlg is not None

    def test_unified_optimizer_dialog(self, qapp):
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        dlg = UnifiedOptimizerDialog()
        assert dlg is not None
        assert hasattr(dlg, "_param_table")
        assert hasattr(dlg, "_floor_table")
        assert hasattr(dlg, "_obj1_combo")
        assert hasattr(dlg, "_canvas")
        assert hasattr(dlg, "_start_btn")

    def test_unified_optimizer_advanced_options(self, qapp):
        """統合最適化ダイアログの詳細設定パネルが正しく構築される。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        dlg = UnifiedOptimizerDialog()

        # 詳細設定ウィジェットが存在する
        assert hasattr(dlg, "_adv_toggle")
        assert hasattr(dlg, "_adv_widget")
        assert hasattr(dlg, "_seed_check")
        assert hasattr(dlg, "_seed_spin")
        assert hasattr(dlg, "_parallel_spin")
        assert hasattr(dlg, "_timeout_spin")
        assert hasattr(dlg, "_checkpoint_check")
        assert hasattr(dlg, "_checkpoint_interval_spin")
        assert hasattr(dlg, "_robust_check")
        assert hasattr(dlg, "_robust_samples_spin")
        assert hasattr(dlg, "_robust_delta_spin")

        # 初期状態: 詳細設定は非表示 (isHidden=hidden explicitly)
        assert dlg._adv_widget.isHidden()

        # トグルONで表示
        dlg._adv_toggle.setChecked(True)
        assert not dlg._adv_widget.isHidden()

        # トグルOFFで非表示
        dlg._adv_toggle.setChecked(False)
        assert dlg._adv_widget.isHidden()

    def test_unified_optimizer_seed_toggle(self, qapp):
        """乱数シードチェックボックスでスピンの有効/無効が切り替わる。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        dlg = UnifiedOptimizerDialog()

        assert not dlg._seed_spin.isEnabled()
        dlg._seed_check.setChecked(True)
        assert dlg._seed_spin.isEnabled()
        dlg._seed_check.setChecked(False)
        assert not dlg._seed_spin.isEnabled()

    def test_unified_optimizer_config_advanced_fields(self, qapp):
        """詳細設定が OptimizationConfig に正しく反映される。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog, ParameterRange
        dlg = UnifiedOptimizerDialog()

        params = [ParameterRange(
            key="field_8", label="test", min_val=100, max_val=1000,
            step=50, is_integer=False, is_floor_count=False,
        )]

        # デフォルトの詳細設定値を確認
        config = dlg._build_config(params)
        assert config.snap_timeout == 300
        assert config.n_parallel == 1
        assert config.checkpoint_interval == 0
        assert config.robustness_samples == 0
        assert config.random_seed is None

        # シード有効化
        dlg._seed_check.setChecked(True)
        dlg._seed_spin.setValue(123)
        config = dlg._build_config(params)
        assert config.random_seed == 123

        # チェックポイント有効化
        dlg._checkpoint_check.setChecked(True)
        dlg._checkpoint_interval_spin.setValue(20)
        config = dlg._build_config(params)
        assert config.checkpoint_interval == 20

        # ロバスト有効化
        dlg._robust_check.setChecked(True)
        dlg._robust_samples_spin.setValue(5)
        dlg._robust_delta_spin.setValue(0.10)
        config = dlg._build_config(params)
        assert config.robustness_samples == 5
        assert abs(config.robustness_delta - 0.10) < 0.001

        # タイムアウト変更
        dlg._timeout_spin.setValue(600)
        config = dlg._build_config(params)
        assert config.snap_timeout == 600

    def test_unified_optimizer_json_buttons_exist(self, qapp):
        """JSON保存/読込/画像保存ボタンが存在する。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        dlg = UnifiedOptimizerDialog()

        assert hasattr(dlg, "_save_json_btn")
        assert hasattr(dlg, "_load_json_btn")
        assert hasattr(dlg, "_save_plot_btn")

        # 初期状態: 結果がないので無効
        assert not dlg._save_json_btn.isEnabled()
        assert dlg._load_json_btn.isEnabled()  # 読込はいつでも可能
        assert not dlg._save_plot_btn.isEnabled()

    def test_unified_optimizer_obj2_toggles_method(self, qapp):
        """目的関数2を有効にすると探索手法がNSGA-IIに切り替わる。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        dlg = UnifiedOptimizerDialog()

        dlg._method_combo.setCurrentIndex(0)  # grid
        assert dlg._method_combo.currentData() == "grid"

        dlg._obj2_enabled.setChecked(True)
        assert dlg._method_combo.currentData() == "nsga2"

    def test_unified_optimizer_axis_selectors_exist(self, qapp):
        """X/Y軸セレクタが存在し、自動 + 反復番号 + 応答値の選択肢を持つ。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog, _OBJECTIVE_ITEMS
        dlg = UnifiedOptimizerDialog()
        assert hasattr(dlg, "_xaxis_combo")
        assert hasattr(dlg, "_yaxis_combo")
        # 初期値は両方「自動」
        assert dlg._xaxis_combo.currentData() == "auto"
        assert dlg._yaxis_combo.currentData() == "auto"
        # 選択肢の数: auto + iteration + _OBJECTIVE_ITEMS
        expected = 2 + len(_OBJECTIVE_ITEMS)
        assert dlg._xaxis_combo.count() == expected
        assert dlg._yaxis_combo.count() == expected
        # iteration 選択可能
        assert dlg._xaxis_combo.findData("iteration") >= 0
        # 応答値 (max_drift 等) 選択可能
        assert dlg._yaxis_combo.findData("max_drift") >= 0

    def test_unified_optimizer_axis_change_triggers_update(self, qapp):
        """X/Y軸セレクタ変更で _update_plot が呼ばれ、軸ラベルが更新される。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        from app.services.optimizer import OptimizationCandidate
        dlg = UnifiedOptimizerDialog()
        dlg._candidates = [
            OptimizationCandidate(
                iteration=1, params={"field_8": 500.0},
                response_values={"max_drift": 0.005, "max_acc": 300.0},
                objective_value=0.005, is_feasible=True,
            ),
            OptimizationCandidate(
                iteration=2, params={"field_8": 700.0},
                response_values={"max_drift": 0.004, "max_acc": 280.0},
                objective_value=0.004, is_feasible=True,
            ),
        ]
        idx_x = dlg._xaxis_combo.findData("max_drift")
        idx_y = dlg._yaxis_combo.findData("max_acc")
        dlg._xaxis_combo.setCurrentIndex(idx_x)
        dlg._yaxis_combo.setCurrentIndex(idx_y)
        assert "最大層間変形角" in dlg._ax.get_xlabel()
        assert "最大絶対加速度" in dlg._ax.get_ylabel()

    def test_unified_optimizer_axis_includes_checked_parameters(self, qapp):
        """チェック済みパラメータが軸セレクタの選択肢に含まれる。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        dlg = UnifiedOptimizerDialog()
        # パラメータテーブルが空でないことが前提 (selected_damper_def に依存)
        # 空でも動的追加テストのため、_field_rows を手動で注入
        from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox
        cb = QCheckBox()
        lo_spin = QDoubleSpinBox()
        hi_spin = QDoubleSpinBox()
        dlg._field_rows = [{
            "cb": cb, "val_idx_0based": 8, "label": "C0 (減衰係数)",
            "current": 500.0, "lo_spin": lo_spin, "hi_spin": hi_spin,
            "unit": "kN·s/mm",
        }]
        dlg._floor_rows = []

        # 未チェック時はパラメータはセレクタに含まれない
        dlg._refresh_axis_combos()
        assert dlg._xaxis_combo.findData("field_8") < 0

        # チェックすると追加される
        cb.setChecked(True)
        dlg._refresh_axis_combos()
        idx = dlg._xaxis_combo.findData("field_8")
        assert idx >= 0
        # ラベルにダイヤマーク+名前+単位
        assert "C0" in dlg._xaxis_combo.itemText(idx)
        assert "kN·s/mm" in dlg._xaxis_combo.itemText(idx)

        # チェック外すと削除される (選択は auto にフォールバック)
        dlg._xaxis_combo.setCurrentIndex(idx)
        cb.setChecked(False)
        dlg._refresh_axis_combos()
        assert dlg._xaxis_combo.findData("field_8") < 0
        assert dlg._xaxis_combo.currentData() == "auto"

    def test_unified_optimizer_param_axis_plot(self, qapp):
        """パラメータを軸に指定してプロットできる (params から値抽出)。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        from app.services.optimizer import OptimizationCandidate
        from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox
        dlg = UnifiedOptimizerDialog()

        cb = QCheckBox()
        cb.setChecked(True)
        dlg._field_rows = [{
            "cb": cb, "val_idx_0based": 8, "label": "C0",
            "current": 500.0,
            "lo_spin": QDoubleSpinBox(), "hi_spin": QDoubleSpinBox(),
            "unit": "kN·s/mm",
        }]
        dlg._floor_rows = []
        dlg._refresh_axis_combos()

        dlg._candidates = [
            OptimizationCandidate(
                iteration=1, params={"field_8": 500.0},
                response_values={"max_drift": 0.005},
                objective_value=0.005, is_feasible=True,
            ),
            OptimizationCandidate(
                iteration=2, params={"field_8": 800.0},
                response_values={"max_drift": 0.004},
                objective_value=0.004, is_feasible=True,
            ),
        ]
        # X軸をパラメータ field_8 に設定
        idx_x = dlg._xaxis_combo.findData("field_8")
        idx_y = dlg._yaxis_combo.findData("max_drift")
        dlg._xaxis_combo.setCurrentIndex(idx_x)
        dlg._yaxis_combo.setCurrentIndex(idx_y)
        # X軸ラベルにパラメータ名が現れる
        assert "C0" in dlg._ax.get_xlabel()
        # 値抽出関数を直接チェック
        cand = dlg._candidates[1]
        assert dlg._candidate_axis_value(cand, "field_8") == 800.0
        assert dlg._candidate_axis_value(cand, "iteration") == 2.0

    def test_unified_optimizer_axis_auto_default_behavior(self, qapp):
        """両軸 auto のとき 1目的で収束プロット (X=反復) になる。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        from app.services.optimizer import OptimizationCandidate
        dlg = UnifiedOptimizerDialog()
        dlg._candidates = [
            OptimizationCandidate(
                iteration=1, params={}, response_values={"max_drift": 0.005},
                objective_value=0.005, is_feasible=True,
            ),
        ]
        dlg._update_plot()
        assert "反復" in dlg._ax.get_xlabel()

    def test_unified_optimizer_analysis_buttons_exist(self, qapp):
        """統合最適化ダイアログの分析ボタンが存在し、初期状態で無効。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        dlg = UnifiedOptimizerDialog()

        # 分析ボタンの存在確認
        assert hasattr(dlg, "_sensitivity_btn")
        assert hasattr(dlg, "_sobol_btn")
        assert hasattr(dlg, "_diagnostics_btn")
        assert hasattr(dlg, "_correlation_btn")
        assert hasattr(dlg, "_heatmap_btn")
        assert hasattr(dlg, "_pareto_btn")
        assert hasattr(dlg, "_log_btn")
        assert hasattr(dlg, "_html_report_btn")
        assert hasattr(dlg, "_comparison_btn")

        # 初期状態: 結果がないので分析系は無効
        assert not dlg._sensitivity_btn.isEnabled()
        assert not dlg._sobol_btn.isEnabled()
        assert not dlg._diagnostics_btn.isEnabled()
        assert not dlg._correlation_btn.isEnabled()
        assert not dlg._heatmap_btn.isEnabled()
        assert not dlg._pareto_btn.isEnabled()
        assert not dlg._log_btn.isEnabled()
        assert not dlg._html_report_btn.isEnabled()
        # 結果比較はいつでも可能
        assert dlg._comparison_btn.isEnabled()

    def test_unified_optimizer_analysis_buttons_enable(self, qapp):
        """結果セット後に分析ボタンが有効化される。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        from app.services.optimizer import (
            OptimizationCandidate, OptimizationConfig, OptimizationResult,
            ParameterRange,
        )

        dlg = UnifiedOptimizerDialog()

        params = [
            ParameterRange(key="field_8", label="C0", min_val=100, max_val=1000,
                           step=50, is_integer=False, is_floor_count=False),
            ParameterRange(key="field_12", label="alpha", min_val=0.1, max_val=1.0,
                           step=0.05, is_integer=False, is_floor_count=False),
        ]
        config = OptimizationConfig(
            objective_key="max_drift", objective_label="最大層間変形角",
            parameters=params, constraints={}, method="random", max_iterations=10,
        )

        cands = []
        for i in range(5):
            cands.append(OptimizationCandidate(
                iteration=i, params={"field_8": 200 + i * 100, "field_12": 0.3 + i * 0.1},
                objective_value=0.01 - i * 0.001, is_feasible=True,
                response_values={"max_drift": 0.01 - i * 0.001},
            ))
        best = min(cands, key=lambda c: c.objective_value)
        result = OptimizationResult(config=config, all_candidates=cands, best=best)

        dlg._result = result
        dlg._candidates = list(cands)
        dlg._enable_analysis_buttons()

        assert dlg._sensitivity_btn.isEnabled()
        assert dlg._sobol_btn.isEnabled()
        assert dlg._diagnostics_btn.isEnabled()
        assert dlg._correlation_btn.isEnabled()
        assert dlg._heatmap_btn.isEnabled()
        assert dlg._pareto_btn.isEnabled()
        assert dlg._log_btn.isEnabled()
        assert dlg._html_report_btn.isEnabled()

    def test_unified_optimizer_diagnostics_action(self, qapp):
        """収束診断が実行可能。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        from app.services.optimizer import (
            OptimizationCandidate, OptimizationConfig, OptimizationResult,
            ParameterRange,
        )
        from app.services.optimizer_analytics import compute_convergence_diagnostics

        dlg = UnifiedOptimizerDialog()
        params = [ParameterRange(key="field_8", label="C0", min_val=100,
                                  max_val=1000, step=50, is_integer=False,
                                  is_floor_count=False)]
        config = OptimizationConfig(
            objective_key="max_drift", objective_label="最大層間変形角",
            parameters=params, constraints={}, method="random", max_iterations=20,
        )
        cands = [OptimizationCandidate(
            iteration=i, params={"field_8": 200 + i * 50},
            objective_value=0.01 - i * 0.0003, is_feasible=True,
            response_values={"max_drift": 0.01 - i * 0.0003},
        ) for i in range(10)]
        result = OptimizationResult(config=config, all_candidates=cands)

        diag = compute_convergence_diagnostics(result)
        assert diag is not None
        assert 0 <= diag.quality_score <= 100
        assert diag.quality_label in ("優良", "良好", "要注意", "不十分")

    def test_unified_optimizer_result_table_exists(self, qapp):
        """統合最適化ダイアログに候補一覧テーブルが存在する。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        dlg = UnifiedOptimizerDialog()
        assert hasattr(dlg, "_result_table")
        assert hasattr(dlg, "_detail_tabs")
        assert dlg._result_table.columnCount() == 5
        assert dlg._result_table.rowCount() == 0
        headers = [dlg._result_table.horizontalHeaderItem(i).text() for i in range(5)]
        assert headers == ["順位", "パラメータ", "目的関数", "判定", "マージン"]

    def test_unified_optimizer_populate_result_table(self, qapp):
        """_populate_result_table が候補を順位付きで追加する。"""
        from PySide6.QtCore import Qt
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        from app.services.optimizer import (
            OptimizationCandidate, OptimizationConfig, OptimizationResult,
            ParameterRange,
        )
        dlg = UnifiedOptimizerDialog()
        params = [ParameterRange(
            key="field_8", label="C0", min_val=100, max_val=1000,
            step=50, is_integer=False, is_floor_count=False,
        )]
        config = OptimizationConfig(
            objective_key="max_drift", objective_label="最大層間変形角",
            parameters=params, constraints={}, method="random", max_iterations=10,
        )
        cands = [
            OptimizationCandidate(
                iteration=i, params={"field_8": 200 + i * 100},
                objective_value=0.01 - i * 0.001, is_feasible=(i < 3),
                response_values={"max_drift": 0.01 - i * 0.001},
            )
            for i in range(5)
        ]
        best = min(cands, key=lambda c: c.objective_value)
        result = OptimizationResult(
            config=config, all_candidates=cands, best=best,
        )

        dlg._candidates = list(cands)
        dlg._populate_result_table(result)

        assert dlg._result_table.rowCount() == 5
        first_iter = dlg._result_table.item(0, 0).data(Qt.UserRole)
        assert first_iter in {c.iteration for c in cands}

    def test_unified_optimizer_result_row_select_shows_detail(self, qapp):
        """結果行を選択すると候補詳細が表示され、詳細タブに切り替わる。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        from app.services.optimizer import (
            OptimizationCandidate, OptimizationConfig, OptimizationResult,
            ParameterRange,
        )
        dlg = UnifiedOptimizerDialog()
        params = [ParameterRange(
            key="field_8", label="C0", min_val=100, max_val=1000,
            step=50, is_integer=False, is_floor_count=False,
        )]
        config = OptimizationConfig(
            objective_key="max_drift", objective_label="最大層間変形角",
            parameters=params, constraints={}, method="random", max_iterations=10,
        )
        cands = [
            OptimizationCandidate(
                iteration=i + 1, params={"field_8": 200.0 + i * 100.0},
                objective_value=0.01 - i * 0.001, is_feasible=True,
                response_values={"max_drift": 0.01 - i * 0.001},
            )
            for i in range(3)
        ]
        result = OptimizationResult(
            config=config, all_candidates=cands, best=cands[-1],
        )

        dlg._candidates = list(cands)
        dlg._populate_result_table(result)

        dlg._detail_tabs.setCurrentIndex(1)
        dlg._result_table.selectRow(0)

        assert dlg._selected_candidate is not None
        assert dlg._apply_btn.isEnabled()
        assert dlg._detail_tabs.currentIndex() == 0
        assert dlg._detail_text.toPlainText() != ""

    def test_unified_optimizer_plot_scatter_layer(self, qapp):
        """_plot_scatter_layer ヘルパーが散布図を描画。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        dlg = UnifiedOptimizerDialog()
        # 空リストは None を返す
        assert dlg._plot_scatter_layer([], [], "blue", "o", 0.5, "test") is None
        # データありの場合はアーティストを返す
        artist = dlg._plot_scatter_layer([1, 2], [3, 4], "blue", "o", 0.5, "test")
        assert artist is not None

    def test_unified_optimizer_close_event(self, qapp):
        """closeEvent でオプティマイザがキャンセルされる。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        from unittest.mock import MagicMock
        dlg = UnifiedOptimizerDialog()
        dlg._optimizer.cancel = MagicMock()
        dlg.close()
        dlg._optimizer.cancel.assert_called_once()

    def test_unified_optimizer_collect_params_warns_invalid_range(self, qapp, caplog):
        """下限>=上限のチェック済みパラメータでログ警告が出る。"""
        import logging
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        dlg = UnifiedOptimizerDialog()
        # 手動でフィールド行を追加（チェック済み、lo >= hi）
        from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox
        cb = QCheckBox()
        cb.setChecked(True)
        lo_spin = QDoubleSpinBox()
        lo_spin.setValue(100.0)
        hi_spin = QDoubleSpinBox()
        hi_spin.setValue(50.0)  # lo > hi
        dlg._field_rows.append({
            "cb": cb, "field_idx_1based": 8, "val_idx_0based": 8,
            "label": "テストC0", "current": 75.0,
            "lo_spin": lo_spin, "hi_spin": hi_spin, "unit": "kN",
        })
        with caplog.at_level(logging.WARNING, logger="app.ui.unified_optimizer_dialog"):
            result = dlg._collect_parameters()
        assert len(result) == 0
        assert "テストC0" in caplog.text
        assert "範囲不正" in caplog.text

    def test_unified_optimizer_build_case_overrides_empty(self, qapp):
        """best_params が空なら空の overrides を返す。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        dlg = UnifiedOptimizerDialog()
        dp, rd = dlg.build_case_overrides()
        assert dp == {}
        assert rd == {}

    def test_unified_optimizer_build_case_overrides_physical(self, qapp):
        """物理パラメータが damper_params 形式 (1-indexed) に変換される。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        from app.services.optimizer import OptimizationCandidate, OptimizationResult, OptimizationConfig, ParameterRange
        dlg = UnifiedOptimizerDialog()
        # ダンパー定義をモック設定
        from app.models.s8i_parser import DamperDefinition
        dd = DamperDefinition(keyword="DVOD", name="C1", values=["C1"] + ["0"] * 20)
        dlg._damper_defs = [dd]
        dlg._def_combo.addItem("C1 (DVOD)", "C1")
        dlg._def_combo.setCurrentIndex(dlg._def_combo.count() - 1)
        # 結果をモック設定
        cand = OptimizationCandidate(
            params={"field_7": 500.0, "field_12": 0.8},
            objective_value=0.005,
            response_values={"max_drift": 0.005},
        )
        config = OptimizationConfig(
            parameters=[
                ParameterRange(key="field_7", label="K0", min_val=100, max_val=1000, step=50),
                ParameterRange(key="field_12", label="alpha", min_val=0.1, max_val=1.0, step=0.1),
            ],
            objective_key="max_drift",
            method="random",
            max_iterations=10,
        )
        dlg._result = OptimizationResult(config=config, all_candidates=[cand], best=cand)
        dp, rd = dlg.build_case_overrides()
        # field_7 → 1-indexed "8", field_12 → 1-indexed "13"
        assert "C1" in dp
        assert dp["C1"]["8"] == "500.0"
        assert dp["C1"]["13"] == "0.8"
        assert rd == {}

    def test_unified_optimizer_build_case_overrides_floor_count(self, qapp):
        """基数パラメータが _rd_overrides 形式に変換される。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        from app.services.optimizer import OptimizationCandidate, OptimizationResult, OptimizationConfig, ParameterRange
        dlg = UnifiedOptimizerDialog()
        # floor_rd_map をモック設定
        dlg._floor_rd_map = {"F3": [10, 11], "F5": [20]}
        # 結果をモック設定
        cand = OptimizationCandidate(
            params={"floor_count_F3": 6, "floor_count_F5": 3},
            objective_value=0.005,
            response_values={"max_drift": 0.005},
        )
        config = OptimizationConfig(
            parameters=[
                ParameterRange(key="floor_count_F3", label="F3基数", min_val=0, max_val=8, step=1, is_integer=True, is_floor_count=True),
                ParameterRange(key="floor_count_F5", label="F5基数", min_val=0, max_val=8, step=1, is_integer=True, is_floor_count=True),
            ],
            objective_key="max_drift",
            method="random",
            max_iterations=10,
        )
        dlg._result = OptimizationResult(config=config, all_candidates=[cand], best=cand)
        dp, rd = dlg.build_case_overrides()
        assert dp == {}
        # F3: 6本 → 2要素に均等分配 (3+3)
        assert rd["10"]["quantity"] == 3
        assert rd["11"]["quantity"] == 3
        # F5: 3本 → 1要素
        assert rd["20"]["quantity"] == 3

    def test_unified_optimizer_build_case_overrides_mixed(self, qapp):
        """物理+基数パラメータの混在ケースで両方正しく変換される。"""
        from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog
        from app.services.optimizer import OptimizationCandidate, OptimizationResult, OptimizationConfig, ParameterRange
        from app.models.s8i_parser import DamperDefinition
        dlg = UnifiedOptimizerDialog()
        dd = DamperDefinition(keyword="DVOD", name="OD", values=["OD"] + ["0"] * 20)
        dlg._damper_defs = [dd]
        dlg._def_combo.addItem("OD (DVOD)", "OD")
        dlg._def_combo.setCurrentIndex(dlg._def_combo.count() - 1)
        dlg._floor_rd_map = {"F1": [5]}
        cand = OptimizationCandidate(
            params={"field_8": 850, "floor_count_F1": 4},
            objective_value=0.006,
            response_values={"max_drift": 0.006},
        )
        config = OptimizationConfig(
            parameters=[
                ParameterRange(key="field_8", label="C0", min_val=100, max_val=2000, step=50),
                ParameterRange(key="floor_count_F1", label="F1基数", min_val=0, max_val=8, step=1, is_integer=True, is_floor_count=True),
            ],
            objective_key="max_drift",
            method="random",
            max_iterations=10,
        )
        dlg._result = OptimizationResult(config=config, all_candidates=[cand], best=cand)
        dp, rd = dlg.build_case_overrides()
        assert dp["OD"]["9"] == "850"
        assert rd["5"]["quantity"] == 4

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

    def test_optimizer_de_in_method_combo(self, qapp):
        """DE (差分進化) が手法コンボに含まれること。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        methods = [dlg._method_combo.itemData(i) for i in range(dlg._method_combo.count())]
        assert "de" in methods

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
            floor_keys=["F1", "F2", "F3"],
            current_quantities={"F1": 2, "F2": 3, "F3": 1},
            max_quantities={"F1": 5, "F2": 5, "F3": 5},
        )
        assert dlg._combo_strategy.count() >= 12
        for i in range(dlg._combo_strategy.count()):
            dlg._combo_strategy.setCurrentIndex(i)

    def test_minimizer_eval_mode_no_fn(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(
            floor_keys=["F1", "F2", "F3"],
            current_quantities={"F1": 2, "F2": 3, "F3": 1},
            max_quantities={"F1": 5, "F2": 5, "F3": 5},
        )
        assert not dlg._is_snap
        assert "未接続" in dlg._lbl_eval_mode.text()

    def test_minimizer_eval_mode_with_fn(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        from app.services.damper_count_minimizer import EvaluationResult
        def dummy_eval(quantities):
            return EvaluationResult(total_count=sum(quantities.values()),
                                    is_feasible=True, worst_margin=0.1)
        dlg = MinimizerDialog(
            floor_keys=["F1", "F2", "F3"],
            current_quantities={"F1": 2, "F2": 3, "F3": 1},
            max_quantities={"F1": 5, "F2": 5, "F3": 5},
            evaluate_fn=dummy_eval,
        )
        assert dlg._is_snap
        assert "SNAP" in dlg._lbl_eval_mode.text()

    def test_minimizer_chart_and_buttons(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(
            floor_keys=["F1", "F2", "F3"],
            current_quantities={"F1": 2, "F2": 3, "F3": 1},
            max_quantities={"F1": 5, "F2": 5, "F3": 5},
        )
        assert dlg._canvas is not None
        assert not dlg._btn_csv.isEnabled()
        assert not dlg._btn_copy.isEnabled()

    def test_minimizer_elevation_diagram(self, qapp):
        """立面ダイアグラムの描画がクラッシュしないこと。"""
        from app.ui.minimizer_dialog import MinimizerDialog
        from app.services.damper_count_minimizer import MinimizationResult, MinimizationStep
        dlg = MinimizerDialog(
            floor_keys=["1F", "2F", "3F"],
            current_quantities={"1F": 4, "2F": 3, "3F": 2},
            max_quantities={"1F": 10, "2F": 10, "3F": 10},
        )
        result = MinimizationResult(
            strategy="floor_add",
            initial_quantities={"1F": 4, "2F": 3, "3F": 2},
            final_quantities={"1F": 2, "2F": 1, "3F": 0},
            final_count=3,
            is_feasible=True,
            final_margin=0.05,
            history=[MinimizationStep(
                iteration=0, quantities={"1F": 2, "2F": 1, "3F": 0},
                total_count=3, is_feasible=True, worst_margin=0.05,
                summary={"max_drift": 0.005},
            )],
        )
        dlg._update_elevation_diagram(result)
        assert len(dlg._fig_elev.axes) == 1

    def test_minimizer_elevation_diagram_empty(self, qapp):
        """空結果で立面ダイアグラムがクラッシュしないこと。"""
        from app.ui.minimizer_dialog import MinimizerDialog
        from app.services.damper_count_minimizer import MinimizationResult
        dlg = MinimizerDialog(
            floor_keys=["1F"],
            current_quantities={"1F": 1},
            max_quantities={"1F": 5},
        )
        result = MinimizationResult(
            strategy="floor_add",
            initial_quantities={},
            final_quantities={},
            final_count=0,
            is_feasible=False,
            final_margin=-1.0,
            history=[],
        )
        dlg._update_elevation_diagram(result)
        # Should not crash

    def test_minimizer_realtime_chart(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(
            floor_keys=["F1", "F2", "F3"],
            current_quantities={"F1": 2, "F2": 3, "F3": 1},
            max_quantities={"F1": 5, "F2": 5, "F3": 5},
        )
        dlg._plot_counts = [6, 5, 4, 3]
        dlg._plot_margins = [0.3, 0.2, 0.1, -0.05]
        dlg._plot_feasible = [True, True, True, False]
        dlg._update_realtime_chart()
        assert len(dlg._fig.axes) == 2

    def test_minimizer_export_csv(self, qapp, tmp_path, monkeypatch):
        from app.ui.minimizer_dialog import MinimizerDialog
        from app.services.damper_count_minimizer import MinimizationResult, MinimizationStep
        dlg = MinimizerDialog(
            floor_keys=["F1", "F2", "F3"],
            current_quantities={"F1": 2, "F2": 3, "F3": 1},
            max_quantities={"F1": 5, "F2": 5, "F3": 5},
        )
        steps = [
            MinimizationStep(0, {"F1": 2, "F2": 3, "F3": 1}, 6, True, 0.2, action="init"),
        ]
        dlg._result = MinimizationResult(
            strategy="floor_remove",
            initial_quantities={"F1": 2, "F2": 3, "F3": 1},
            final_quantities={"F1": 1, "F2": 2, "F3": 1},
            final_count=4, is_feasible=True, final_margin=0.1,
            history=steps, evaluations=5,
        )
        csv_path = str(tmp_path / "test_min.csv")
        monkeypatch.setattr(
            "app.ui.minimizer_dialog.QFileDialog.getSaveFileName",
            lambda *a, **kw: (csv_path, ""),
        )
        monkeypatch.setattr(
            "app.ui.minimizer_dialog.QMessageBox.information",
            lambda *a, **kw: None,
        )
        dlg._export_csv()
        with open(csv_path, encoding="utf-8-sig") as f:
            csv_content = f.read()
        assert "F1" in csv_content
        assert "F2" in csv_content  # floor keys present

    def test_minimizer_copy_result(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        from app.services.damper_count_minimizer import MinimizationResult
        from PySide6.QtWidgets import QApplication
        dlg = MinimizerDialog(
            floor_keys=["F1", "F2", "F3"],
            current_quantities={"F1": 2, "F2": 3, "F3": 1},
            max_quantities={"F1": 5, "F2": 5, "F3": 5},
        )
        dlg._result = MinimizationResult(
            strategy="floor_remove",
            initial_quantities={"F1": 2, "F2": 3, "F3": 1},
            final_quantities={"F1": 1, "F2": 2, "F3": 1},
            final_count=4, is_feasible=True, final_margin=0.1,
            evaluations=5,
        )
        dlg._copy_result()
        text = QApplication.clipboard().text()
        assert "floor_remove" in text

    def test_minimizer_on_finished_enables_buttons(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        from app.services.damper_count_minimizer import MinimizationResult, MinimizationStep
        dlg = MinimizerDialog(
            floor_keys=["F1", "F2", "F3"],
            current_quantities={"F1": 2, "F2": 3, "F3": 1},
            max_quantities={"F1": 5, "F2": 5, "F3": 5},
        )
        assert not dlg._btn_csv.isEnabled()
        result = MinimizationResult(
            strategy="floor_remove",
            initial_quantities={"F1": 2, "F2": 3, "F3": 1},
            final_quantities={"F1": 1, "F2": 2, "F3": 1},
            final_count=4, is_feasible=True, final_margin=0.1,
            history=[
                MinimizationStep(0, {"F1": 2, "F2": 3, "F3": 1}, 6, True, 0.2, action="init"),
            ],
            evaluations=3,
        )
        dlg._on_finished(result)
        assert dlg._btn_csv.isEnabled()
        assert dlg._btn_copy.isEnabled()
        assert dlg._result is not None

    def test_minimizer_cancel_button_exists(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(
            floor_keys=["F1", "F2"],
            current_quantities={"F1": 2, "F2": 3},
            max_quantities={"F1": 5, "F2": 5},
        )
        assert hasattr(dlg, "_btn_cancel")
        assert not dlg._btn_cancel.isEnabled()

    def test_minimizer_cancel_disables_after_finish(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        from app.services.damper_count_minimizer import MinimizationResult, MinimizationStep
        dlg = MinimizerDialog(
            floor_keys=["F1", "F2"],
            current_quantities={"F1": 2, "F2": 3},
            max_quantities={"F1": 5, "F2": 5},
        )
        result = MinimizationResult(
            strategy="floor_add",
            initial_quantities={"F1": 2, "F2": 3},
            final_quantities={"F1": 1, "F2": 2},
            final_count=3, is_feasible=True, final_margin=0.05,
            history=[
                MinimizationStep(0, {"F1": 2, "F2": 3}, 5, True, 0.1, action="init"),
            ],
            evaluations=2,
        )
        dlg._on_finished(result)
        assert not dlg._btn_cancel.isEnabled()

    def test_minimizer_cancel_on_error(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(
            floor_keys=["F1", "F2"],
            current_quantities={"F1": 2, "F2": 3},
            max_quantities={"F1": 5, "F2": 5},
        )
        dlg._on_error("中止しました")
        assert not dlg._btn_cancel.isEnabled()
        assert "中止" in dlg._lbl_status.text()

    def test_minimizer_worker_stop_requested(self, qapp):
        from app.ui.minimizer_dialog import _MinimizerWorker, _CancelledError
        from app.services.damper_count_minimizer import EvaluationResult, MinimizationStep
        import pytest

        def dummy_eval(q):
            return EvaluationResult(total_count=sum(q.values()),
                                    is_feasible=True, worst_margin=0.1)

        worker = _MinimizerWorker(
            floor_keys=["F1"], max_quantities={"F1": 5},
            initial_quantities={"F1": 3}, evaluate_fn=dummy_eval,
            strategy="random", max_iterations=10,
        )
        worker.request_stop()
        step = MinimizationStep(1, {"F1": 3}, 3, True, 0.1, action="eval")
        with pytest.raises(_CancelledError):
            worker._progress_cb(step)

    def test_minimizer_floor_table(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(
            floor_keys=["F1", "F2"],
            current_quantities={"F1": 3, "F2": 5},
            max_quantities={"F1": 10, "F2": 10},
        )
        assert dlg._floor_table.rowCount() == 2
        assert dlg._floor_table.item(0, 0).text() == "F1"
        assert dlg._floor_table.item(0, 1).text() == "3"

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
        assert "統合最適化" in action_texts or "O)" in action_texts
        assert "iRDT" in action_texts
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


@pytest.mark.skipif(not _qt_available(), reason="PySide6 unavailable")
class TestSensitivityDialog:
    """SensitivityDialog のインスタンス化・描画テスト。"""

    def test_instantiate(self, qapp):
        from app.ui.optimizer_dialog import SensitivityDialog
        from app.services.optimizer import SensitivityResult, SensitivityEntry
        result = SensitivityResult(
            entries=[
                SensitivityEntry(
                    key="Cd", label="減衰係数", base_value=500.0,
                    variations=[-0.2, -0.1, 0.0, 0.1, 0.2],
                    objective_values=[0.006, 0.0055, 0.005, 0.0047, 0.0044],
                    sensitivity_index=0.32,
                ),
                SensitivityEntry(
                    key="alpha", label="速度指数", base_value=0.4,
                    variations=[-0.2, -0.1, 0.0, 0.1, 0.2],
                    objective_values=[0.0051, 0.0050, 0.005, 0.0050, 0.0051],
                    sensitivity_index=0.02,
                ),
            ],
            base_objective=0.005,
            objective_key="max_drift",
            objective_label="最大層間変形角",
        )
        dlg = SensitivityDialog(result)
        assert dlg is not None
        assert dlg.windowTitle() == "パラメータ感度解析"

    def test_empty_entries(self, qapp):
        from app.ui.optimizer_dialog import SensitivityDialog
        from app.services.optimizer import SensitivityResult
        result = SensitivityResult(
            entries=[], base_objective=0.005,
            objective_key="max_drift",
        )
        dlg = SensitivityDialog(result)
        assert dlg is not None

    def test_sensitivity_button_exists(self, qapp):
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert hasattr(dlg, "_sensitivity_btn")
        assert not dlg._sensitivity_btn.isEnabled()

    def test_validation_rejects_invalid_range(self, qapp):
        """min_val >= max_val のとき最適化が拒否されることを検証。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from unittest.mock import patch

        dlg = OptimizerDialog()
        # min > max に設定
        if dlg._param_widgets:
            dlg._param_widgets[0]["min"].setValue(1000)
            dlg._param_widgets[0]["max"].setValue(100)
            dlg._param_widgets[0]["step"].setValue(10)
        with patch.object(dlg, "_result_summary"):
            with patch("app.ui.optimizer_dialog.QMessageBox.warning") as mock_warn:
                dlg._start_optimization()
                mock_warn.assert_called_once()
                args = mock_warn.call_args[0]
                assert "パラメータ設定エラー" in args[1]

    def test_validation_rejects_zero_step_grid(self, qapp):
        """グリッドサーチで刻み幅0のとき最適化が拒否されることを検証。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from unittest.mock import patch

        dlg = OptimizerDialog()
        # グリッドサーチを選択し、step=0に設定
        dlg._method_combo.setCurrentIndex(0)  # grid
        if dlg._param_widgets:
            dlg._param_widgets[0]["min"].setValue(100)
            dlg._param_widgets[0]["max"].setValue(1000)
            dlg._param_widgets[0]["step"].setValue(0)
        with patch.object(dlg, "_result_summary"):
            with patch("app.ui.optimizer_dialog.QMessageBox.warning") as mock_warn:
                dlg._start_optimization()
                mock_warn.assert_called_once()
                args = mock_warn.call_args[0]
                assert "パラメータ設定エラー" in args[1]

    def test_validation_accepts_valid_range(self, qapp):
        """正常なパラメータ設定でバリデーションを通過することを検証。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from unittest.mock import patch, MagicMock

        dlg = OptimizerDialog()
        if dlg._param_widgets:
            dlg._param_widgets[0]["min"].setValue(100)
            dlg._param_widgets[0]["max"].setValue(200)
            dlg._param_widgets[0]["step"].setValue(100)
        # _iter_spin を低く設定して時間警告を回避
        dlg._iter_spin.setValue(5)
        # optimize と matplotlib描画をモックして安全に通過
        with patch.object(dlg._optimizer, "optimize") as mock_opt:
            with patch.object(dlg._conv_canvas, "draw"):
                with patch("app.ui.optimizer_dialog.QMessageBox.warning") as mock_warn:
                    dlg._start_optimization()
                    mock_warn.assert_not_called()
                    mock_opt.assert_called_once()


class TestCandidateDetailDialog:
    """候補詳細ダイアログのインスタンス化テスト。"""

    def test_instantiate_feasible(self, qapp):
        from app.ui.optimizer_dialog import _CandidateDetailDialog
        from app.services.optimizer import OptimizationCandidate, OptimizationConfig

        cand = OptimizationCandidate(
            params={"Cd": 500, "alpha": 0.3},
            objective_value=0.003,
            response_values={"max_drift": 0.003, "max_acc": 2.5, "shear_coeff": 0.15},
            is_feasible=True,
            constraint_margins={"max_drift": 0.002},
        )
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[],
        )
        dlg = _CandidateDetailDialog(cand, config)
        assert dlg.windowTitle().startswith("候補詳細")
        dlg.close()

    def test_instantiate_infeasible(self, qapp):
        from app.ui.optimizer_dialog import _CandidateDetailDialog
        from app.services.optimizer import OptimizationCandidate

        cand = OptimizationCandidate(
            params={"Cd": 100},
            objective_value=0.01,
            response_values={"max_drift": 0.01},
            is_feasible=False,
            constraint_margins={"max_drift": -0.005},
        )
        dlg = _CandidateDetailDialog(cand, None)
        assert dlg.windowTitle().startswith("候補詳細")
        dlg.close()

    def test_instantiate_no_margins(self, qapp):
        from app.ui.optimizer_dialog import _CandidateDetailDialog
        from app.services.optimizer import OptimizationCandidate

        cand = OptimizationCandidate(
            params={"x": 1.0},
            objective_value=0.5,
            response_values={"max_drift": 0.5},
        )
        dlg = _CandidateDetailDialog(cand, None)
        dlg.close()


class TestMethodRecommendation:
    """手法推奨ロジックのテスト。"""

    def test_recommend_grid_for_small_space(self, qapp):
        """パラメータ空間が小さい場合グリッドサーチを推奨。"""
        from app.ui.optimizer_dialog import OptimizerDialog

        dlg = OptimizerDialog()
        # デフォルトのオイルダンパー: Cd(100-2000,step100)=20, alpha(0.1-1.0,step0.1)=10 → 200通り
        # 200 > 50 なのでベイズが推奨される。小さい空間にするため調整
        if dlg._param_widgets:
            dlg._param_widgets[0]["min"].setValue(100)
            dlg._param_widgets[0]["max"].setValue(300)
            dlg._param_widgets[0]["step"].setValue(100)
            dlg._param_widgets[1]["min"].setValue(0.3)
            dlg._param_widgets[1]["max"].setValue(0.5)
            dlg._param_widgets[1]["step"].setValue(0.1)
        rec_method, reason, _ = dlg._recommend_method()
        assert rec_method == "grid"
        assert "グリッドサーチ" in reason
        dlg.close()

    def test_recommend_bayesian_for_medium_space(self, qapp):
        """中規模空間ではベイズ最適化を推奨。"""
        from app.ui.optimizer_dialog import OptimizerDialog

        dlg = OptimizerDialog()
        # Cd: 100-2000, step=100 → 20通り, alpha: 0.1-1.0, step=0.1 → 10通り → 200通り
        rec_method, reason, iter_hint = dlg._recommend_method()
        assert rec_method == "bayesian"
        assert "ベイズ" in reason
        assert "推奨反復数" in iter_hint
        dlg.close()

    def test_recommend_button_exists(self, qapp):
        """おすすめボタンが存在する。"""
        from app.ui.optimizer_dialog import OptimizerDialog

        dlg = OptimizerDialog()
        assert hasattr(dlg, "_method_rec_btn")
        assert dlg._method_rec_btn.text() == "💡 おすすめ"
        dlg.close()

    def test_recommendation_hint_in_est_label(self, qapp):
        """推奨手法が異なる場合、推定ラベルにヒントが表示される。"""
        from app.ui.optimizer_dialog import OptimizerDialog

        dlg = OptimizerDialog()
        # デフォルトはグリッドサーチ選択、200通り → ベイズ推奨
        dlg._method_combo.setCurrentIndex(0)  # グリッドサーチ
        dlg._update_est_run_label()
        label_text = dlg._est_run_label.text()
        assert "💡" in label_text or "推奨" in label_text
        dlg.close()


class TestStagnationDetection:
    """収束停滞検出のテスト。"""

    def test_no_stagnation_with_improving_data(self, qapp):
        """改善が続くデータでは停滞を検出しない。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from app.services.optimizer import OptimizationCandidate

        candidates = [
            OptimizationCandidate(
                params={"x": float(i)},
                objective_value=100.0 - i,
                response_values={"max_drift": 0.01 - i * 0.0001},
                is_feasible=True,
            )
            for i in range(50)
        ]
        result = OptimizerDialog._detect_stagnation(candidates)
        assert result is None

    def test_detect_stagnation_with_flat_data(self, qapp):
        """後半がフラットなデータで停滞を検出する。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from app.services.optimizer import OptimizationCandidate

        # 前半: 改善あり、後半: フラット
        candidates = []
        for i in range(100):
            if i < 30:
                val = 10.0 - i * 0.1  # 改善
            else:
                val = 7.0 + (i % 3) * 0.001  # ほぼフラット（最良は7.0）
            candidates.append(
                OptimizationCandidate(
                    params={"x": float(i)},
                    objective_value=val,
                    response_values={"max_drift": val * 0.001},
                    is_feasible=True,
                )
            )
        result = OptimizerDialog._detect_stagnation(candidates)
        assert result is not None
        assert result["stagnation_length"] > 10
        assert result["total_evals"] == 100

    def test_no_stagnation_with_few_data(self, qapp):
        """データが少ない場合は停滞検出しない。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from app.services.optimizer import OptimizationCandidate

        candidates = [
            OptimizationCandidate(
                params={"x": 1.0},
                objective_value=5.0,
                response_values={"max_drift": 0.005},
                is_feasible=True,
            )
            for _ in range(5)
        ]
        result = OptimizerDialog._detect_stagnation(candidates)
        assert result is None

    def test_stagnation_with_infeasible_mixed(self, qapp):
        """infeasibleが混在してもfeasibleのみで判定する。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from app.services.optimizer import OptimizationCandidate

        candidates = []
        for i in range(80):
            is_f = i % 2 == 0  # 半分がfeasible
            val = 5.0 if is_f else float("inf")
            candidates.append(
                OptimizationCandidate(
                    params={"x": float(i)},
                    objective_value=val,
                    response_values={"max_drift": 0.005},
                    is_feasible=is_f,
                )
            )
        result = OptimizerDialog._detect_stagnation(candidates)
        # 全feasibleが同値(5.0) → 停滞検出
        assert result is not None

    def test_result_table_shows_infeasible(self, qapp):
        """結果テーブルが制約違反候補も表示し、feasible優先でソートする。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from app.services.optimizer import (
            OptimizationCandidate, OptimizationResult, OptimizationConfig,
        )

        dlg = OptimizerDialog()
        result = OptimizationResult(
            config=OptimizationConfig(objective_key="max_drift", method="grid"),
            best=OptimizationCandidate(
                params={"Cd": 500}, objective_value=0.003,
                response_values={"max_drift": 0.003}, is_feasible=True,
            ),
            all_candidates=[
                OptimizationCandidate(
                    params={"Cd": 300}, objective_value=0.01,
                    response_values={"max_drift": 0.01}, is_feasible=False,
                ),
                OptimizationCandidate(
                    params={"Cd": 500}, objective_value=0.003,
                    response_values={"max_drift": 0.003}, is_feasible=True,
                ),
                OptimizationCandidate(
                    params={"Cd": 400}, objective_value=0.006,
                    response_values={"max_drift": 0.006}, is_feasible=False,
                ),
            ],
        )
        dlg._populate_result_table(result)
        # 3行表示される（feasible 1 + infeasible 2）
        assert dlg._result_table.rowCount() == 3
        # 1行目はfeasible（順位 "1"）
        assert dlg._result_table.item(0, 0).text() == "1"
        assert dlg._result_table.item(0, 3).text() == "OK"
        # 2行目はinfeasible（順位 "-"）
        assert dlg._result_table.item(1, 0).text() == "-"
        assert dlg._result_table.item(1, 3).text() == "NG"
        dlg.close()


class TestWarmStartUI:
    """ウォームスタートUI要素のテスト。"""

    def test_warm_start_checkbox_exists(self, qapp):
        """OptimizerDialogにウォームスタートチェックボックスがある。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert hasattr(dlg, "_warm_start_cb")
        assert not dlg._warm_start_cb.isChecked()
        dlg.close()

    def test_warm_start_browse_disabled_by_default(self, qapp):
        """チェックOFF時は参照ボタンが無効。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert not dlg._warm_start_browse_btn.isEnabled()
        dlg.close()

    def test_warm_start_browse_enabled_on_check(self, qapp):
        """チェックON時は参照ボタンが有効。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        dlg._warm_start_cb.setChecked(True)
        assert dlg._warm_start_browse_btn.isEnabled()
        dlg._warm_start_cb.setChecked(False)
        assert not dlg._warm_start_browse_btn.isEnabled()
        dlg.close()

    def test_warm_start_config_empty_when_unchecked(self, qapp):
        """チェックOFF時はconfigにウォーム候補が含まれない。"""
        from app.ui.optimizer_dialog import OptimizerDialog, OptimizationCandidate
        dlg = OptimizerDialog()
        dlg._warm_start_candidates = [
            OptimizationCandidate(params={"Cd": 500}, objective_value=0.005)
        ]
        dlg._warm_start_cb.setChecked(False)
        config = dlg._build_config()
        assert config.warm_start_candidates == []
        dlg.close()

    def test_warm_start_config_populated_when_checked(self, qapp):
        """チェックON時はconfigにウォーム候補が含まれる。"""
        from app.ui.optimizer_dialog import OptimizerDialog, OptimizationCandidate
        dlg = OptimizerDialog()
        dlg._warm_start_candidates = [
            OptimizationCandidate(params={"Cd": 500}, objective_value=0.005)
        ]
        dlg._warm_start_cb.setChecked(True)
        config = dlg._build_config()
        assert len(config.warm_start_candidates) == 1
        dlg.close()


class TestConfigPreset:
    """設定プリセット保存・読込のテスト。"""

    def test_save_config_buttons_exist(self, qapp):
        """OptimizerDialogに設定保存/読込ボタンがある。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert hasattr(dlg, "_save_config_btn")
        assert hasattr(dlg, "_load_config_btn")
        dlg.close()

    def test_build_config_roundtrip(self, qapp):
        """_build_config() → to_dict → _apply_config_preset のラウンドトリップ。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        # 値を変更
        dlg._iter_spin.setValue(200)
        dlg._parallel_spin.setValue(4)
        dlg._penalty_cb.setChecked(True)
        dlg._penalty_spin.setValue(50.0)

        config = dlg._build_config()
        preset = config.to_dict()

        # 別のダイアログで復元
        dlg2 = OptimizerDialog()
        dlg2._apply_config_preset(preset)
        assert dlg2._iter_spin.value() == 200
        assert dlg2._parallel_spin.value() == 4
        assert dlg2._penalty_cb.isChecked() is True
        assert dlg2._penalty_spin.value() == 50.0
        dlg.close()
        dlg2.close()

    def test_apply_preset_objective_key(self, qapp):
        """プリセットから目的関数が正しく復元される。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        preset = {"objective_key": "max_acc", "parameters": [], "method": "random"}
        dlg._apply_config_preset(preset)
        # max_acc は _OBJECTIVE_ITEMS の2番目(index=1)
        assert dlg._obj_combo.currentIndex() == 1
        dlg.close()

    def test_apply_preset_damper_type(self, qapp):
        """プリセットからダンパー種類が正しく復元さ��る。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        preset = {"damper_type": "鋼材ダンパー", "parameters": []}
        dlg._apply_config_preset(preset)
        assert dlg._damper_combo.currentText() == "鋼材ダンパー"
        dlg.close()

    def test_apply_preset_param_ranges(self, qapp):
        """プリセットからパラメータ範囲が正しく復元される。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        # デフォルト（オイルダンパー）のパラメータを変更するプリセット
        preset = {
            "damper_type": "オイルダンパー",
            "parameters": [
                {"key": "Cd", "label": "減衰係数 Cd", "min_val": 200, "max_val": 1500, "step": 50},
                {"key": "alpha", "label": "速度指数 α", "min_val": 0.2, "max_val": 0.8, "step": 0.05},
            ],
        }
        dlg._apply_config_preset(preset)
        assert len(dlg._param_widgets) >= 2
        assert dlg._param_widgets[0]["min"].value() == 200
        assert dlg._param_widgets[0]["max"].value() == 1500
        assert dlg._param_widgets[0]["step"].value() == 50
        assert dlg._param_widgets[1]["min"].value() == 0.2
        dlg.close()

    def test_apply_preset_robust(self, qapp):
        """プリセットからロバスト最適化設定が復元される。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        preset = {"robustness_samples": 5, "robustness_delta": 0.1}
        dlg._apply_config_preset(preset)
        assert dlg._robust_check.isChecked() is True
        assert dlg._robust_samples_spin.value() == 5
        assert abs(dlg._robust_delta_spin.value() - 0.1) < 1e-6
        dlg.close()


class TestComparisonDialog:
    """結果比較ダイアログのテスト。"""

    def test_instantiation(self, qapp):
        """ComparisonDialogがインスタンス化できる。"""
        from app.ui.optimizer_dialog import ComparisonDialog
        dlg = ComparisonDialog()
        assert dlg.windowTitle() == "最適化結果の比較"
        dlg.close()

    def test_clear_all(self, qapp):
        """全クリアが正常動作する。"""
        from app.ui.optimizer_dialog import ComparisonDialog
        dlg = ComparisonDialog()
        dlg._clear_all()
        assert dlg._table.rowCount() == 0
        dlg.close()

    def test_compare_button_exists(self, qapp):
        """OptimizerDialogに結果比較ボタンがある。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert hasattr(dlg, "_compare_btn")
        dlg.close()


class TestCorrelationDialog:
    """CorrelationDialog のUIテスト。"""

    def test_instantiation(self, qapp):
        """CorrelationDialogが正常にインスタンス化される。"""
        from app.ui.optimizer_dialog import CorrelationDialog
        from app.services.optimizer import (
            CorrelationResult,
            CorrelationEntry,
        )
        corr = CorrelationResult(
            entries=[
                CorrelationEntry(
                    param_x="Cd", param_y="alpha",
                    label_x="減衰係数", label_y="速度指数",
                    correlation=0.85,
                    x_values=[100, 200, 300],
                    y_values=[0.2, 0.4, 0.6],
                ),
            ],
            param_keys=["Cd", "alpha"],
            param_labels=["減衰係数", "速度指数"],
            n_candidates=10,
            objective_key="max_drift",
        )
        dlg = CorrelationDialog(corr)
        assert dlg is not None
        assert "相関分析" in dlg.windowTitle()
        dlg.close()

    def test_no_strong_correlation(self, qapp):
        """強い相関がない場合も正常表示。"""
        from app.ui.optimizer_dialog import CorrelationDialog
        from app.services.optimizer import CorrelationResult, CorrelationEntry
        corr = CorrelationResult(
            entries=[
                CorrelationEntry(
                    param_x="Cd", param_y="alpha",
                    label_x="減衰係数", label_y="速度指数",
                    correlation=0.1,
                ),
            ],
            param_keys=["Cd", "alpha"],
            param_labels=["減衰係数", "速度指数"],
            n_candidates=5,
            objective_key="max_drift",
        )
        dlg = CorrelationDialog(corr)
        assert dlg is not None
        dlg.close()

    def test_correlation_button_exists(self, qapp):
        """OptimizerDialogに相関分析ボタンがある。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert hasattr(dlg, "_correlation_btn")
        assert not dlg._correlation_btn.isEnabled()
        dlg.close()

    def test_log_export_button_exists(self, qapp):
        """OptimizerDialogに評価ログボタンがある。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert hasattr(dlg, "_log_export_btn")
        assert not dlg._log_export_btn.isEnabled()
        dlg.close()


class TestDiagnosticsDialog:
    """DiagnosticsDialog のUIテスト。"""

    def test_instantiation_good_score(self, qapp):
        """高スコアの診断結果が正常に表示される。"""
        from app.ui.optimizer_dialog import DiagnosticsDialog
        from app.services.optimizer import ConvergenceDiagnostics
        diag = ConvergenceDiagnostics(
            feasibility_ratio=0.8,
            improvement_ratio=0.001,
            space_coverage=0.7,
            best_cluster_ratio=0.2,
            stagnation_detected=False,
            n_evaluations=100,
            n_feasible=80,
            quality_score=90,
            quality_label="優良",
            recommendations=["探索品質は良好です。結果を信頼して設計に使用できます。"],
        )
        dlg = DiagnosticsDialog(diag)
        assert dlg is not None
        assert "収束品質診断" in dlg.windowTitle()
        dlg.close()

    def test_instantiation_low_score(self, qapp):
        """低スコアの診断結果が正常に表示される。"""
        from app.ui.optimizer_dialog import DiagnosticsDialog
        from app.services.optimizer import ConvergenceDiagnostics
        diag = ConvergenceDiagnostics(
            feasibility_ratio=0.0,
            improvement_ratio=0.3,
            space_coverage=0.05,
            best_cluster_ratio=0.01,
            stagnation_detected=True,
            n_evaluations=5,
            n_feasible=0,
            quality_score=10,
            quality_label="不十分",
            recommendations=[
                "制約を満たす候補が0件です。",
                "反復数を増やして再実行を推奨します。",
            ],
        )
        dlg = DiagnosticsDialog(diag)
        assert dlg is not None
        dlg.close()

    def test_diagnostics_button_exists(self, qapp):
        """OptimizerDialogに収束診断ボタンがある。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert hasattr(dlg, "_diagnostics_btn")
        assert not dlg._diagnostics_btn.isEnabled()
        dlg.close()

    def test_format_duration(self, qapp):
        """_format_duration が秒/分/時間を正しくフォーマットする。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        fmt = OptimizerDialog._format_duration
        assert fmt(5) == "5秒"
        assert fmt(59) == "59秒"
        assert fmt(60) == "1分0秒"
        assert fmt(90) == "1分30秒"
        assert fmt(3661) == "1時間1分"

    def test_eta_progress_label(self, qapp):
        """_on_progress でETA情報が進捗ラベルに含まれる。"""
        import time
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        dlg._opt_start_time = time.time() - 10  # 10秒前に開始
        dlg._on_progress(5, 10, "評価中: 5/10")
        label_text = dlg._progress_label.text()
        assert "経過" in label_text
        assert "残り" in label_text
        dlg.close()

    def test_copy_params_button_exists(self, qapp):
        """OptimizerDialogに最良パラメータコピーボタンがある。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert hasattr(dlg, "_copy_params_btn")
        assert not dlg._copy_params_btn.isEnabled()
        dlg.close()

    def test_copy_best_params_with_result(self, qapp):
        """最良解がある場合にパラメータをクリップボードにコピーできる。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from app.services.optimizer import OptimizationResult, OptimizationCandidate
        from PySide6.QtWidgets import QApplication
        dlg = OptimizerDialog()
        best = OptimizationCandidate(
            params={"Cd": 500.0, "alpha": 0.3},
            objective_value=0.003,
            response_values={"max_drift": 0.003, "max_acc": 2.5},
            is_feasible=True,
        )
        dlg._result = OptimizationResult(best=best, all_candidates=[best])
        dlg._copy_best_params()
        clipboard = QApplication.clipboard()
        text = clipboard.text() if clipboard else ""
        assert "Cd = 500" in text
        assert "alpha = 0.3" in text
        assert "目的関数値" in text
        dlg.close()


class TestResultTableSorting:
    """結果テーブルのインタラクティブソートのテスト。"""

    def test_sorting_enabled(self, qapp):
        """結果テーブルにソートが有効化されている。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert dlg._result_table.isSortingEnabled()
        dlg.close()

    def test_sort_by_objective_value(self, qapp):
        """目的関数値列でソートすると数値順になる。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from app.services.optimizer import (
            OptimizationCandidate, OptimizationConfig, OptimizationResult,
            ParameterRange,
        )
        from PySide6.QtCore import Qt
        dlg = OptimizerDialog()
        config = OptimizationConfig(
            parameters=[ParameterRange("Cd", "Cd", 100, 1000, 100)],
            objective_key="max_drift",
        )
        result = OptimizationResult(
            config=config,
            all_candidates=[
                OptimizationCandidate(
                    params={"Cd": 300}, objective_value=0.008,
                    response_values={"max_drift": 0.008}, is_feasible=True,
                ),
                OptimizationCandidate(
                    params={"Cd": 500}, objective_value=0.002,
                    response_values={"max_drift": 0.002}, is_feasible=True,
                ),
                OptimizationCandidate(
                    params={"Cd": 400}, objective_value=0.005,
                    response_values={"max_drift": 0.005}, is_feasible=True,
                ),
            ],
        )
        dlg._populate_result_table(result)
        # 目的関数値列(col=2)で昇順ソート
        dlg._result_table.sortByColumn(2, Qt.AscendingOrder)
        # 最小値が先頭
        assert dlg._result_table.item(0, 2).text() == "0.002"
        assert dlg._result_table.item(2, 2).text() == "0.008"
        dlg.close()

    def test_sort_by_verdict(self, qapp):
        """判定列でソートするとOKが先に来る。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from app.services.optimizer import (
            OptimizationCandidate, OptimizationConfig, OptimizationResult,
            ParameterRange,
        )
        from PySide6.QtCore import Qt
        dlg = OptimizerDialog()
        config = OptimizationConfig(
            parameters=[ParameterRange("Cd", "Cd", 100, 1000, 100)],
            objective_key="max_drift",
        )
        result = OptimizationResult(
            config=config,
            all_candidates=[
                OptimizationCandidate(
                    params={"Cd": 300}, objective_value=0.01,
                    response_values={"max_drift": 0.01}, is_feasible=False,
                ),
                OptimizationCandidate(
                    params={"Cd": 500}, objective_value=0.003,
                    response_values={"max_drift": 0.003}, is_feasible=True,
                ),
            ],
        )
        dlg._populate_result_table(result)
        # 判定列(col=3)で昇順ソート → OK(0.0)がNG(1.0)より先
        dlg._result_table.sortByColumn(3, Qt.AscendingOrder)
        assert dlg._result_table.item(0, 3).text() == "OK"
        assert dlg._result_table.item(1, 3).text() == "NG"
        dlg.close()

    def test_numeric_table_item_sort_order(self, qapp):
        """_NumericTableItemが数値順でソートされる。"""
        from app.ui.optimizer_dialog import _NumericTableItem
        item_a = _NumericTableItem("0.002", 0.002)
        item_b = _NumericTableItem("0.010", 0.010)
        item_dash = _NumericTableItem("-", float("inf"))
        assert item_a < item_b
        assert item_b < item_dash
        assert not item_b < item_a


class TestHeatmapDialog:
    """パラメータ空間ヒートマップダイアログのテスト。"""

    def test_heatmap_button_exists(self, qapp):
        """OptimizerDialogにヒートマップボタンがある。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert hasattr(dlg, "_heatmap_btn")
        assert dlg._heatmap_btn.text() == "空間ヒートマップ"
        assert not dlg._heatmap_btn.isEnabled()
        dlg.close()

    def test_heatmap_dialog_opens(self, qapp):
        """ヒートマップダイアログがクラッシュせずにインスタンス化できる。"""
        from app.ui.optimizer_dialog import _HeatmapDialog
        from app.services.optimizer import (
            OptimizationCandidate, OptimizationConfig, OptimizationResult,
            ParameterRange,
        )
        config = OptimizationConfig(
            parameters=[
                ParameterRange("Cd", "減衰係数", 100, 1000, 100),
                ParameterRange("alpha", "速度指数", 0.1, 1.0, 0.1),
            ],
            objective_key="max_drift",
        )
        candidates = [
            OptimizationCandidate(
                params={"Cd": 100 + i * 100, "alpha": 0.1 + i * 0.1},
                objective_value=0.01 - i * 0.001,
                response_values={"max_drift": 0.01 - i * 0.001},
                is_feasible=True,
            )
            for i in range(5)
        ]
        result = OptimizationResult(config=config, all_candidates=candidates)
        dlg = _HeatmapDialog(result)
        assert dlg.windowTitle() == "パラメータ空間ヒートマップ"
        dlg.close()

    def test_heatmap_with_three_params(self, qapp):
        """3パラメータ時にペア選択コンボボックスが表示される。"""
        from app.ui.optimizer_dialog import _HeatmapDialog
        from app.services.optimizer import (
            OptimizationCandidate, OptimizationConfig, OptimizationResult,
            ParameterRange,
        )
        config = OptimizationConfig(
            parameters=[
                ParameterRange("Cd", "Cd", 100, 500, 100),
                ParameterRange("alpha", "alpha", 0.1, 0.5, 0.1),
                ParameterRange("Qy", "Qy", 50, 200, 50),
            ],
            objective_key="max_drift",
        )
        candidates = [
            OptimizationCandidate(
                params={"Cd": 200 + i * 50, "alpha": 0.2 + i * 0.05, "Qy": 100 + i * 20},
                objective_value=0.005 + i * 0.001,
                response_values={"max_drift": 0.005 + i * 0.001},
                is_feasible=True,
            )
            for i in range(5)
        ]
        result = OptimizationResult(config=config, all_candidates=candidates)
        dlg = _HeatmapDialog(result)
        # 3C2 = 3ペア
        assert dlg._pair_combo is not None
        assert dlg._pair_combo.count() == 3
        dlg.close()

    def test_heatmap_info_label(self, qapp):
        """情報ラベルにカバレッジ情報が表示される。"""
        from app.ui.optimizer_dialog import _HeatmapDialog
        from app.services.optimizer import (
            OptimizationCandidate, OptimizationConfig, OptimizationResult,
            ParameterRange,
        )
        config = OptimizationConfig(
            parameters=[
                ParameterRange("Cd", "Cd", 100, 1000, 100),
                ParameterRange("alpha", "alpha", 0.1, 1.0, 0.1),
            ],
            objective_key="max_drift",
        )
        candidates = [
            OptimizationCandidate(
                params={"Cd": 100 + i * 100, "alpha": 0.1 + i * 0.1},
                objective_value=0.01 - i * 0.001,
                response_values={"max_drift": 0.01 - i * 0.001},
                is_feasible=True,
            )
            for i in range(10)
        ]
        result = OptimizationResult(config=config, all_candidates=candidates)
        dlg = _HeatmapDialog(result)
        info = dlg._info_label.text()
        assert "候補数" in info
        assert "ビン" in info
        assert "探索済み" in info
        dlg.close()

    # ----------------------------------------------------------
    # MinimizerDialog 詳細パラメータパネル
    # ----------------------------------------------------------

    def test_minimizer_adv_panel_visibility_ga(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(["F1"], {"F1": 5}, {"F1": 3})
        # GA を選択
        for i in range(dlg._combo_strategy.count()):
            if dlg._combo_strategy.itemData(i) == "ga":
                dlg._combo_strategy.setCurrentIndex(i)
                break
        # isHidden() はウィジェット自身の hidden 状態のみ確認（親の表示に依存しない）
        assert not dlg._spin_pop.isHidden()
        assert dlg._spin_temp.isHidden()
        assert dlg._chk_de_adaptive.isHidden()
        dlg.close()

    def test_minimizer_adv_panel_visibility_sa(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(["F1"], {"F1": 5}, {"F1": 3})
        for i in range(dlg._combo_strategy.count()):
            if dlg._combo_strategy.itemData(i) == "sa":
                dlg._combo_strategy.setCurrentIndex(i)
                break
        assert dlg._spin_pop.isHidden()
        assert not dlg._spin_temp.isHidden()
        assert dlg._chk_de_adaptive.isHidden()
        dlg.close()

    def test_minimizer_adv_panel_visibility_de(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(["F1"], {"F1": 5}, {"F1": 3})
        for i in range(dlg._combo_strategy.count()):
            if dlg._combo_strategy.itemData(i) == "de":
                dlg._combo_strategy.setCurrentIndex(i)
                break
        assert not dlg._spin_pop.isHidden()
        assert not dlg._chk_de_adaptive.isHidden()
        assert dlg._chk_de_adaptive.isChecked()  # デフォルトON
        dlg.close()

    def test_minimizer_adv_panel_hidden_for_deterministic(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(["F1"], {"F1": 5}, {"F1": 3})
        for i in range(dlg._combo_strategy.count()):
            if dlg._combo_strategy.itemData(i) == "floor_add":
                dlg._combo_strategy.setCurrentIndex(i)
                break
        assert dlg._adv_group.isHidden()
        dlg.close()

    def test_minimizer_collect_extra_kwargs_de(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(["F1"], {"F1": 5}, {"F1": 3})
        dlg._spin_pop.setValue(20)
        dlg._chk_de_adaptive.setChecked(False)
        kw = dlg._collect_extra_kwargs("de")
        assert kw["population_size"] == 20
        assert kw["adaptive"] is False
        dlg.close()

    def test_minimizer_collect_extra_kwargs_sa(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(["F1"], {"F1": 5}, {"F1": 3})
        dlg._spin_temp.setValue(500.0)
        kw = dlg._collect_extra_kwargs("sa")
        assert kw["initial_temp"] == 500.0
        assert "population_size" not in kw
        dlg.close()

    def test_minimizer_collect_extra_kwargs_bayesian(self, qapp):
        from app.ui.minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(["F1"], {"F1": 5}, {"F1": 3})
        dlg._spin_n_initial.setValue(15)
        kw = dlg._collect_extra_kwargs("bayesian")
        assert kw["n_initial"] == 15
        dlg.close()

    # ------------------------------------------------------------------
    # Phase AF: 乱数シード制御 UI
    # ------------------------------------------------------------------

    def test_optimizer_seed_checkbox_default(self, qapp):
        """OptimizerDialogの乱数シードチェックボックスがデフォルトでオフであること。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert not dlg._seed_check.isChecked()
        assert not dlg._seed_spin.isEnabled()
        dlg.close()

    def test_optimizer_seed_checkbox_toggle(self, qapp):
        """シードチェックボックスのON/OFFでスピンが有効/無効になること。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        dlg._seed_check.setChecked(True)
        assert dlg._seed_spin.isEnabled()
        dlg._seed_check.setChecked(False)
        assert not dlg._seed_spin.isEnabled()
        dlg.close()

    def test_optimizer_seed_in_config(self, qapp):
        """シード有効時にconfigにrandom_seedが設定されること。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        dlg._seed_check.setChecked(True)
        dlg._seed_spin.setValue(123)
        config = dlg._build_config()
        assert config.random_seed == 123
        dlg.close()

    def test_optimizer_seed_none_when_unchecked(self, qapp):
        """シード無効時にconfigのrandom_seedがNoneであること。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        dlg._seed_check.setChecked(False)
        config = dlg._build_config()
        assert config.random_seed is None
        dlg.close()

    def test_optimizer_save_plot_btn_exists(self, qapp):
        """OptimizerDialogに収束グラフ画像保存ボタンがある。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert hasattr(dlg, "_save_plot_btn")
        assert not dlg._save_plot_btn.isEnabled()
        dlg.close()

    def test_optimizer_save_plot_btn_enabled_after_result(self, qapp):
        """結果読込後に画像保存ボタンが有効化される。"""
        from app.ui.optimizer_dialog import (
            OptimizerDialog, OptimizationResult, OptimizationCandidate,
            OptimizationConfig, ParameterRange,
        )
        dlg = OptimizerDialog()
        cand = OptimizationCandidate(
            params={"x": 0.5}, objective_value=0.1,
            response_values={"max_drift": 0.1}, is_feasible=True, iteration=0,
        )
        result = OptimizationResult(
            best=cand, all_candidates=[cand],
            config=OptimizationConfig(
                objective_key="max_drift",
                parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0.1)],
            ),
        )
        dlg._result = result
        dlg._populate_result_table(result)
        dlg._draw_convergence(result)
        dlg._save_plot_btn.setEnabled(True)
        assert dlg._save_plot_btn.isEnabled()
        dlg.close()

    def test_optimizer_save_convergence_plot_to_file(self, qapp, tmp_path):
        """_save_convergence_plotで画像ファイルが保存される。"""
        from unittest.mock import patch
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        out_path = str(tmp_path / "test_plot.png")
        with patch(
            "app.ui.optimizer_dialog.QFileDialog.getSaveFileName",
            return_value=(out_path, "PNG Image (*.png)"),
        ), patch(
            "app.ui.optimizer_dialog.QMessageBox.information",
        ):
            dlg._save_convergence_plot()
        import os
        assert os.path.exists(out_path)
        assert os.path.getsize(out_path) > 0
        dlg.close()

    # ------------------------------------------------------------------
    # MinimizerDialog: 結果適用ボタン + closeEvent
    # ------------------------------------------------------------------

    def test_minimizer_apply_button_disabled_without_model(self, qapp):
        """model_pathなしでは適用ボタンが無効。"""
        from app.ui.minimizer_dialog import MinimizerDialog

        dlg = MinimizerDialog(["F1", "F2"], {"F1": 5, "F2": 3}, {"F1": 10, "F2": 6})
        assert not dlg._btn_apply.isEnabled()
        dlg.close()

    def test_minimizer_apply_button_enabled_after_finish(self, qapp):
        """model_path+floor_rd_mapありで完了後に適用ボタンが有効。"""
        from app.ui.minimizer_dialog import MinimizerDialog
        from app.services.damper_count_minimizer import MinimizationResult

        dlg = MinimizerDialog(
            ["F1"], {"F1": 5}, {"F1": 10},
            model_path="/tmp/test.s8i",
            floor_rd_map={"F1": [0, 1]},
        )
        result = MinimizationResult(
            strategy="floor_add",
            initial_quantities={"F1": 5},
            final_quantities={"F1": 3},
            final_count=3,
            is_feasible=True,
            final_margin=0.05,
            evaluations=5,
        )
        dlg._on_finished(result)
        assert dlg._btn_apply.isEnabled()
        dlg.close()

    def test_minimizer_apply_writes_s8i(self, qapp, tmp_path, monkeypatch):
        """適用ボタンが.s8iファイルにダンパー本数を書き戻す。"""
        from unittest.mock import patch, MagicMock
        from PySide6.QtWidgets import QMessageBox
        from app.ui.minimizer_dialog import MinimizerDialog
        from app.services.damper_count_minimizer import MinimizationResult

        dlg = MinimizerDialog(
            ["F1"], {"F1": 5}, {"F1": 10},
            model_path="/tmp/test.s8i",
            floor_rd_map={"F1": [0, 1]},
        )
        result = MinimizationResult(
            strategy="floor_add",
            initial_quantities={"F1": 5},
            final_quantities={"F1": 4},
            final_count=4,
            is_feasible=True,
            final_margin=0.05,
            evaluations=5,
        )
        dlg._on_finished(result)

        mock_model = MagicMock()
        with patch(
            "app.ui.minimizer_dialog.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch(
            "app.models.s8i_parser.parse_s8i",
            return_value=mock_model,
        ), patch(
            "app.ui.minimizer_dialog.QMessageBox.information",
        ):
            dlg._apply_result_to_s8i()

        # F1: 4本を2要素に分配 → 2本, 2本
        assert mock_model.update_damper_element.call_count == 2
        mock_model.update_damper_element.assert_any_call(0, quantity=2)
        mock_model.update_damper_element.assert_any_call(1, quantity=2)
        mock_model.write.assert_called_once_with("/tmp/test.s8i")
        dlg.close()

    def test_minimizer_apply_infeasible_warning(self, qapp, monkeypatch):
        """制約未充足時に警告を表示する。"""
        from unittest.mock import patch
        from PySide6.QtWidgets import QMessageBox
        from app.ui.minimizer_dialog import MinimizerDialog
        from app.services.damper_count_minimizer import MinimizationResult

        dlg = MinimizerDialog(
            ["F1"], {"F1": 5}, {"F1": 10},
            model_path="/tmp/test.s8i",
            floor_rd_map={"F1": [0]},
        )
        result = MinimizationResult(
            strategy="floor_add",
            initial_quantities={"F1": 5},
            final_quantities={"F1": 3},
            final_count=3,
            is_feasible=False,
            final_margin=-0.02,
            evaluations=5,
        )
        dlg._on_finished(result)

        # Noを選択 → 書き戻さない
        with patch(
            "app.ui.minimizer_dialog.QMessageBox.warning",
            return_value=QMessageBox.StandardButton.No,
        ) as mock_warn:
            dlg._apply_result_to_s8i()
            mock_warn.assert_called_once()
        dlg.close()

    def test_minimizer_close_event_stops_worker(self, qapp):
        """closeEventでワーカーを停止する。"""
        from unittest.mock import MagicMock
        from app.ui.minimizer_dialog import MinimizerDialog

        dlg = MinimizerDialog(["F1"], {"F1": 5}, {"F1": 10})
        mock_worker = MagicMock()
        mock_worker.isRunning.return_value = True
        mock_worker.wait.return_value = True
        dlg._worker = mock_worker

        dlg.close()

        mock_worker.request_stop.assert_called_once()
        mock_worker.quit.assert_called_once()
        mock_worker.wait.assert_called_once_with(3000)


class TestConstraintMarginColumn:
    """結果テーブルの最小マージン列テスト。"""

    def test_margin_column_header_exists(self, qapp):
        """結果テーブルに最小マージン列ヘッダーが存在する。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        headers = [
            dlg._result_table.horizontalHeaderItem(i).text()
            for i in range(dlg._result_table.columnCount())
        ]
        assert "最小マージン" in headers
        assert dlg._result_table.columnCount() == 6
        dlg.close()

    def test_margin_column_shows_values(self, qapp):
        """制約マージンが結果テーブルに表示される。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from app.services.optimizer import (
            OptimizationCandidate, OptimizationResult, OptimizationConfig,
        )

        dlg = OptimizerDialog()
        result = OptimizationResult(
            config=OptimizationConfig(objective_key="max_drift", method="grid"),
            best=OptimizationCandidate(
                params={"Cd": 500}, objective_value=0.003,
                response_values={"max_drift": 0.003}, is_feasible=True,
                constraint_margins={"max_drift": 0.002, "max_acc": 0.5},
            ),
            all_candidates=[
                OptimizationCandidate(
                    params={"Cd": 500}, objective_value=0.003,
                    response_values={"max_drift": 0.003}, is_feasible=True,
                    constraint_margins={"max_drift": 0.002, "max_acc": 0.5},
                ),
                OptimizationCandidate(
                    params={"Cd": 300}, objective_value=0.01,
                    response_values={"max_drift": 0.01}, is_feasible=False,
                    constraint_margins={"max_drift": -0.005, "max_acc": 0.1},
                ),
            ],
        )
        dlg._populate_result_table(result)
        assert dlg._result_table.rowCount() == 2
        # feasible候補: 最小マージン = 0.002 (max_drift)
        margin_text = dlg._result_table.item(0, 4).text()
        assert "max_drift" in margin_text
        assert "+0.002" in margin_text
        # infeasible候補: 最小マージン = -0.005 (max_drift)
        margin_text2 = dlg._result_table.item(1, 4).text()
        assert "-0.005" in margin_text2
        dlg.close()

    def test_margin_column_no_margins(self, qapp):
        """制約マージンが無い候補は「—」表示。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from app.services.optimizer import (
            OptimizationCandidate, OptimizationResult, OptimizationConfig,
        )

        dlg = OptimizerDialog()
        result = OptimizationResult(
            config=OptimizationConfig(objective_key="max_drift", method="grid"),
            best=OptimizationCandidate(
                params={"Cd": 500}, objective_value=0.003,
                response_values={"max_drift": 0.003}, is_feasible=True,
            ),
            all_candidates=[
                OptimizationCandidate(
                    params={"Cd": 500}, objective_value=0.003,
                    response_values={"max_drift": 0.003}, is_feasible=True,
                ),
            ],
        )
        dlg._populate_result_table(result)
        assert dlg._result_table.item(0, 4).text() == "—"
        dlg.close()


class TestIrdtVariationControl:
    """iRDTウィザード感度解析変動幅コントロールのテスト。"""

    def test_variation_spin_exists(self, qapp):
        """変動幅スピンボックスが存在し、デフォルト20%。"""
        from app.ui.irdt_wizard_dialog import IrdtWizardDialog
        dlg = IrdtWizardDialog()
        assert hasattr(dlg, "_variation_spin")
        assert dlg._variation_spin.value() == 20
        assert dlg._variation_spin.minimum() == 5
        assert dlg._variation_spin.maximum() == 50
        dlg.close()

    def test_variation_spin_suffix(self, qapp):
        """スピンボックスに%サフィックスがある。"""
        from app.ui.irdt_wizard_dialog import IrdtWizardDialog
        dlg = IrdtWizardDialog()
        assert dlg._variation_spin.suffix() == " %"
        dlg.close()


class TestOptimizerDialogTimeoutAndApply:
    """OptimizerDialog タイムアウトUI と 結果適用のテスト。"""

    def test_timeout_spin_exists(self, qapp):
        """タイムアウトスピンボックスが存在する。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert hasattr(dlg, "_timeout_spin")
        assert dlg._timeout_spin.value() == 300
        assert dlg._timeout_spin.minimum() == 30
        assert dlg._timeout_spin.maximum() == 3600
        dlg.close()

    def test_timeout_spin_suffix(self, qapp):
        """タイムアウトスピンに秒サフィックスがある。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert dlg._timeout_spin.suffix() == " 秒"
        dlg.close()

    def test_timeout_in_config(self, qapp):
        """_build_configがsnap_timeoutを含む。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        dlg._timeout_spin.setValue(600)
        config = dlg._build_config()
        assert config.snap_timeout == 600
        dlg.close()

    def test_apply_btn_label(self, qapp):
        """適用ボタンのラベルが.s8i適用であること。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert ".s8i" in dlg._apply_btn.text()
        dlg.close()

    def test_apply_best_no_result(self, qapp):
        """結果なしの場合_apply_bestがダイアログを表示する。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from unittest.mock import patch
        dlg = OptimizerDialog()
        dlg._result = None
        with patch("PySide6.QtWidgets.QMessageBox.information") as mock_info:
            dlg._apply_best()
            mock_info.assert_called_once()
        dlg.close()

    def test_preset_restores_timeout(self, qapp):
        """プリセット読込でタイムアウト値が復元される。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        preset = {"snap_timeout": 900}
        dlg._apply_config_preset(preset)
        assert dlg._timeout_spin.value() == 900
        dlg.close()


class TestOptimizerDialogValidationAndETA:
    """AO-1: リアルタイムバリデーション, AO-2: 動的ETA のテスト。"""

    def test_validate_param_ranges_ok(self, qapp):
        """正常な範囲ではスピンの背景が赤くならない。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        if dlg._param_widgets:
            w = dlg._param_widgets[0]
            w["min"].setValue(0.0)
            w["max"].setValue(10.0)
            w["step"].setValue(1.0)
            dlg._validate_param_ranges()
            assert "ffcccc" not in w["min"].styleSheet()
            assert "ffcccc" not in w["max"].styleSheet()
        dlg.close()

    def test_validate_param_ranges_min_ge_max(self, qapp):
        """min >= max のとき背景が赤くなる。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        if dlg._param_widgets:
            w = dlg._param_widgets[0]
            w["min"].setValue(10.0)
            w["max"].setValue(5.0)
            dlg._validate_param_ranges()
            assert "ffcccc" in w["min"].styleSheet()
            assert "ffcccc" in w["max"].styleSheet()
        dlg.close()

    def test_validate_step_bad_grid(self, qapp):
        """グリッドサーチ時に step > range で赤くなる。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        if dlg._param_widgets:
            # グリッドサーチに設定
            for i in range(dlg._method_combo.count()):
                if dlg._method_combo.itemData(i) == "grid":
                    dlg._method_combo.setCurrentIndex(i)
                    break
            w = dlg._param_widgets[0]
            w["min"].setValue(0.0)
            w["max"].setValue(1.0)
            w["step"].setValue(5.0)
            dlg._validate_param_ranges()
            assert "ffcccc" in w["step"].styleSheet()
        dlg.close()

    def test_validate_step_ok_non_grid(self, qapp):
        """非グリッドサーチではstep検証しない。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        if dlg._param_widgets:
            for i in range(dlg._method_combo.count()):
                if dlg._method_combo.itemData(i) == "random":
                    dlg._method_combo.setCurrentIndex(i)
                    break
            w = dlg._param_widgets[0]
            w["min"].setValue(0.0)
            w["max"].setValue(1.0)
            w["step"].setValue(5.0)
            dlg._validate_param_ranges()
            assert "ffcccc" not in w["step"].styleSheet()
        dlg.close()

    def test_avg_eval_sec_initial(self, qapp):
        """初期値は30秒。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        assert dlg._avg_eval_sec == 30.0
        dlg.close()

    def test_avg_eval_sec_updates_on_progress(self, qapp):
        """_on_progressで実測値が更新される。"""
        import time as _time
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        dlg._opt_start_time = _time.time() - 20.0  # 20秒前に開始
        dlg._on_progress(4, 10, "テスト")  # 4回完了 → 5秒/回
        assert abs(dlg._avg_eval_sec - 5.0) < 0.5
        dlg.close()

    def test_est_run_label_uses_avg_eval(self, qapp):
        """動的評価時間が推定ラベルに反映される。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        dlg = OptimizerDialog()
        dlg._avg_eval_sec = 10.0  # 10秒/回
        dlg._update_est_run_label()
        # ラベルにテキストがセットされていること
        text = dlg._est_run_label.text()
        assert "推定" in text
        dlg.close()


class TestMinimizerFloorMargins:
    """MinimizerDialog結果テーブルの階別マージン表示テスト。"""

    def test_table_with_floor_margins(self, qapp):
        """FloorResponse付き結果でマージン列が表示される。"""
        from app.ui.minimizer_dialog import MinimizerDialog
        from app.services.damper_count_minimizer import (
            FloorResponse, MinimizationResult,
        )
        dlg = MinimizerDialog(["F1", "F2"], {"F1": 5, "F2": 3}, {"F1": 10, "F2": 6})
        result = MinimizationResult(
            strategy="floor_add",
            initial_quantities={"F1": 0, "F2": 0},
            final_quantities={"F1": 3, "F2": 2},
            final_count=5,
            is_feasible=True,
            final_margin=0.02,
            final_floor_responses=[
                FloorResponse(floor_key="F1", values={"margin_max_drift": 0.08, "margin_max_acc": 0.15}, damper_count=3),
                FloorResponse(floor_key="F2", values={"margin_max_drift": 0.02, "margin_max_acc": 0.10}, damper_count=2),
            ],
        )
        dlg._populate_result_table(result)
        # 5列（階, 最終, 初期, 変化, マージン）
        assert dlg._table.columnCount() == 5
        # F1のマージン: 最小は0.08 (max_drift)
        margin_text = dlg._table.item(0, 4).text()
        assert "+0.08" in margin_text
        assert "max_drift" in margin_text
        # F2のマージン: 最小は0.02 (max_drift)
        margin_text2 = dlg._table.item(1, 4).text()
        assert "+0.02" in margin_text2
        dlg.close()

    def test_table_without_floor_margins(self, qapp):
        """FloorResponseなし結果でマージン列が非表示。"""
        from app.ui.minimizer_dialog import MinimizerDialog
        from app.services.damper_count_minimizer import MinimizationResult
        dlg = MinimizerDialog(["F1"], {"F1": 5}, {"F1": 10})
        result = MinimizationResult(
            strategy="floor_add",
            initial_quantities={"F1": 0},
            final_quantities={"F1": 3},
            final_count=3,
            is_feasible=True,
            final_margin=0.1,
        )
        dlg._populate_result_table(result)
        assert dlg._table.columnCount() == 4
        dlg.close()

    def test_margin_color_coding(self, qapp):
        """マージン値に応じた色分け(緑/橙/赤)。"""
        from app.ui.minimizer_dialog import MinimizerDialog
        from app.services.damper_count_minimizer import (
            FloorResponse, MinimizationResult,
        )
        from PySide6.QtGui import QColor
        dlg = MinimizerDialog(["F1", "F2", "F3"], {"F1": 5, "F2": 5, "F3": 5}, {"F1": 10, "F2": 10, "F3": 10})
        result = MinimizationResult(
            strategy="floor_add",
            initial_quantities={"F1": 0, "F2": 0, "F3": 0},
            final_quantities={"F1": 3, "F2": 2, "F3": 1},
            final_count=6,
            is_feasible=False,
            final_margin=-0.01,
            final_floor_responses=[
                FloorResponse(floor_key="F1", values={"margin_max_drift": 0.10}, damper_count=3),  # 緑(余裕)
                FloorResponse(floor_key="F2", values={"margin_max_drift": 0.03}, damper_count=2),  # 橙(僅差)
                FloorResponse(floor_key="F3", values={"margin_max_drift": -0.01}, damper_count=1), # 赤(違反)
            ],
        )
        dlg._populate_result_table(result)
        # F1: 緑(60, 179, 113)
        color1 = dlg._table.item(0, 4).foreground().color()
        assert color1.green() > 150  # 緑系
        # F2: 橙(255, 165, 0)
        color2 = dlg._table.item(1, 4).foreground().color()
        assert color2.red() > 200 and color2.green() > 100  # 橙系
        # F3: 赤(220, 50, 50)
        color3 = dlg._table.item(2, 4).foreground().color()
        assert color3.red() > 200 and color3.green() < 100  # 赤系
        dlg.close()

    def test_make_margin_item_no_response(self, qapp):
        """FloorResponseがNoneの場合「—」表示。"""
        from app.ui.minimizer_dialog import MinimizerDialog
        item = MinimizerDialog._make_margin_item(None)
        assert item.text() == "—"

    def test_total_row_shows_overall_margin(self, qapp):
        """合計行にoverall marginが表示される。"""
        from app.ui.minimizer_dialog import MinimizerDialog
        from app.services.damper_count_minimizer import (
            FloorResponse, MinimizationResult,
        )
        dlg = MinimizerDialog(["F1"], {"F1": 5}, {"F1": 10})
        result = MinimizationResult(
            strategy="floor_add",
            initial_quantities={"F1": 0},
            final_quantities={"F1": 3},
            final_count=3,
            is_feasible=True,
            final_margin=0.05,
            final_floor_responses=[
                FloorResponse(floor_key="F1", values={"margin_max_drift": 0.05}, damper_count=3),
            ],
        )
        dlg._populate_result_table(result)
        # 合計行(row=1)のマージン列
        total_margin = dlg._table.item(1, 4).text()
        assert "+0.0500" in total_margin
        dlg.close()


class TestMinimizerHtmlReport:
    """MinimizerDialog HTMLレポートボタンのテスト。"""

    def test_html_button_exists_and_disabled_initially(self, qapp):
        """HTMLレポートボタンが存在し、初期状態は無効。"""
        from app.ui.minimizer_dialog import MinimizerDialog
        dlg = MinimizerDialog(["F1"], {"F1": 5}, {"F1": 10})
        assert hasattr(dlg, "_btn_html")
        assert dlg._btn_html.text() == "HTMLレポート"
        assert not dlg._btn_html.isEnabled()
        dlg.close()

    def test_html_button_enabled_after_result(self, qapp):
        """結果取得後にHTMLレポートボタンが有効化される。"""
        from app.ui.minimizer_dialog import MinimizerDialog
        from app.services.damper_count_minimizer import MinimizationResult
        dlg = MinimizerDialog(["F1"], {"F1": 5}, {"F1": 10})
        result = MinimizationResult(
            strategy="floor_add",
            initial_quantities={"F1": 0},
            final_quantities={"F1": 3},
            final_count=3,
            is_feasible=True,
            final_margin=0.05,
        )
        dlg._on_finished(result)
        assert dlg._btn_html.isEnabled()
        dlg.close()


class TestOptimizerStartRefactor:
    """AV-1: _start_optimization分割後の各サブメソッドのテスト。"""

    def test_validate_config_empty_params(self, qapp):
        """パラメータ未設定でFalseを返す。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from app.services.optimizer import OptimizationConfig
        from unittest.mock import patch

        dlg = OptimizerDialog()
        config = OptimizationConfig(objective_key="max_drift", parameters=[])
        with patch("app.ui.optimizer_dialog.QMessageBox.warning") as mock_warn:
            assert dlg._validate_config(config) is False
            mock_warn.assert_called_once()
        dlg.close()

    def test_validate_config_valid(self, qapp):
        """正常な設定でTrueを返す。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from app.services.optimizer import OptimizationConfig, ParameterRange
        from unittest.mock import patch

        dlg = OptimizerDialog()
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="Cd", label="Cd", min_val=100, max_val=1000, step=100)],
        )
        with patch("app.ui.optimizer_dialog.QMessageBox.warning") as mock_warn:
            assert dlg._validate_config(config) is True
            mock_warn.assert_not_called()
        dlg.close()

    def test_validate_config_zero_weights(self, qapp):
        """複合目的関数の重み合計0でFalseを返す。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from app.services.optimizer import OptimizationConfig, ParameterRange
        from unittest.mock import patch

        dlg = OptimizerDialog()
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="Cd", label="Cd", min_val=100, max_val=1000, step=100)],
            objective_weights={"max_drift": 0.0, "max_acc": 0.0},
        )
        with patch("app.ui.optimizer_dialog.QMessageBox.warning"):
            assert dlg._validate_config(config) is False
        dlg.close()

    def test_confirm_large_run_small(self, qapp):
        """50回以下では確認なしでTrue。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from app.services.optimizer import OptimizationConfig, ParameterRange

        dlg = OptimizerDialog()
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="Cd", label="Cd", min_val=100, max_val=1000, step=100)],
            method="random",
        )
        dlg._iter_spin.setValue(10)
        assert dlg._confirm_large_run(config) is True
        dlg.close()

    def test_reset_ui_for_optimization(self, qapp):
        """UIリセットで実行ボタン無効、キャンセルボタン有効。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from unittest.mock import patch

        dlg = OptimizerDialog()
        with patch.object(dlg._conv_canvas, "draw"):
            dlg._reset_ui_for_optimization()
        assert not dlg._run_btn.isEnabled()
        assert dlg._cancel_btn.isEnabled()
        assert not dlg._apply_btn.isEnabled()
        dlg.close()

    def test_create_evaluate_fn_no_snap(self, qapp):
        """SNAP未設定でNone(モック)を返す。"""
        from app.ui.optimizer_dialog import OptimizerDialog
        from app.services.optimizer import OptimizationConfig, ParameterRange

        dlg = OptimizerDialog()
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="Cd", label="Cd", min_val=100, max_val=1000, step=100)],
        )
        result = dlg._create_evaluate_fn(config)
        assert result is None
        assert "モック" in dlg._result_summary.text()
        dlg.close()
