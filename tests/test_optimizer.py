"""
tests/test_optimizer.py
Unit tests for optimizer module — ParameterRange, mock evaluate, GP, EI,
and Bayesian search logic.

PySide6 の QThread / Signal を使う optimizer.py を import するには
PySide6 のランタイムライブラリが必要です。このテストでは PySide6 が
ロードできない環境でも動作するように、sys.modules をモックします。
"""

import pytest
import sys
import math
import random
from typing import Dict
from unittest.mock import MagicMock

import numpy as np

# ---------------------------------------------------------------------------
# PySide6 mock — 共有ライブラリが無い環境でもインポートを通す
# ---------------------------------------------------------------------------
_HAS_QT = False
try:
    from PySide6.QtCore import QObject  # noqa: F401
    _HAS_QT = True
except (ImportError, OSError):
    # PySide6 が利用不能 → 軽量モックを注入
    _mock_qtcore = MagicMock()

    class _FakeSignal:
        """Signal() の代替: emit / connect は何もしない。"""
        def __init__(self, *args, **kwargs):
            pass
        def emit(self, *a, **kw):
            pass
        def connect(self, *a, **kw):
            pass

    _mock_qtcore.Signal = _FakeSignal
    _mock_qtcore.QObject = type("QObject", (), {"__init__": lambda self, *a, **kw: None})
    _mock_qtcore.QThread = type("QThread", (), {
        "__init__": lambda self, *a, **kw: None,
        "start": lambda self: None,
        "isRunning": lambda self: False,
        "wait": lambda self, *a: None,
        "terminate": lambda self: None,
    })

    _mock_qtwidgets = MagicMock()

    sys.modules.setdefault("PySide6", MagicMock())
    sys.modules["PySide6.QtCore"] = _mock_qtcore
    sys.modules.setdefault("PySide6.QtWidgets", _mock_qtwidgets)
    sys.modules.setdefault("PySide6.QtGui", MagicMock())

# Now import the optimizer classes (always succeeds)
from app.services.optimizer import (
    ParameterRange,
    OptimizationConfig,
    OptimizationCandidate,
    OptimizationResult,
    _mock_evaluate,
    _GaussianProcessRegressor,
    _expected_improvement_no_scipy,
    _OptimizationWorker,
)

needs_qt = pytest.mark.skipif(not _HAS_QT, reason="PySide6 runtime not available")


# No qapp fixture needed — all Worker methods are called synchronously.


# ===================================================================
# ParameterRange
# ===================================================================


class TestParameterRangeDiscreteValues:
    def test_with_step(self):
        pr = ParameterRange(key="Cd", min_val=100, max_val=500, step=100)
        vals = pr.discrete_values()
        assert vals == [100, 200, 300, 400, 500]

    def test_continuous(self):
        pr = ParameterRange(key="a", min_val=0, max_val=1, step=0)
        vals = pr.discrete_values()
        assert len(vals) == 20
        assert vals[0] == pytest.approx(0.0)
        assert vals[-1] == pytest.approx(1.0)

    def test_integer_flag(self):
        pr = ParameterRange(key="n", min_val=1, max_val=10, step=1, is_integer=True)
        vals = pr.discrete_values()
        assert all(isinstance(v, int) for v in vals)
        assert vals == list(range(1, 11))

    def test_max_points_limit(self):
        pr = ParameterRange(key="x", min_val=0, max_val=1000, step=1)
        vals = pr.discrete_values(max_points=5)
        assert len(vals) <= 5


class TestParameterRangeRandomValue:
    def test_in_range(self):
        pr = ParameterRange(key="x", min_val=10, max_val=20)
        for _ in range(100):
            v = pr.random_value()
            assert 10 <= v <= 20

    def test_integer(self):
        pr = ParameterRange(key="n", min_val=1, max_val=5, is_integer=True)
        for _ in range(50):
            v = pr.random_value()
            assert v == round(v)

    def test_snap_to_step(self):
        pr = ParameterRange(key="x", min_val=0, max_val=1, step=0.25)
        for _ in range(50):
            v = pr.random_value()
            assert v % 0.25 == pytest.approx(0.0, abs=0.001)


# ===================================================================
# Mock evaluate
# ===================================================================


