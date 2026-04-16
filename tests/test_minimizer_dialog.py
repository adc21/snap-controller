"""
tests/test_minimizer_dialog.py
ダンパー本数最小化ダイアログのテスト。

PySide6 なし環境でも実行できるよう conftest.py のモックを活用する。
ダイアログ本体は UI 依存のため import テストと純粋ロジックテストに分割する。
"""

from __future__ import annotations

import sys
from typing import Dict, List, Tuple
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. サービス層テスト（UI 非依存）
# ---------------------------------------------------------------------------

class TestMinimizerServiceDirect:
    """damper_count_minimizer サービスの動作確認（UI なし）。"""

    def _make_simple_evaluate_fn(self, threshold: float = 0.5):
        """本数が threshold 以上ならば feasible になるモック評価関数。"""
        def _fn(placement: List[bool]) -> Tuple[Dict[str, float], bool, float]:
            n = len(placement)
            ratio = sum(placement) / max(n, 1)
            value = 1.0 - ratio
            margin = threshold - value
            is_feasible = value <= threshold
            return {"mock_value": value}, is_feasible, margin
        return _fn

    def test_greedy_remove_reduces_count(self):
        from app.services.damper_count_minimizer import minimize_greedy_remove
        evaluate_fn = self._make_simple_evaluate_fn(threshold=0.5)
        result = minimize_greedy_remove(
            num_positions=5,
            evaluate_fn=evaluate_fn,
        )
        assert result.strategy == "greedy_remove"
        assert result.final_count <= 5
        assert result.is_feasible

    def test_greedy_add_starts_from_empty(self):
        from app.services.damper_count_minimizer import minimize_greedy_add
        evaluate_fn = self._make_simple_evaluate_fn(threshold=0.5)
        result = minimize_greedy_add(
            num_positions=6,
            evaluate_fn=evaluate_fn,
        )
        assert result.strategy == "greedy_add"
        assert result.is_feasible
        # 貪欲追加では必要最小限の本数に収束するはず
        assert result.final_count <= 6

    def test_exhaustive_finds_minimum(self):
        from app.services.damper_count_minimizer import minimize_exhaustive

        # 3箇所のうち2本あれば feasible
        def _fn(placement: List[bool]) -> Tuple[Dict, bool, float]:
            n = sum(placement)
            feasible = n >= 2
            margin = (n - 2) / 3.0
            return {"n": n}, feasible, margin

        result = minimize_exhaustive(num_positions=3, evaluate_fn=_fn)
        assert result.final_count == 2
        assert result.is_feasible

    def test_required_positions_respected(self):
        from app.services.damper_count_minimizer import minimize_greedy_remove

        removed_positions = []

        def _fn(placement: List[bool]) -> Tuple[Dict, bool, float]:
            removed_positions.append(list(placement))
            n = sum(placement)
            margin = (n - 1) / 5.0
            return {"n": n}, n >= 1, margin

        result = minimize_greedy_remove(
            num_positions=5,
            evaluate_fn=_fn,
            required_positions=[0, 1],  # 位置 0, 1 は必須
        )
        # 最終配置で位置 0, 1 は True
        assert result.final_placement[0] is True
        assert result.final_placement[1] is True

    def test_infeasible_initial_returns_immediately(self):
        from app.services.damper_count_minimizer import minimize_greedy_remove

        # 何をしても不可能
        def _fn(placement):
            return {}, False, -1.0

        result = minimize_greedy_remove(num_positions=4, evaluate_fn=_fn)
        assert not result.is_feasible
        assert "満載配置でも" in result.note

    def test_history_is_populated(self):
        from app.services.damper_count_minimizer import minimize_greedy_remove

        def _fn(placement):
            n = sum(placement)
            m = (n - 2) / 5.0
            return {"n": n}, n >= 2, m

        result = minimize_greedy_remove(num_positions=5, evaluate_fn=_fn)
        assert len(result.history) > 0
        # 最初のステップは "init"
        assert result.history[0].action == "init"
        # 最後は "final"
        assert result.history[-1].action == "final"

    def test_minimize_damper_count_entry_point(self):
        from app.services.damper_count_minimizer import minimize_damper_count

        def _fn(placement):
            n = sum(placement)
            return {}, n >= 2, (n - 2) / 5.0

        for strategy in ["greedy_remove", "greedy_add"]:
            result = minimize_damper_count(
                num_positions=5,
                evaluate_fn=_fn,
                strategy=strategy,
            )
            assert result.strategy == strategy
            assert result.final_count >= 0

    def test_minimize_damper_count_unknown_strategy(self):
        from app.services.damper_count_minimizer import minimize_damper_count

        with pytest.raises(ValueError, match="unknown strategy"):
            minimize_damper_count(
                num_positions=3,
                evaluate_fn=lambda p: ({}, True, 1.0),
                strategy="invalid_strategy",
            )

    def test_exhaustive_too_many_positions(self):
        from app.services.damper_count_minimizer import minimize_exhaustive

        with pytest.raises(ValueError, match="limited to"):
            minimize_exhaustive(
                num_positions=13,
                evaluate_fn=lambda p: ({}, True, 1.0),
            )

    def test_minimization_result_summary_text(self):
        from app.services.damper_count_minimizer import MinimizationResult

        result = MinimizationResult(
            strategy="greedy_remove",
            initial_placement=[True, True, True],
            final_placement=[True, False, True],
            final_count=2,
            is_feasible=True,
            final_margin=0.15,
            evaluations=7,
        )
        text = result.summary_text()
        assert "greedy_remove" in text
        assert "2" in text
        assert "OK" in text


# ---------------------------------------------------------------------------
# 2. ダイアログ import テスト（PySide6 モック環境）
# ---------------------------------------------------------------------------

class TestMinimizerDialogImport:
    """MinimizerDialog が正常に import できることを確認。"""

    def test_import(self):
        # conftest.py のモックで PySide6 が利用可能なはず
        try:
            from app.ui.minimizer_dialog import MinimizerDialog
        except ImportError as e:
            pytest.skip(f"PySide6 not available: {e}")

    def test_module_exports(self):
        try:
            import app.ui.minimizer_dialog as mod
            assert hasattr(mod, "MinimizerDialog")
        except ImportError:
            pytest.skip("PySide6 not available")


# ---------------------------------------------------------------------------
# 3. _MinimizerWorker のロジックテスト（QThread モック）
# ---------------------------------------------------------------------------

class TestMinimizerWorkerLogic:
    """ワーカースレッドのロジックを同期的に検証。"""

    def test_worker_calls_evaluate_fn(self):
        """ワーカーが evaluate_fn を正しく呼び出すことを確認。"""
        called = []

        def _fn(placement):
            called.append(list(placement))
            n = sum(placement)
            return {"n": n}, n >= 2, (n - 2) / 3.0

        from app.services.damper_count_minimizer import minimize_damper_count
        result = minimize_damper_count(
            num_positions=4,
            evaluate_fn=_fn,
            strategy="greedy_remove",
        )
        assert len(called) > 0
        # 全ての呼び出しは長さ4のリスト
        for placement in called:
            assert len(placement) == 4

    def test_progress_callback_called(self):
        """進捗コールバックが各ステップで呼ばれることを確認。"""
        steps = []

        def _fn(placement):
            n = sum(placement)
            return {}, n >= 2, (n - 2) / 4.0

        from app.services.damper_count_minimizer import minimize_greedy_remove
        result = minimize_greedy_remove(
            num_positions=5,
            evaluate_fn=_fn,
            progress_cb=lambda step: steps.append(step),
        )
        assert len(steps) > 0
        # 最初の step は init
        assert steps[0].action == "init"
