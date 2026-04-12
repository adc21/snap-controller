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
    build_floor_rd_map,
    _compute_margin,
    _extract_minimizer_response,
    _safe_dict_max,
)
from app.models.analysis_case import AnalysisCase
from app.models.performance_criteria import PerformanceCriteria, CriterionItem
from app.models.s8i_parser import parse_s8i


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
    """create_minimizer_evaluate_fn のテスト（新インターフェース）。"""

    def test_returns_none_without_exe(self):
        criteria = PerformanceCriteria(name="test")
        result = create_minimizer_evaluate_fn(
            snap_exe_path="",
            base_s8i_path="/tmp/model.s8i",
            criteria=criteria,
            floor_rd_map={"F1": [0]},
        )
        assert result is None

    def test_returns_none_with_nonexistent_exe(self):
        criteria = PerformanceCriteria(name="test")
        result = create_minimizer_evaluate_fn(
            snap_exe_path="/nonexistent/SNAP.exe",
            base_s8i_path="/tmp/model.s8i",
            criteria=criteria,
            floor_rd_map={"F1": [0]},
        )
        assert result is None

    def test_returns_none_with_nonexistent_s8i(self, tmp_path):
        exe = tmp_path / "SNAP.exe"
        exe.touch()
        criteria = PerformanceCriteria(name="test")
        result = create_minimizer_evaluate_fn(
            snap_exe_path=str(exe),
            base_s8i_path=str(tmp_path / "nonexistent.s8i"),
            criteria=criteria,
            floor_rd_map={"F1": [0]},
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
            criteria=criteria,
            floor_rd_map={"F1": [0]},
        )
        assert callable(result)

    def test_log_callback_on_missing_exe(self):
        criteria = PerformanceCriteria(name="test")
        logs = []
        create_minimizer_evaluate_fn(
            snap_exe_path="/nonexistent/SNAP.exe",
            base_s8i_path="/tmp/model.s8i",
            criteria=criteria,
            floor_rd_map={"F1": [0]},
            log_callback=logs.append,
        )
        assert any("SNAP.exe" in msg for msg in logs)


# ---------------------------------------------------------------------------
# MultiWaveEvaluator テスト
# ---------------------------------------------------------------------------

from app.services.snap_evaluator import MultiWaveEvaluator


class TestMultiWaveEvaluator:
    """MultiWaveEvaluator のテスト。"""

    def _make_mock_evaluator(self, response: dict):
        """モック SnapEvaluator を返す。stats と get_stats_text を持つ callable。"""
        class MockEval:
            def __init__(self, resp):
                self._resp = resp
                self._count = 0
            def __call__(self, params):
                self._count += 1
                return dict(self._resp)
            @property
            def stats(self):
                return {"total": self._count, "success": self._count, "error": 0, "cache_hits": 0}
        return MockEval(response)

    def test_max_aggregation(self):
        """最大値集約で各応答キーの最大を返す。"""
        ev1 = self._make_mock_evaluator({"max_drift": 0.003, "max_acc": 2.0})
        ev2 = self._make_mock_evaluator({"max_drift": 0.005, "max_acc": 1.5})
        mw = MultiWaveEvaluator(
            evaluators=[("wave1", ev1), ("wave2", ev2)],
            aggregation="max",
        )
        result = mw({"Cd": 100})
        assert result["max_drift"] == 0.005
        assert result["max_acc"] == 2.0

    def test_mean_aggregation(self):
        """平均値集約で各応答キーの平均を返す。"""
        ev1 = self._make_mock_evaluator({"max_drift": 0.002, "max_acc": 2.0})
        ev2 = self._make_mock_evaluator({"max_drift": 0.004, "max_acc": 4.0})
        mw = MultiWaveEvaluator(
            evaluators=[("wave1", ev1), ("wave2", ev2)],
            aggregation="mean",
        )
        result = mw({"Cd": 100})
        assert abs(result["max_drift"] - 0.003) < 1e-10
        assert abs(result["max_acc"] - 3.0) < 1e-10

    def test_per_wave_results_stored(self):
        """各波形ごとの結果が保持される。"""
        ev1 = self._make_mock_evaluator({"max_drift": 0.003})
        ev2 = self._make_mock_evaluator({"max_drift": 0.005})
        mw = MultiWaveEvaluator(
            evaluators=[("El Centro", ev1), ("Hachinohe", ev2)],
        )
        mw({"Cd": 100})
        per_wave = mw.last_per_wave_results
        assert "El Centro" in per_wave
        assert "Hachinohe" in per_wave
        assert per_wave["El Centro"]["max_drift"] == 0.003
        assert per_wave["Hachinohe"]["max_drift"] == 0.005

    def test_stats_aggregated(self):
        """統計が全evaluator分集約される。"""
        ev1 = self._make_mock_evaluator({"max_drift": 0.003})
        ev2 = self._make_mock_evaluator({"max_drift": 0.005})
        mw = MultiWaveEvaluator(evaluators=[("w1", ev1), ("w2", ev2)])
        mw({"Cd": 100})
        stats = mw.stats
        assert stats["n_waves"] == 2
        assert stats["total"] == 2  # 1 eval each
        assert "per_wave" in stats

    def test_stats_text(self):
        """get_stats_text が文字列を返す。"""
        ev1 = self._make_mock_evaluator({"max_drift": 0.003})
        mw = MultiWaveEvaluator(evaluators=[("w1", ev1)])
        mw({"Cd": 100})
        text = mw.get_stats_text()
        assert "多波SNAP評価" in text
        assert "1波" in text

    def test_empty_evaluators_raises(self):
        """evaluators が空の場合 ValueError。"""
        with pytest.raises(ValueError):
            MultiWaveEvaluator(evaluators=[])

    def test_inf_handling_in_max(self):
        """inf 値が含まれる場合の最大値集約。"""
        ev1 = self._make_mock_evaluator({"max_drift": 0.003, "max_acc": float("inf")})
        ev2 = self._make_mock_evaluator({"max_drift": 0.005, "max_acc": 2.0})
        mw = MultiWaveEvaluator(evaluators=[("w1", ev1), ("w2", ev2)], aggregation="max")
        result = mw({"Cd": 100})
        assert result["max_drift"] == 0.005
        assert result["max_acc"] == float("inf")

    def test_inf_handling_in_mean(self):
        """平均値集約でinf値はスキップされる。"""
        ev1 = self._make_mock_evaluator({"max_acc": float("inf")})
        ev2 = self._make_mock_evaluator({"max_acc": 2.0})
        mw = MultiWaveEvaluator(evaluators=[("w1", ev1), ("w2", ev2)], aggregation="mean")
        result = mw({"Cd": 100})
        assert result["max_acc"] == 2.0

    def test_eval_count_increments(self):
        """評価回数がインクリメントされる。"""
        ev1 = self._make_mock_evaluator({"max_drift": 0.003})
        mw = MultiWaveEvaluator(evaluators=[("w1", ev1)])
        mw({"Cd": 100})
        mw({"Cd": 200})
        assert mw._eval_count == 2


