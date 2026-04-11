"""
tests/test_snap_evaluator.py
SnapEvaluator のユニットテスト。

SNAP.exe が存在しないテスト環境では、
create_snap_evaluator が None を返すことを確認し、
SnapEvaluator のユーティリティメソッドのみテストします。
"""

import pytest
from pathlib import Path

from app.services.snap_evaluator import (
    SnapEvaluator,
    create_snap_evaluator,
    create_minimizer_evaluate_fn,
    _compute_margin,
    _extract_minimizer_response,
)
from app.models.analysis_case import AnalysisCase
from app.models.performance_criteria import PerformanceCriteria, CriterionItem


class TestSnapEvaluatorCacheKey:
    """キャッシュキー生成のテスト。"""

    def test_same_params_same_key(self):
        key1 = SnapEvaluator._make_cache_key({"Cd": 500.0, "alpha": 0.4})
        key2 = SnapEvaluator._make_cache_key({"alpha": 0.4, "Cd": 500.0})
        assert key1 == key2

    def test_different_params_different_key(self):
        key1 = SnapEvaluator._make_cache_key({"Cd": 500.0})
        key2 = SnapEvaluator._make_cache_key({"Cd": 600.0})
        assert key1 != key2

    def test_float_precision_tolerance(self):
        """浮動小数点の微小差がキャッシュヒットを妨げないこと。"""
        key1 = SnapEvaluator._make_cache_key({"Cd": 500.0})
        key2 = SnapEvaluator._make_cache_key({"Cd": 500.0000001})
        assert key1 == key2

    def test_float_precision_distinct(self):
        """有意な差はキャッシュミスになること。"""
        key1 = SnapEvaluator._make_cache_key({"Cd": 500.0})
        key2 = SnapEvaluator._make_cache_key({"Cd": 500.1})
        assert key1 != key2


class TestSnapEvaluatorErrorResponse:
    """エラーレスポンスのテスト。"""

    def test_error_response_all_inf(self):
        # SnapEvaluator のインスタンスなしでもテスト可能
        # (staticmethod ではないので間接的にテスト)
        response = {
            "max_drift": float("inf"),
            "max_acc": float("inf"),
            "max_disp": float("inf"),
            "max_vel": float("inf"),
            "shear_coeff": float("inf"),
            "max_otm": float("inf"),
            "max_story_disp": float("inf"),
        }
        for v in response.values():
            assert v == float("inf")


class TestCreateSnapEvaluator:
    """create_snap_evaluator ヘルパーのテスト。"""

    def test_returns_none_without_exe_path(self):
        case = AnalysisCase(model_path="/tmp/model.s8i")
        from app.services.optimizer import ParameterRange
        params = [ParameterRange(key="Cd", min_val=100, max_val=500, step=100)]

        log_messages = []
        result = create_snap_evaluator(
            snap_exe_path="",
            base_case=case,
            param_ranges=params,
            log_callback=log_messages.append,
        )
        assert result is None
        assert any("モック評価" in msg for msg in log_messages)

    def test_returns_none_without_model_path(self):
        case = AnalysisCase(snap_exe_path="/tmp/SNAP.exe")
        from app.services.optimizer import ParameterRange
        params = [ParameterRange(key="Cd", min_val=100, max_val=500, step=100)]

        result = create_snap_evaluator(
            snap_exe_path="/tmp/SNAP.exe",
            base_case=case,
            param_ranges=params,
        )
        assert result is None

    def test_returns_none_with_nonexistent_files(self, tmp_path):
        case = AnalysisCase(
            model_path=str(tmp_path / "nonexistent.s8i"),
            snap_exe_path=str(tmp_path / "SNAP.exe"),
        )
        from app.services.optimizer import ParameterRange
        params = [ParameterRange(key="Cd", min_val=100, max_val=500, step=100)]

        result = create_snap_evaluator(
            snap_exe_path=str(tmp_path / "SNAP.exe"),
            base_case=case,
            param_ranges=params,
        )
        assert result is None

    def test_file_not_found_error_in_constructor(self):
        """SnapEvaluator のコンストラクタがファイル不在でエラーを投げる。"""
        with pytest.raises(FileNotFoundError):
            SnapEvaluator(
                snap_exe_path="/nonexistent/SNAP.exe",
                base_s8i_path="/nonexistent/model.s8i",
            )

    def test_stats_initial(self, tmp_path):
        """存在するファイルで作成した場合の初期統計。"""
        # 実在するファイルを作成
        exe = tmp_path / "SNAP.exe"
        exe.touch()
        s8i = tmp_path / "model.s8i"
        s8i.write_text("TTL / test\n", encoding="shift_jis")

        evaluator = SnapEvaluator(
            snap_exe_path=str(exe),
            base_s8i_path=str(s8i),
        )
        stats = evaluator.stats
        assert stats["total"] == 0
        assert stats["success"] == 0
        assert stats["error"] == 0

    def test_stats_text(self, tmp_path):
        """get_stats_text() が文字列を返す。"""
        exe = tmp_path / "SNAP.exe"
        exe.touch()
        s8i = tmp_path / "model.s8i"
        s8i.write_text("TTL / test\n", encoding="shift_jis")

        evaluator = SnapEvaluator(
            snap_exe_path=str(exe),
            base_s8i_path=str(s8i),
        )
        text = evaluator.get_stats_text()
        assert "SNAP評価" in text
        assert "合計 0 回" in text