class TestMockEvaluate:
    def test_returns_all_keys(self):
        result = _mock_evaluate({"Cd": 300, "alpha": 0.4}, {}, "max_drift")
        expected_keys = {
            "max_drift", "max_acc", "max_disp", "max_vel",
            "shear_coeff", "max_otm", "max_story_disp", "peak_gain_db",
        }
        assert expected_keys == set(result.keys())

    def test_all_positive(self):
        result = _mock_evaluate({"Cd": 500}, {}, "max_drift")
        for k, v in result.items():
            assert v > 0, f"{k} should be positive"

    def test_cd_effect_on_average(self):
        """Higher Cd reduces drift on average."""
        base = {"max_drift": 0.01}
        low = [_mock_evaluate({"Cd": 50}, base, "max_drift")["max_drift"] for _ in range(80)]
        high = [_mock_evaluate({"Cd": 2000}, base, "max_drift")["max_drift"] for _ in range(80)]
        assert np.mean(low) > np.mean(high)

    def test_peak_gain_db_tmd_effect(self):
        """Higher mass ratio and damping ratio reduce peak_gain_db."""
        base = {"peak_gain_db": 20.0}
        low_mu = [
            _mock_evaluate({"mu": 0.01, "zeta_d": 0.05, "Cd": 300}, base, "peak_gain_db")["peak_gain_db"]
            for _ in range(80)
        ]
        high_mu = [
            _mock_evaluate({"mu": 0.10, "zeta_d": 0.20, "Cd": 300}, base, "peak_gain_db")["peak_gain_db"]
            for _ in range(80)
        ]
        assert np.mean(low_mu) > np.mean(high_mu)


# ===================================================================
# Optimization data classes
# ===================================================================


class TestOptimizationResult:
    def test_feasible_candidates(self):
        r = OptimizationResult(all_candidates=[
            OptimizationCandidate(is_feasible=True, objective_value=1),
            OptimizationCandidate(is_feasible=False, objective_value=2),
            OptimizationCandidate(is_feasible=True, objective_value=3),
        ])
        assert len(r.feasible_candidates) == 2

    def test_ranked_candidates(self):
        r = OptimizationResult(all_candidates=[
            OptimizationCandidate(is_feasible=True, objective_value=0.03),
            OptimizationCandidate(is_feasible=True, objective_value=0.01),
            OptimizationCandidate(is_feasible=True, objective_value=0.02),
        ])
        ranked = r.ranked_candidates
        assert [c.objective_value for c in ranked] == [0.01, 0.02, 0.03]

    def test_summary_text_with_best(self):
        r = OptimizationResult(
            config=OptimizationConfig(objective_label="テスト", method="grid"),
            best=OptimizationCandidate(params={"Cd": 500}, objective_value=0.005,
                                       response_values={"max_drift": 0.005}),
            all_candidates=[],
            elapsed_sec=1.5,
            message="OK",
        )
        text = r.get_summary_text()
        assert "テスト" in text
        assert "0.005" in text

    def test_summary_text_no_best(self):
        r = OptimizationResult(config=OptimizationConfig(), message="OK")
        assert "見つかりませんでした" in r.get_summary_text()


# ===================================================================
# Gaussian Process Regressor
# ===================================================================


class TestGaussianProcess:
    def test_fit_predict_linear(self):
        gp = _GaussianProcessRegressor(length_scale=1.0, noise=1e-6)
        X = np.array([[0.0], [0.5], [1.0]])
        y = np.array([1.0, 0.5, 0.0])
        gp.fit(X, y)
        mu, sigma = gp.predict(np.array([[0.25], [0.75]]))
        assert mu[0] == pytest.approx(0.75, abs=0.25)
        assert mu[1] == pytest.approx(0.25, abs=0.25)

    def test_low_uncertainty_at_data(self):
        gp = _GaussianProcessRegressor(noise=1e-6)
        X = np.array([[0.0], [0.5], [1.0]])
        y = np.array([0.0, 0.5, 1.0])
        gp.fit(X, y)
        _, sigma = gp.predict(X)
        assert all(s < 0.1 for s in sigma)

    def test_higher_uncertainty_far_away(self):
        gp = _GaussianProcessRegressor(length_scale=0.3, noise=1e-6)
        X = np.array([[0.0], [1.0]])
        y = np.array([0.0, 1.0])
        gp.fit(X, y)
        _, s_near = gp.predict(np.array([[0.5]]))
        _, s_far = gp.predict(np.array([[10.0]]))
        assert s_far[0] > s_near[0]

    def test_predict_without_fit(self):
        gp = _GaussianProcessRegressor()
        mu, sigma = gp.predict(np.array([[0.5]]))
        assert mu[0] == 0.0
        assert sigma[0] == 1.0

    def test_multidimensional(self):
        gp = _GaussianProcessRegressor(noise=1e-6)
        X = np.array([[0, 0], [1, 0], [0, 1], [1, 1]], dtype=float)
        y = np.array([0, 1, 1, 2], dtype=float)
        gp.fit(X, y)
        mu, _ = gp.predict(np.array([[0.5, 0.5]]))
        assert mu[0] == pytest.approx(1.0, abs=0.4)