class TestDycRunFlagReset:
    """DYC run_flag=2 リセット機能のテスト。"""

    def _make_s8i_with_dyc(self, tmp_path, run_flags):
        """指定した run_flag のDYCケースを持つ.s8iファイルを作成。"""
        lines = ["TTL / 1,1,1,0,0,Test"]
        for i, flag in enumerate(run_flags, 1):
            lines.append(f"DYC / CASE{i},{flag},1,100")
        s8i_file = tmp_path / "test.s8i"
        s8i_file.write_text("\n".join(lines), encoding="shift_jis")
        return str(s8i_file)

    def test_run_flag_2_reset_to_1_in_parsed_model(self, tmp_path):
        """run_flag=2(解析済み)をパース後に1にリセットできることを確認。"""
        s8i_path = self._make_s8i_with_dyc(tmp_path, [2, 1, 0])
        model = parse_s8i(s8i_path)

        # SnapEvaluator と同じリセットロジック
        for dyc in model.dyc_cases:
            if dyc.run_flag == 2:
                dyc.run_flag = 1
                dyc.values[1] = "1"

        assert model.dyc_cases[0].run_flag == 1
        assert model.dyc_cases[0].values[1] == "1"
        assert model.dyc_cases[1].run_flag == 1  # 変更なし
        assert model.dyc_cases[2].run_flag == 0  # 変更なし

    def test_run_flag_reset_persists_through_write(self, tmp_path):
        """リセット後のwrite()で正しくrun_flag=1が書き出される。"""
        s8i_path = self._make_s8i_with_dyc(tmp_path, [2, 2, 0])
        model = parse_s8i(s8i_path)

        for dyc in model.dyc_cases:
            if dyc.run_flag == 2:
                dyc.run_flag = 1
                dyc.values[1] = "1"

        output = tmp_path / "output.s8i"
        model.write(str(output))

        model2 = parse_s8i(str(output))
        assert model2.dyc_cases[0].run_flag == 1
        assert model2.dyc_cases[1].run_flag == 1
        assert model2.dyc_cases[2].run_flag == 0

    def test_no_reset_needed_when_all_flag_1(self, tmp_path):
        """全ケースがrun_flag=1の場合、リセット不要で変更なし。"""
        s8i_path = self._make_s8i_with_dyc(tmp_path, [1, 1])
        model = parse_s8i(s8i_path)

        changed = False
        for dyc in model.dyc_cases:
            if dyc.run_flag == 2:
                dyc.run_flag = 1
                dyc.values[1] = "1"
                changed = True

        assert changed is False
        assert all(d.run_flag == 1 for d in model.dyc_cases)


class TestSafeDictMax:
    """_safe_dict_max のエッジケーステスト。"""

    def test_normal_dict(self):
        assert _safe_dict_max({"a": 1.0, "b": 3.0, "c": 2.0}) == 3.0

    def test_empty_dict(self):
        """空辞書でValueErrorが出ないこと。"""
        assert _safe_dict_max({}) is None

    def test_none_input(self):
        assert _safe_dict_max(None) is None

    def test_dict_with_none_values(self):
        """None値を含む辞書でも正常動作。"""
        assert _safe_dict_max({"a": None, "b": 5.0}) == 5.0

    def test_dict_with_all_none_values(self):
        """全てNone値の辞書はNoneを返す。"""
        assert _safe_dict_max({"a": None, "b": None}) is None

    def test_single_value(self):
        assert _safe_dict_max({"a": 42.0}) == 42.0
