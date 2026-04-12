"""Tests for app.services.damper_count_minimizer (新インターフェース)."""

from __future__ import annotations

from typing import Dict

import pytest

from app.services.damper_count_minimizer import (
    EvaluationResult,
    FloorResponse,
    MinimizationResult,
    STRATEGIES,
    STRATEGY_CATEGORIES,
    minimize_binary_search,
    minimize_damper_count,
    minimize_de,
    minimize_floor_add,
    minimize_floor_remove,
    minimize_ga,
    minimize_linear_search,
    minimize_nelder_mead,
    minimize_pso,
    minimize_random,
    minimize_sa,
    minimize_sqp,
)


# ---------------------------------------------------------------------------
# モック評価関数: 各階の本数に比例して性能が改善される
# ---------------------------------------------------------------------------


def make_evaluator(weights: Dict[str, float], threshold: float):
    """
    weights: 各階の1本あたりの性能改善量
    threshold: 基準値（合計改善量がこれ以上ならOK）
    """
    eval_count = [0]

    def eval_fn(quantities: Dict[str, int]) -> EvaluationResult:
        eval_count[0] += 1
        total_perf = sum(quantities.get(k, 0) * w for k, w in weights.items())
        total_count = sum(quantities.values())
        margin = (total_perf - threshold) / max(threshold, 1e-10)
        is_feasible = total_perf >= threshold

        floor_responses = []
        for k in sorted(quantities.keys()):
            perf = quantities.get(k, 0) * weights.get(k, 0)
            fr = FloorResponse(
                floor_key=k,
                values={
                    "max_drift": max(0.01 - perf * 0.001, 0.001),
                    "margin_max_drift": margin,
                },
                damper_count=quantities.get(k, 0),
            )
            floor_responses.append(fr)

        return EvaluationResult(
            floor_responses=floor_responses,
            total_count=total_count,
            is_feasible=is_feasible,
            worst_margin=margin,
            summary={"max_drift": 0.01 - total_perf * 0.001},
        )
    return eval_fn


FLOOR_KEYS = ["F1", "F2", "F3"]
WEIGHTS = {"F1": 3.0, "F2": 5.0, "F3": 2.0}
THRESHOLD = 10.0  # F1*3 + F2*5 + F3*2 >= 10 → 例: F1=1,F2=1,F3=1 → 10 ちょうどOK
MAX_Q = {"F1": 5, "F2": 5, "F3": 5}


class TestFloorAdd:
    def test_finds_feasible_solution(self):
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        result = minimize_floor_add(FLOOR_KEYS, MAX_Q, evalfn)
        assert result.is_feasible
        assert result.final_count <= 15  # 最大でも全階5本
        assert result.strategy == "floor_add"
        assert len(result.history) > 0

    def test_records_history(self):
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        result = minimize_floor_add(FLOOR_KEYS, MAX_Q, evalfn)
        assert result.evaluations > 0
        for step in result.history:
            assert isinstance(step.quantities, dict)


class TestFloorRemove:
    def test_reduces_from_initial(self):
        initial = {"F1": 3, "F2": 3, "F3": 3}
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        result = minimize_floor_remove(FLOOR_KEYS, initial, evalfn)
        assert result.is_feasible
        assert result.final_count <= sum(initial.values())
        assert result.strategy == "floor_remove"

    def test_infeasible_initial(self):
        initial = {"F1": 0, "F2": 0, "F3": 0}
        evalfn = make_evaluator(WEIGHTS, 100.0)
        result = minimize_floor_remove(FLOOR_KEYS, initial, evalfn)
        assert not result.is_feasible


class TestBinarySearch:
    def test_finds_minimum_uniform(self):
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        result = minimize_binary_search(FLOOR_KEYS, 5, evalfn)
        assert result.is_feasible
        # 全階1本 → 3+5+2=10 → ちょうどOK
        assert result.final_count <= 6  # 最大でも各2本
        assert result.strategy == "binary_search"


class TestLinearSearch:
    def test_finds_minimum(self):
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        result = minimize_linear_search(FLOOR_KEYS, 5, evalfn)
        assert result.is_feasible
        assert result.strategy == "linear_search"


class TestGA:
    def test_finds_solution(self):
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        result = minimize_ga(FLOOR_KEYS, MAX_Q, evalfn,
                             population_size=10, generations=10)
        assert result.strategy == "ga"
        assert result.evaluations > 0
        assert len(result.history) > 0