# ===================================================================
# Expected Improvement
# ===================================================================


class TestExpectedImprovement:
    def test_positive_for_improvement(self):
        ei = _expected_improvement_no_scipy(
            mu=np.array([0.3]), sigma=np.array([0.1]), y_best=0.5
        )
        assert ei[0] > 0

    def test_zero_for_zero_sigma(self):
        ei = _expected_improvement_no_scipy(
            mu=np.array([0.3]), sigma=np.array([0.0]), y_best=0.5
        )
        assert ei[0] == 0.0

    def test_prefers_lower_mu(self):
        ei = _expected_improvement_no_scipy(
            mu=np.array([0.1, 0.4]), sigma=np.array([0.1, 0.1]), y_best=0.5
        )
        assert ei[0] > ei[1]

    def test_prefers_higher_sigma(self):
        ei = _expected_improvement_no_scipy(
            mu=np.array([0.3, 0.3]), sigma=np.array([0.5, 0.01]), y_best=0.5
        )
        assert ei[0] > ei[1]


# ===================================================================
# Worker-level search tests (require Qt)
# ===================================================================


class TestBayesianSearch:
    """Test the Bayesian search method directly."""

    def test_finds_1d_minimum(self):
        def evaluate(params):
            return {"max_drift": (params["x"] - 0.5) ** 2}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="bayesian",
            max_iterations=35,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_bayesian_search(config)

        assert result.best is not None
        assert result.best.objective_value < 0.15
        assert abs(result.best.params["x"] - 0.5) < 0.4

    def test_finds_2d_minimum(self):
        def evaluate(params):
            return {"max_drift": (params["x"] - 0.3) ** 2 + (params["y"] - 0.7) ** 2}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key="x", min_val=0, max_val=1, step=0),
                ParameterRange(key="y", min_val=0, max_val=1, step=0),
            ],
            method="bayesian",
            max_iterations=40,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_bayesian_search(config)

        assert result.best is not None
        assert result.best.objective_value < 0.2

    def test_empty_params_returns_message(self):
        config = OptimizationConfig(
            objective_key="max_drift", parameters=[], method="bayesian",
        )
        worker = _OptimizationWorker(config)
        result = worker._run_bayesian_search(config)
        assert "設定されていません" in result.message

    def test_message_includes_bayesian_label(self):
        def evaluate(params):
            return {"max_drift": params["x"] ** 2}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="bayesian",
            max_iterations=15,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_bayesian_search(config)
        assert "ベイズ" in result.message


class TestGridSearch:
    def test_evaluates_all_combos(self):
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="Cd", min_val=100, max_val=300, step=100)],
        )
        worker = _OptimizationWorker(config)
        result = worker._run_grid_search(config)
        assert len(result.all_candidates) == 3

    def test_finds_best(self):
        def evaluate(params):
            return {"max_drift": abs(params["Cd"] - 200)}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="Cd", min_val=100, max_val=300, step=100)],
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_grid_search(config)
        assert result.best.params["Cd"] == 200
        assert result.best.objective_value == 0.0


class TestRandomSearch:
    def test_respects_max_iterations(self):
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="random",
            max_iterations=20,
        )
        worker = _OptimizationWorker(config)
        result = worker._run_random_search(config)
        assert len(result.all_candidates) <= 20


class TestLatinHypercubeSampling:
    def test_shape(self):
        samples = _OptimizationWorker._latin_hypercube_sample(10, 3)
        assert samples.shape == (10, 3)

    def test_range(self):
        samples = _OptimizationWorker._latin_hypercube_sample(50, 4)
        assert np.all(samples >= 0) and np.all(samples <= 1)

    def test_coverage(self):
        """Each bin gets exactly one sample per dimension."""
        n = 20
        samples = _OptimizationWorker._latin_hypercube_sample(n, 2)
        for d in range(2):
            bins = np.floor(samples[:, d] * n).astype(int)
            bins = np.clip(bins, 0, n - 1)
            assert len(set(bins)) == n


# ===================================================================
# GA (遺伝的アルゴリズム)
# ===================================================================