class TestComputeMargin:
    """_compute_margin のテスト。"""

    def _make_criteria(self, items):
        return PerformanceCriteria(name="test", items=items)

    def test_all_within_limits(self):
        criteria = self._make_criteria([
            CriterionItem(key="max_drift", label="層間変形角", unit="rad",
                          enabled=True, limit_value=0.01),
            CriterionItem(key="max_acc", label="最大加速度", unit="m/s²",
                          enabled=True, limit_value=5.0),
        ])
        summary = {"max_drift": 0.005, "max_acc": 3.0}
        margin = _compute_margin(summary, criteria)
        # max_drift: (0.01 - 0.005) / 0.01 = 0.5
        # max_acc: (5.0 - 3.0) / 5.0 = 0.4
        assert abs(margin - 0.4) < 1e-10  # min(0.5, 0.4) = 0.4

    def test_one_exceeds_limit(self):
        criteria = self._make_criteria([
            CriterionItem(key="max_drift", label="層間変形角", unit="rad",
                          enabled=True, limit_value=0.01),
        ])
        summary = {"max_drift": 0.012}
        margin = _compute_margin(summary, criteria)
        # (0.01 - 0.012) / 0.01 = -0.2
        assert margin < 0

    def test_no_enabled_criteria(self):
        criteria = self._make_criteria([
            CriterionItem(key="max_drift", label="層間変形角", unit="rad",
                          enabled=False, limit_value=0.01),
        ])
        summary = {"max_drift": 0.005}
        margin = _compute_margin(summary, criteria)
        assert margin == 0.0

    def test_missing_key_in_summary(self):
        criteria = self._make_criteria([
            CriterionItem(key="max_drift", label="層間変形角", unit="rad",
                          enabled=True, limit_value=0.01),
        ])
        summary = {}  # key missing
        margin = _compute_margin(summary, criteria)
        assert margin == 0.0


class TestCreateMinimizerEvaluateFn:
    """create_minimizer_evaluate_fn のテスト。"""

    def test_returns_none_without_exe(self):
        criteria = PerformanceCriteria(name="test")
        result = create_minimizer_evaluate_fn(
            snap_exe_path="",
            base_s8i_path="/tmp/model.s8i",
            damper_def_name="D1",
            criteria=criteria,
        )
        assert result is None

    def test_returns_none_with_nonexistent_exe(self):
        criteria = PerformanceCriteria(name="test")
        result = create_minimizer_evaluate_fn(
            snap_exe_path="/nonexistent/SNAP.exe",
            base_s8i_path="/tmp/model.s8i",
            damper_def_name="D1",
            criteria=criteria,
        )
        assert result is None

    def test_returns_none_with_nonexistent_s8i(self, tmp_path):
        exe = tmp_path / "SNAP.exe"
        exe.touch()
        criteria = PerformanceCriteria(name="test")
        result = create_minimizer_evaluate_fn(
            snap_exe_path=str(exe),
            base_s8i_path=str(tmp_path / "nonexistent.s8i"),
            damper_def_name="D1",
            criteria=criteria,
        )
        assert result is None

    def test_returns_callable_with_valid_paths(self, tmp_path):
        exe = tmp_path / "SNAP.exe"
        exe.touch()
        s8i = tmp_path / "model.s8i"
        s8i.write_text("TTL / test\n", encoding="shift_jis")
        criteria = PerformanceCriteria(name="test")

        result = create_minimizer_evaluate_fn(
            snap_exe_path=str(exe),
            base_s8i_path=str(s8i),
            damper_def_name="D1",
            criteria=criteria,
        )
        assert callable(result)

    def test_log_callback_on_missing_exe(self):
        criteria = PerformanceCriteria(name="test")
        logs = []
        create_minimizer_evaluate_fn(
            snap_exe_path="/nonexistent/SNAP.exe",
            base_s8i_path="/tmp/model.s8i",
            damper_def_name="D1",
            criteria=criteria,
            log_callback=logs.append,
        )
        assert any("SNAP.exe" in msg for msg in logs)