class TestSA:
    def test_finds_solution(self):
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        result = minimize_sa(FLOOR_KEYS, MAX_Q, evalfn, max_iterations=30)
        assert result.strategy == "sa"
        assert result.evaluations > 0


class TestPSO:
    def test_finds_solution(self):
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        result = minimize_pso(FLOOR_KEYS, MAX_Q, evalfn,
                              n_particles=8, max_iterations=10)
        assert result.strategy == "pso"
        assert result.evaluations > 0


class TestDE:
    def test_finds_solution(self):
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        result = minimize_de(FLOOR_KEYS, MAX_Q, evalfn,
                             population_size=10, max_iterations=10)
        assert result.strategy == "de"
        assert result.evaluations > 0


class TestSQP:
    def test_finds_solution(self):
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        result = minimize_sqp(FLOOR_KEYS, MAX_Q, evalfn)
        assert result.strategy == "sqp"
        assert result.evaluations > 0


class TestNelderMead:
    def test_finds_solution(self):
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        result = minimize_nelder_mead(FLOOR_KEYS, MAX_Q, evalfn)
        assert result.strategy == "nelder_mead"
        assert result.evaluations > 0


class TestRandom:
    def test_finds_solution(self):
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        result = minimize_random(FLOOR_KEYS, MAX_Q, evalfn, max_iterations=20)
        assert result.strategy == "random"
        assert result.evaluations == 20


class TestEntryPoint:
    def test_unknown_strategy_raises(self):
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        with pytest.raises(ValueError):
            minimize_damper_count(FLOOR_KEYS, MAX_Q, evalfn, strategy="foo")

    def test_all_strategies_registered(self):
        assert len(STRATEGIES) == 12
        for cat_keys in STRATEGY_CATEGORIES.values():
            for key in cat_keys:
                assert key in STRATEGIES

    def test_floor_add_via_entry(self):
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        result = minimize_damper_count(FLOOR_KEYS, MAX_Q, evalfn,
                                       strategy="floor_add")
        assert result.strategy == "floor_add"

    def test_binary_search_via_entry(self):
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        result = minimize_damper_count(FLOOR_KEYS, MAX_Q, evalfn,
                                       strategy="binary_search")
        assert result.strategy == "binary_search"


class TestSACooling:
    """SA指数冷却スケジュールのテスト。"""

    def test_sa_explores_late_iterations(self):
        """指数冷却により後半でも温度が十分正（>0）であること。"""
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        temps = []

        def capture_progress(step):
            if step.note and "T=" in step.note:
                t_str = step.note.split("T=")[1].split()[0]
                temps.append(float(t_str))

        result = minimize_sa(FLOOR_KEYS, MAX_Q, evalfn,
                             max_iterations=100, progress_cb=capture_progress)
        assert result.strategy == "sa"
        # 指数冷却: 中盤（全体の半分付近）でまだ有意な温度が残る
        if len(temps) >= 2:
            mid = len(temps) // 2
            assert temps[mid] > 0.1, f"中盤温度が低すぎ: {temps[mid]}"

    def test_sa_completes_all_iterations(self):
        """指数冷却では temp<=0 によるbreakが起きず全反復を完了する。"""
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        step_count = [0]

        def count_progress(step):
            step_count[0] += 1

        max_it = 50
        result = minimize_sa(FLOOR_KEYS, MAX_Q, evalfn,
                             max_iterations=max_it, progress_cb=count_progress)
        # init + 周期的ステップ + final の分、少なくとも3ステップは記録
        assert step_count[0] >= 3
        assert result.evaluations >= max_it


class TestSummaryText:
    def test_summary_contains_key_info(self):
        evalfn = make_evaluator(WEIGHTS, THRESHOLD)
        result = minimize_floor_add(FLOOR_KEYS, MAX_Q, evalfn)
        text = result.summary_text()
        assert "ダンパー本数最小化結果" in text
        assert "floor_add" in text
        assert "最終合計本数" in text


class TestDataClasses:
    def test_floor_response(self):
        fr = FloorResponse(floor_key="F1", values={"max_drift": 0.005}, damper_count=3)
        assert fr.floor_key == "F1"
        assert fr.damper_count == 3

    def test_evaluation_result(self):
        er = EvaluationResult(total_count=10, is_feasible=True, worst_margin=0.1)
        assert er.total_count == 10
        assert er.is_feasible