class TestGASearch:
    def test_finds_minimum_of_quadratic(self):
        """GA should find the minimum of (x - 0.5)^2 near x=0.5."""
        def evaluate(params):
            return {"max_drift": (params["x"] - 0.5) ** 2}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="ga",
            max_iterations=200,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_ga_search(config)

        assert result.best is not None
        assert result.best.objective_value < 0.01
        assert abs(result.best.params["x"] - 0.5) < 0.15

    def test_empty_params_returns_message(self):
        config = OptimizationConfig(
            objective_key="max_drift", parameters=[], method="ga",
        )
        worker = _OptimizationWorker(config)
        result = worker._run_ga_search(config)
        assert "設定されていません" in result.message

    def test_message_includes_ga_label(self):
        def evaluate(params):
            return {"max_drift": params["x"] ** 2}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="ga",
            max_iterations=50,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_ga_search(config)
        assert "遺伝的アルゴリズム" in result.message

    def test_respects_constraints(self):
        """GA should only accept feasible candidates as best."""
        def evaluate(params):
            return {"max_drift": params["x"], "stress": params["x"] * 100}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="ga",
            max_iterations=100,
            constraints={"stress": 50},  # x must be <= 0.5
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_ga_search(config)
        if result.best is not None:
            assert result.best.is_feasible

    def test_multidimensional(self):
        """GA should handle multiple parameters."""
        def evaluate(params):
            return {"max_drift": (params["x"] - 0.3) ** 2 + (params["y"] - 0.7) ** 2}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key="x", min_val=0, max_val=1, step=0),
                ParameterRange(key="y", min_val=0, max_val=1, step=0),
            ],
            method="ga",
            max_iterations=200,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_ga_search(config)
        assert result.best is not None
        assert result.best.objective_value < 0.05


# ===================================================================
# SA (焼きなまし法)
# ===================================================================


class TestSASearch:
    def test_finds_minimum_of_quadratic(self):
        """SA should find the minimum of (x - 0.5)^2 near x=0.5."""
        def evaluate(params):
            return {"max_drift": (params["x"] - 0.5) ** 2}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="sa",
            max_iterations=200,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_sa_search(config)

        assert result.best is not None
        assert result.best.objective_value < 0.01

    def test_empty_params_returns_message(self):
        config = OptimizationConfig(
            objective_key="max_drift", parameters=[], method="sa",
        )
        worker = _OptimizationWorker(config)
        result = worker._run_sa_search(config)
        assert "設定されていません" in result.message

    def test_message_includes_sa_label(self):
        def evaluate(params):
            return {"max_drift": params["x"] ** 2}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="sa",
            max_iterations=50,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_sa_search(config)
        assert "焼きなまし法" in result.message

    def test_acceptance_ratio_in_message(self):
        def evaluate(params):
            return {"max_drift": params["x"]}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="sa",
            max_iterations=50,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_sa_search(config)
        assert "受容率" in result.message

    def test_multidimensional(self):
        def evaluate(params):
            return {"max_drift": (params["x"] - 0.3) ** 2 + (params["y"] - 0.7) ** 2}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key="x", min_val=0, max_val=1, step=0),
                ParameterRange(key="y", min_val=0, max_val=1, step=0),
            ],
            method="sa",
            max_iterations=300,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_sa_search(config)
        assert result.best is not None
        assert result.best.objective_value < 0.05


class TestMethodDispatch:
    """Verify that the run() dispatcher routes to GA and SA correctly."""

    def test_ga_dispatch(self):
        def evaluate(params):
            return {"max_drift": params["x"]}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="ga",
            max_iterations=30,
        )
        worker = _OptimizationWorker(config, evaluate)
        worker.run()
        # run() emits finished_signal — we just check no exception was raised

    def test_sa_dispatch(self):
        def evaluate(params):
            return {"max_drift": params["x"]}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="sa",
            max_iterations=30,
        )
        worker = _OptimizationWorker(config, evaluate)
        worker.run()


# ---------------------------------------------------------------------------
# 感度解析テスト
# ---------------------------------------------------------------------------

