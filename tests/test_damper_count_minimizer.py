"""Tests for app.services.damper_count_minimizer."""

from __future__ import annotations

from typing import Dict, List, Tuple

import pytest

from app.services.damper_count_minimizer import (
    minimize_damper_count,
    minimize_exhaustive,
    minimize_greedy_add,
    minimize_greedy_remove,
)


# ---------------------------------------------------------------------------
# 仮想評価関数: 各位置 i のダンパーが性能を w_i だけ改善する
# 全層の改善量合計が threshold 以上なら基準を満たす
# ---------------------------------------------------------------------------

def make_evaluator(weights: List[float], threshold: float):
    def eval_fn(placement: List[bool]) -> Tuple[Dict[str, float], bool, float]:
        total = sum(w for w, on in zip(weights, placement) if on)
        margin = (total - threshold) / threshold
        return {"perf": total}, total >= threshold, margin
    return eval_fn


class TestGreedyRemove:
    def test_removes_lowest_weight_dampers(self):
        weights = [5.0, 5.0, 5.0, 0.1, 0.1]
        evalfn = make_evaluator(weights, threshold=12.0)  # 3本で達成可能
        result = minimize_greedy_remove(5, evalfn)
        assert result.is_feasible
        # 軽い 2 本が撤去され、重い 3 本が残るはず
        assert result.final_count == 3
        assert result.final_placement[0] is True
        assert result.final_placement[1] is True
        assert result.final_placement[2] is True

    def test_infeasible_initial(self):
        weights = [0.1, 0.1, 0.1]
        evalfn = make_evaluator(weights, threshold=100.0)
        result = minimize_greedy_remove(3, evalfn)
        assert not result.is_feasible
        assert result.final_count == 3  # 撤去できず満載のまま

    def test_required_positions_kept(self):
        weights = [0.1, 5.0, 5.0, 5.0]
        evalfn = make_evaluator(weights, threshold=10.0)
        result = minimize_greedy_remove(4, evalfn, required_positions=[0])
        assert result.final_placement[0] is True  # 必須位置は残る
        assert result.is_feasible


class TestGreedyAdd:
    def test_adds_best_position_first(self):
        weights = [1.0, 10.0, 1.0, 1.0]
        evalfn = make_evaluator(weights, threshold=9.0)
        result = minimize_greedy_add(4, evalfn)
        assert result.is_feasible
        # 位置 1 (weight=10) が最初に選ばれ、1 本で達成
        assert result.final_count == 1
        assert result.final_placement[1] is True

    def test_multiple_needed(self):
        weights = [3.0, 3.0, 3.0, 3.0]
        evalfn = make_evaluator(weights, threshold=9.0)
        result = minimize_greedy_add(4, evalfn)
        assert result.is_feasible
        assert result.final_count == 3


class TestExhaustive:
    def test_finds_optimal(self):
        weights = [1.0, 2.0, 4.0, 8.0]
        evalfn = make_evaluator(weights, threshold=10.0)
        result = minimize_exhaustive(4, evalfn)
        assert result.is_feasible
        assert result.final_count == 2  # 8 + 4 = 12
        assert result.final_placement[3] is True
        assert result.final_placement[2] is True

    def test_infeasible_returns_ng(self):
        weights = [1.0, 1.0]
        evalfn = make_evaluator(weights, threshold=100.0)
        result = minimize_exhaustive(2, evalfn)
        assert not result.is_feasible

    def test_too_large_raises(self):
        with pytest.raises(ValueError):
            minimize_exhaustive(20, lambda p: ({}, True, 0.0), max_positions=12)


class TestEntryPoint:
    def test_unknown_strategy(self):
        with pytest.raises(ValueError):
            minimize_damper_count(3, lambda p: ({}, True, 0.0), strategy="foo")

    def test_greedy_remove_via_entry(self):
        weights = [5.0, 5.0, 0.1]
        evalfn = make_evaluator(weights, threshold=9.0)
        result = minimize_damper_count(3, evalfn, strategy="greedy_remove")
        assert result.strategy == "greedy_remove"
        assert result.final_count == 2