class TestDEAdaptive:
    """DE jDE自己適応F/CRのテスト。"""

    def test_adaptive_de_finds_solution(self):
        fn = make_evaluator({"F1": 1.0, "F2": 0.8}, 3.0)
        result = minimize_de(
            ["F1", "F2"], {"F1": 5, "F2": 5}, fn,
            population_size=10, max_iterations=10, adaptive=True,
        )
        assert isinstance(result, MinimizationResult)
        assert result.evaluations > 0

    def test_non_adaptive_de_works(self):
        fn = make_evaluator({"F1": 1.0}, 2.0)
        result = minimize_de(
            ["F1"], {"F1": 5}, fn,
            population_size=8, max_iterations=5, adaptive=False,
        )
        assert isinstance(result, MinimizationResult)
        assert result.evaluations > 0

    def test_adaptive_de_converges(self):
        fn = make_evaluator({"F1": 1.5, "F2": 1.0}, 3.0)
        result = minimize_de(
            ["F1", "F2"], {"F1": 5, "F2": 5}, fn,
            population_size=15, max_iterations=20, adaptive=True,
        )
        assert result.is_feasible


class TestAutoPenaltyWeight:
    """_auto_penalty_weight のテスト。"""

    def test_auto_penalty_scales_with_max_quantities(self):
        from app.services.damper_count_minimizer import _auto_penalty_weight
        w1 = _auto_penalty_weight({"F1": 5, "F2": 5})
        w2 = _auto_penalty_weight({"F1": 50, "F2": 50})
        assert w2 > w1  # 問題スケールが大きいとペナルティも大きい

    def test_auto_penalty_minimum(self):
        from app.services.damper_count_minimizer import _auto_penalty_weight
        w = _auto_penalty_weight({"F1": 1})
        assert w >= 100.0


# ===========================================================================
# Phase AF: 乱数シード制御 + Nelder-Mead適応許容値
# ===========================================================================

# 簡易評価関数: 合計本数が閾値(3)を超えればfeasible
_simple_eval = make_evaluator({"F1": 1.0, "F2": 1.0}, 3.0)


class TestRandomSeedMinimizer:
    """minimize_damper_count の random_seed 引数テスト。"""

    def test_random_seed_reproducibility(self):
        """同じシードでminimize_randomが同一結果を返すこと。"""
        floor_keys = ["F1", "F2"]
        max_q = {"F1": 5, "F2": 5}

        results = []
        for _ in range(2):
            r = minimize_damper_count(
                floor_keys, max_q, _simple_eval,
                strategy="random", random_seed=42,
                max_iterations=10,
            )
            results.append(r)

        assert results[0].final_quantities == results[1].final_quantities
        assert results[0].final_count == results[1].final_count

    def test_random_seed_ga_reproducibility(self):
        """同じシードでGAが同一結果を返すこと。"""
        floor_keys = ["F1", "F2"]
        max_q = {"F1": 5, "F2": 5}

        results = []
        for _ in range(2):
            r = minimize_damper_count(
                floor_keys, max_q, _simple_eval,
                strategy="ga", random_seed=99,
                population_size=8, generations=3,
            )
            results.append(r)

        assert results[0].final_quantities == results[1].final_quantities

    def test_random_seed_none_default(self):
        """random_seed=Noneでエラーなく実行できること。"""
        floor_keys = ["F1"]
        max_q = {"F1": 3}
        r = minimize_damper_count(
            floor_keys, max_q, _simple_eval,
            strategy="random", random_seed=None,
            max_iterations=5,
        )
        assert r.final_quantities is not None


class TestNelderMeadAdaptiveTol:
    """Nelder-Mead適応的許容値のテスト。"""

    def test_nelder_mead_runs_with_adaptive_tol(self):
        """適応的許容値でNelder-Meadが正常に完了すること。"""
        floor_keys = ["F1", "F2"]
        max_q = {"F1": 10, "F2": 10}
        r = minimize_nelder_mead(floor_keys, max_q, _simple_eval)
        assert r.strategy == "nelder_mead"
        assert r.final_quantities is not None
        assert r.evaluations > 0

    def test_nelder_mead_large_scale(self):
        """大きなmax_quantitiesでもNelder-Meadが正常に完了すること。"""
        floor_keys = ["F1", "F2"]
        max_q = {"F1": 100, "F2": 100}
        r = minimize_nelder_mead(floor_keys, max_q, _simple_eval)
        assert r.strategy == "nelder_mead"
        assert r.final_quantities is not None