class TestComputeSensitivity:
    """compute_sensitivity のユニットテスト。"""

    def _simple_evaluate(self, params):
        """Cd に強く依存、alpha にあまり依存しないモック評価関数。"""
        cd = params.get("Cd", 300)
        alpha = params.get("alpha", 0.4)
        return {
            "max_drift": 0.005 / (1 + 0.001 * cd) * (1 + 0.1 * alpha),
            "max_acc": 3.0 * (1 - 0.0005 * cd),
        }

    def test_basic(self):
        from app.services.optimizer import compute_sensitivity, SensitivityResult
        params = [
            ParameterRange(key="Cd", label="減衰係数", min_val=100, max_val=1000, step=0),
            ParameterRange(key="alpha", label="速度指数", min_val=0.1, max_val=1.0, step=0),
        ]
        best = {"Cd": 500.0, "alpha": 0.4}
        result = compute_sensitivity(
            self._simple_evaluate, best, params, "max_drift",
        )
        assert isinstance(result, SensitivityResult)
        assert len(result.entries) == 2
        assert result.base_objective > 0
        # Cd should be more sensitive than alpha
        ranked = result.ranked_entries
        assert ranked[0].key == "Cd"
        assert ranked[0].sensitivity_index > ranked[1].sensitivity_index

    def test_empty_params(self):
        from app.services.optimizer import compute_sensitivity
        result = compute_sensitivity(
            lambda p: {"obj": 1.0}, {}, [], "obj",
        )
        assert len(result.entries) == 0

    def test_custom_variations(self):
        from app.services.optimizer import compute_sensitivity
        params = [
            ParameterRange(key="Cd", label="Cd", min_val=100, max_val=1000, step=0),
        ]
        best = {"Cd": 500.0}
        result = compute_sensitivity(
            self._simple_evaluate, best, params, "max_drift",
            variation_pcts=[-0.1, 0.0, 0.1],
        )
        entry = result.entries[0]
        assert len(entry.variations) == 3
        assert len(entry.objective_values) == 3

    def test_evaluate_failure_handled(self):
        """評価関数が例外を投げても感度解析が止まらないことを確認。"""
        from app.services.optimizer import compute_sensitivity
        call_count = [0]
        def flaky_eval(params):
            call_count[0] += 1
            if call_count[0] % 3 == 0:
                raise RuntimeError("SNAP crash")
            return {"obj": params.get("x", 1.0) ** 2}

        params = [ParameterRange(key="x", label="x", min_val=0.1, max_val=10, step=0)]
        result = compute_sensitivity(flaky_eval, {"x": 5.0}, params, "obj")
        assert len(result.entries) == 1
        assert len(result.entries[0].objective_values) > 0

    def test_integer_parameter(self):
        from app.services.optimizer import compute_sensitivity
        params = [
            ParameterRange(key="n", label="本数", min_val=1, max_val=10, step=1, is_integer=True),
        ]
        best = {"n": 5.0}
        result = compute_sensitivity(
            lambda p: {"obj": 10.0 / p["n"]}, best, params, "obj",
        )
        assert len(result.entries) == 1
        entry = result.entries[0]
        assert entry.sensitivity_index > 0


# ===========================================================================
# JSON serialization tests (F-3)
# ===========================================================================

class TestParameterRangeSerialization:
    """ParameterRange の to_dict / from_dict テスト。"""

    def test_round_trip(self):
        pr = ParameterRange(
            key="Cd", label="減衰係数", min_val=100, max_val=2000, step=100,
        )
        d = pr.to_dict()
        restored = ParameterRange.from_dict(d)
        assert restored.key == "Cd"
        assert restored.label == "減衰係数"
        assert restored.min_val == 100
        assert restored.max_val == 2000
        assert restored.step == 100
        assert restored.is_integer is False

    def test_integer_param(self):
        pr = ParameterRange(key="n", label="本数", min_val=1, max_val=10, step=1, is_integer=True)
        d = pr.to_dict()
        restored = ParameterRange.from_dict(d)
        assert restored.is_integer is True


class TestOptimizationConfigSerialization:
    """OptimizationConfig の to_dict / from_dict テスト。"""

    def test_round_trip(self):
        config = OptimizationConfig(
            objective_key="max_drift",
            objective_label="最大層間変形角",
            parameters=[
                ParameterRange(key="Cd", label="減衰係数", min_val=100, max_val=1000, step=100),
            ],
            constraints={"max_acc": 5.0},
            method="bayesian",
            max_iterations=50,
            damper_type="オイルダンパー",
            objective_weights={"max_drift": 0.7, "max_acc": 0.3},
        )
        d = config.to_dict()
        restored = OptimizationConfig.from_dict(d)
        assert restored.objective_key == "max_drift"
        assert restored.method == "bayesian"
        assert restored.max_iterations == 50
        assert len(restored.parameters) == 1
        assert restored.parameters[0].key == "Cd"
        assert restored.objective_weights == {"max_drift": 0.7, "max_acc": 0.3}
        assert restored.constraints == {"max_acc": 5.0}


class TestOptimizationCandidateSerialization:
    """OptimizationCandidate の to_dict / from_dict テスト。"""

    def test_round_trip(self):
        cand = OptimizationCandidate(
            params={"Cd": 500, "alpha": 0.4},
            objective_value=0.00321,
            response_values={"max_drift": 0.00321, "max_acc": 3.2},
            is_feasible=True,
            iteration=5,
        )
        d = cand.to_dict()
        restored = OptimizationCandidate.from_dict(d)
        assert restored.params == {"Cd": 500, "alpha": 0.4}
        assert abs(restored.objective_value - 0.00321) < 1e-10
        assert restored.response_values == {"max_drift": 0.00321, "max_acc": 3.2}
        assert restored.is_feasible is True
        assert restored.iteration == 5

    def test_infeasible(self):
        cand = OptimizationCandidate(is_feasible=False, objective_value=float("inf"))
        d = cand.to_dict()
        restored = OptimizationCandidate.from_dict(d)
        assert restored.is_feasible is False


class TestOptimizationResultSerialization:
    """OptimizationResult の to_dict / from_dict / save_json / load_json テスト。"""

    def _make_result(self):
        config = OptimizationConfig(
            objective_key="max_drift",
            objective_label="最大層間変形角",
            parameters=[
                ParameterRange(key="Cd", label="減衰係数", min_val=100, max_val=1000, step=100),
            ],
            method="grid",
            objective_weights={"max_drift": 0.6, "max_acc": 0.4},
        )
        c1 = OptimizationCandidate(
            params={"Cd": 500}, objective_value=0.003,
            response_values={"max_drift": 0.003, "max_acc": 2.5},
            is_feasible=True, iteration=1,
        )
        c2 = OptimizationCandidate(
            params={"Cd": 200}, objective_value=0.008,
            response_values={"max_drift": 0.008, "max_acc": 1.8},
            is_feasible=True, iteration=2,
        )
        c3 = OptimizationCandidate(
            params={"Cd": 100}, objective_value=0.015,
            response_values={"max_drift": 0.015, "max_acc": 6.0},
            is_feasible=False, iteration=3,
        )
        return OptimizationResult(
            best=c1,
            all_candidates=[c1, c2, c3],
            config=config,
            elapsed_sec=12.5,
            converged=True,
            message="完了",
        )

    def test_round_trip_dict(self):
        result = self._make_result()
        d = result.to_dict()
        restored = OptimizationResult.from_dict(d)
        assert len(restored.all_candidates) == 3
        assert restored.best is not None
        assert abs(restored.best.objective_value - 0.003) < 1e-10
        assert restored.config.method == "grid"
        assert restored.elapsed_sec == 12.5
        assert restored.converged is True
        assert restored.message == "完了"
        assert len(restored.feasible_candidates) == 2

    def test_save_load_json(self, tmp_path):
        result = self._make_result()
        path = str(tmp_path / "test_result.json")
        result.save_json(path)

        loaded = OptimizationResult.load_json(path)
        assert len(loaded.all_candidates) == 3
        assert loaded.best is not None
        assert loaded.config.objective_weights == {"max_drift": 0.6, "max_acc": 0.4}

    def test_empty_result_round_trip(self):
        result = OptimizationResult(message="空の結果")
        d = result.to_dict()
        restored = OptimizationResult.from_dict(d)
        assert restored.best is None
        assert len(restored.all_candidates) == 0
        assert restored.message == "空の結果"

    def test_evaluation_method_default_mock(self):
        """evaluation_method のデフォルトは 'mock'。"""
        result = OptimizationResult()
        assert result.evaluation_method == "mock"

    def test_evaluation_method_snap_round_trip(self):
        """evaluation_method='snap' が to_dict/from_dict で保持される。"""
        result = self._make_result()
        result.evaluation_method = "snap"
        d = result.to_dict()
        assert d["evaluation_method"] == "snap"
        restored = OptimizationResult.from_dict(d)
        assert restored.evaluation_method == "snap"

    def test_evaluation_method_in_summary(self):
        """get_summary_text() に評価方式が表示される。"""
        result = self._make_result()
        result.evaluation_method = "mock"
        assert "モック評価" in result.get_summary_text()
        result.evaluation_method = "snap"
        assert "SNAP実解析" in result.get_summary_text()

    def test_evaluation_method_missing_in_dict(self):
        """旧JSONに evaluation_method がない場合、'mock' にフォールバック。"""
        d = {"message": "旧形式", "all_candidates": []}
        restored = OptimizationResult.from_dict(d)
        assert restored.evaluation_method == "mock"

    def test_evaluation_method_save_load_json(self, tmp_path):
        """evaluation_method が JSON保存/読込で保持される。"""
        result = self._make_result()
        result.evaluation_method = "snap"
        path = str(tmp_path / "test_eval_method.json")
        result.save_json(path)
        loaded = OptimizationResult.load_json(path)
        assert loaded.evaluation_method == "snap"


# ===========================================================================
# Pareto front extraction test (F-2)
# ===========================================================================

class TestParetoFrontExtraction:
    """ParetoDialog._extract_pareto_front のテスト。"""

    def test_simple_pareto(self):
        """3点中、(1,3)と(3,1)がPareto front、(2,2)は支配される"""
        # (1,3) is not dominated by any
        # (3,1) is not dominated by any
        # (2,2) is dominated by neither individually, but let's check...
        # Actually (2,2) is NOT dominated by (1,3) or (3,1) since neither
        # has both coords <=. So all 3 are on the front.
        # Let's use a clearer case:
        xs = [1.0, 2.0, 3.0, 1.5]
        ys = [4.0, 2.0, 3.0, 1.0]
        # (1, 4): not dominated
        # (2, 2): dominated by (1.5, 1)? 1.5<=2 and 1<=2 yes, so dominated
        # (3, 3): dominated by (2,2)
        # (1.5, 1): not dominated
        from app.ui.optimizer_dialog import ParetoDialog
        px, py = ParetoDialog._extract_pareto_front(xs, ys)
        assert len(px) == 2
        # Should contain (1,4) and (1.5,1)
        points = set(zip([round(x, 1) for x in px], [round(y, 1) for y in py]))
        assert (1.0, 4.0) in points
        assert (1.5, 1.0) in points

    def test_empty_input(self):
        from app.ui.optimizer_dialog import ParetoDialog
        px, py = ParetoDialog._extract_pareto_front([], [])
        assert px == []
        assert py == []

    def test_single_point(self):
        from app.ui.optimizer_dialog import ParetoDialog
        px, py = ParetoDialog._extract_pareto_front([1.0], [2.0])
        assert len(px) == 1
        assert px[0] == 1.0
        assert py[0] == 2.0


# ---------------------------------------------------------------------------
# Phase I: constraint_margins + CandidateDetailDialog tests
# ---------------------------------------------------------------------------

class TestConstraintMargins:
    """_check_constraints が制約マージンを正しく返すことを検証。"""

    def test_margins_feasible(self):
        """全制約を満たす場合、マージンは全て正。"""
        def evaluate(params):
            return {"max_drift": 0.003, "max_acc": 2.0}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0.1)],
            method="grid",
            constraints={"max_drift": 0.005, "max_acc": 5.0},
        )
        worker = _OptimizationWorker(config, evaluate)
        is_feasible, margins = worker._check_constraints(
            {"max_drift": 0.003, "max_acc": 2.0}, config
        )
        assert is_feasible is True
        assert margins["max_drift"] == pytest.approx(0.002)
        assert margins["max_acc"] == pytest.approx(3.0)

    def test_margins_infeasible(self):
        """制約を超過する場合、対応マージンが負。"""
        def evaluate(params):
            return {"max_drift": 0.008}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0.1)],
            method="grid",
            constraints={"max_drift": 0.005},
        )
        worker = _OptimizationWorker(config, evaluate)
        is_feasible, margins = worker._check_constraints(
            {"max_drift": 0.008}, config
        )
        assert is_feasible is False
        assert margins["max_drift"] == pytest.approx(-0.003)

    def test_candidate_carries_margins(self):
        """OptimizationCandidate にマージンが保存され、JSON往復で保持される。"""
        cand = OptimizationCandidate(
            params={"x": 0.5},
            objective_value=0.003,
            response_values={"max_drift": 0.003},
            is_feasible=True,
            iteration=0,
            constraint_margins={"max_drift": 0.002},
        )
        d = cand.to_dict()
        assert d["constraint_margins"] == {"max_drift": 0.002}
        restored = OptimizationCandidate.from_dict(d)
        assert restored.constraint_margins == {"max_drift": 0.002}

    def test_grid_search_populates_margins(self):
        """グリッドサーチの結果にマージンが含まれること。"""
        call_count = 0
        def evaluate(params):
            nonlocal call_count
            call_count += 1
            return {"max_drift": params["x"] * 0.01}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0.1, max_val=0.3, step=0.1)],
            method="grid",
            constraints={"max_drift": 0.005},
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_grid_search(config)
        for cand in result.all_candidates:
            assert "max_drift" in cand.constraint_margins


# ---------------------------------------------------------------------------
# Phase K: all_ranked_candidates + evaluator_stats tests
# ---------------------------------------------------------------------------

class TestAllRankedCandidates:
    """all_ranked_candidates が制約満足優先でソートされることを検証。"""

    def test_feasible_first_then_infeasible(self):
        """制約満足候補が先、制約違反候補が後に並ぶ。"""
        r = OptimizationResult(all_candidates=[
            OptimizationCandidate(is_feasible=False, objective_value=0.01),
            OptimizationCandidate(is_feasible=True, objective_value=0.03),
            OptimizationCandidate(is_feasible=False, objective_value=0.02),
            OptimizationCandidate(is_feasible=True, objective_value=0.01),
        ])
        ranked = r.all_ranked_candidates
        assert len(ranked) == 4
        # 先頭2つは feasible (0.01, 0.03)
        assert ranked[0].is_feasible is True
        assert ranked[0].objective_value == 0.01
        assert ranked[1].is_feasible is True
        assert ranked[1].objective_value == 0.03
        # 後半2つは infeasible (0.01, 0.02)
        assert ranked[2].is_feasible is False
        assert ranked[2].objective_value == 0.01
        assert ranked[3].is_feasible is False
        assert ranked[3].objective_value == 0.02

    def test_all_feasible(self):
        """全候補が制約満足の場合、目的関数値順。"""
        r = OptimizationResult(all_candidates=[
            OptimizationCandidate(is_feasible=True, objective_value=0.03),
            OptimizationCandidate(is_feasible=True, objective_value=0.01),
        ])
        ranked = r.all_ranked_candidates
        assert [c.objective_value for c in ranked] == [0.01, 0.03]

    def test_all_infeasible(self):
        """全候補が制約違反の場合、目的関数値順。"""
        r = OptimizationResult(all_candidates=[
            OptimizationCandidate(is_feasible=False, objective_value=0.05),
            OptimizationCandidate(is_feasible=False, objective_value=0.02),
        ])
        ranked = r.all_ranked_candidates
        assert [c.objective_value for c in ranked] == [0.02, 0.05]

    def test_empty(self):
        r = OptimizationResult(all_candidates=[])
        assert r.all_ranked_candidates == []


class TestEvaluatorStats:
    """evaluator_stats がサマリーテキスト・JSON保存に含まれることを検証。"""

    def test_summary_includes_stats(self):
        r = OptimizationResult(
            config=OptimizationConfig(objective_label="テスト", method="grid"),
            best=OptimizationCandidate(
                params={"Cd": 500}, objective_value=0.005,
                response_values={"max_drift": 0.005},
            ),
            all_candidates=[],
            evaluation_method="snap",
            evaluator_stats={"total": 20, "success": 15, "error": 1, "cache_hits": 4},
        )
        text = r.get_summary_text()
        assert "SNAP統計" in text
        assert "成功 15" in text
        assert "キャッシュヒット 4" in text

    def test_summary_without_stats(self):
        r = OptimizationResult(
            config=OptimizationConfig(objective_label="テスト", method="grid"),
            best=OptimizationCandidate(
                params={"Cd": 500}, objective_value=0.005,
                response_values={"max_drift": 0.005},
            ),
            all_candidates=[],
        )
        text = r.get_summary_text()
        assert "SNAP統計" not in text

    def test_stats_save_load_json(self, tmp_path):
        """evaluator_stats が JSON保存/読込で保持される。"""
        r = OptimizationResult(
            config=OptimizationConfig(objective_label="テスト", method="grid"),
            all_candidates=[],
            evaluator_stats={"total": 10, "success": 8, "error": 0, "cache_hits": 2},
        )
        path = str(tmp_path / "test_stats.json")
        r.save_json(path)
        loaded = OptimizationResult.load_json(path)
        assert loaded.evaluator_stats == {"total": 10, "success": 8, "error": 0, "cache_hits": 2}

    def test_stats_none_json(self, tmp_path):
        """evaluator_stats が None の場合も JSON保存/読込で維持される。"""
        r = OptimizationResult(
            config=OptimizationConfig(objective_label="テスト", method="grid"),
            all_candidates=[],
        )
        path = str(tmp_path / "test_stats_none.json")
        r.save_json(path)
        loaded = OptimizationResult.load_json(path)
        assert loaded.evaluator_stats is None


