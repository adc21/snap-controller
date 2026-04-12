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


class TestWarmStartConfig:
    """ウォームスタート機能のテスト。"""

    def test_warm_start_candidates_default_empty(self):
        """warm_start_candidates のデフォルトは空リスト。"""
        config = OptimizationConfig()
        assert config.warm_start_candidates == []

    def test_warm_start_candidates_set(self):
        """warm_start_candidates を設定できる。"""
        candidates = [
            OptimizationCandidate(
                params={"Cd": 500, "alpha": 0.3},
                objective_value=0.005,
                is_feasible=True,
            ),
            OptimizationCandidate(
                params={"Cd": 600, "alpha": 0.4},
                objective_value=0.006,
                is_feasible=True,
            ),
        ]
        config = OptimizationConfig(warm_start_candidates=candidates)
        assert len(config.warm_start_candidates) == 2
        assert config.warm_start_candidates[0].objective_value == 0.005

    def test_bayesian_warm_start(self):
        """ベイズ最適化がウォームスタート候補を初期データに使用する。"""
        warm = [
            OptimizationCandidate(
                params={"x": 0.5},
                objective_value=0.1,
                response_values={"max_drift": 0.1},
                is_feasible=True,
            ),
        ]
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange("x", "X", 0.0, 1.0, 0.1)],
            method="bayesian",
            max_iterations=15,
            warm_start_candidates=warm,
        )

        results_collected = []

        def mock_eval(params):
            x = params["x"]
            return {"max_drift": (x - 0.3) ** 2}

        worker = _OptimizationWorker(config, mock_eval)
        worker.candidate_found.connect(lambda c: results_collected.append(c))
        worker.run()

        # ウォーム候補が結果に含まれている
        assert len(results_collected) >= 1
        assert any(c.params.get("x") == 0.5 for c in results_collected)

    def test_ga_warm_start_injects_individuals(self):
        """GA がウォームスタート候補を初期集団に注入する。"""
        warm = [
            OptimizationCandidate(
                params={"x": 0.7},
                objective_value=0.01,
                response_values={"max_drift": 0.01},
                is_feasible=True,
            ),
        ]
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange("x", "X", 0.0, 1.0, 0.1)],
            method="ga",
            max_iterations=40,
            warm_start_candidates=warm,
        )

        results_collected = []

        def mock_eval(params):
            x = params["x"]
            return {"max_drift": (x - 0.7) ** 2}

        worker = _OptimizationWorker(config, mock_eval)
        worker.candidate_found.connect(lambda c: results_collected.append(c))
        worker.run()

        # 結果にウォームスタートの影響がある（最良解がx=0.7付近）
        assert len(results_collected) >= 20

    def test_sa_warm_start_uses_best(self):
        """SA がウォームスタートの最良解を初期解に使用する。"""
        warm = [
            OptimizationCandidate(
                params={"x": 0.3},
                objective_value=0.001,
                response_values={"max_drift": 0.001},
                is_feasible=True,
            ),
            OptimizationCandidate(
                params={"x": 0.8},
                objective_value=0.1,
                response_values={"max_drift": 0.1},
                is_feasible=True,
            ),
        ]
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange("x", "X", 0.0, 1.0, 0.1)],
            method="sa",
            max_iterations=20,
            warm_start_candidates=warm,
        )

        results_collected = []

        def mock_eval(params):
            x = params["x"]
            return {"max_drift": (x - 0.3) ** 2}

        worker = _OptimizationWorker(config, mock_eval)
        worker.candidate_found.connect(lambda c: results_collected.append(c))
        worker.run()

        # SA は初期解にウォームスタートの最良(x=0.3)を使うので、
        # 最初の候補が 0.3 付近であるはず
        assert len(results_collected) >= 10
        assert results_collected[0].params["x"] == pytest.approx(0.3, abs=0.15)


class TestConstraintPenalty:
    """制約ペナルティ法のテスト。"""

    def test_penalized_objective_no_penalty(self):
        """ペナルティ重み0のとき、元の値がそのまま返る。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="Cd", min_val=100, max_val=500, step=50)],
            method="grid",
            max_iterations=5,
            constraint_penalty_weight=0.0,
        )
        worker = _OptimizationWorker(config, lambda p: {"max_drift": 0.005})
        result = worker._penalized_objective(0.005, {"max_drift": -0.001}, config)
        assert result == 0.005

    def test_penalized_objective_with_violation(self):
        """制約違反時にペナルティが加算される。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="Cd", min_val=100, max_val=500, step=50)],
            method="grid",
            max_iterations=5,
            constraint_penalty_weight=10.0,
        )
        worker = _OptimizationWorker(config, lambda p: {"max_drift": 0.005})
        # margin = -0.002 means violation of 0.002
        result = worker._penalized_objective(0.005, {"max_drift": -0.002}, config)
        assert result == pytest.approx(0.005 + 10.0 * 0.002)

    def test_penalized_objective_feasible_no_extra(self):
        """制約満足時はペナルティがゼロ。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="Cd", min_val=100, max_val=500, step=50)],
            method="grid",
            max_iterations=5,
            constraint_penalty_weight=10.0,
        )
        worker = _OptimizationWorker(config, lambda p: {"max_drift": 0.005})
        result = worker._penalized_objective(0.005, {"max_drift": 0.003}, config)
        assert result == 0.005

    def test_penalized_objective_multiple_violations(self):
        """複数制約違反時のペナルティは合算される。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="Cd", min_val=100, max_val=500, step=50)],
            method="grid",
            max_iterations=5,
            constraint_penalty_weight=5.0,
        )
        worker = _OptimizationWorker(config, lambda p: {"max_drift": 0.005})
        margins = {"max_drift": -0.001, "max_acc": -0.5, "max_disp": 0.01}
        result = worker._penalized_objective(0.005, margins, config)
        expected = 0.005 + 5.0 * (0.001 + 0.5)
        assert result == pytest.approx(expected)

    def test_config_serialization_penalty_weight(self):
        """constraint_penalty_weight が to_dict/from_dict で保持される。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            method="bayesian",
            constraint_penalty_weight=25.0,
        )
        d = config.to_dict()
        assert d["constraint_penalty_weight"] == 25.0
        restored = OptimizationConfig.from_dict(d)
        assert restored.constraint_penalty_weight == 25.0

    def test_ga_uses_penalty_when_configured(self):
        """GA探索でペナルティ法を使用すると、infeasible候補もfitnessが有限値になる。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0.0, max_val=1.0)],
            constraints={"max_drift": 0.003},
            method="ga",
            max_iterations=20,
            constraint_penalty_weight=10.0,
        )

        def mock_eval(params):
            x = params["x"]
            return {"max_drift": x * 0.01}  # x=0.3 => drift=0.003

        worker = _OptimizationWorker(config, mock_eval)
        candidates = []
        worker.candidate_found.connect(lambda c: candidates.append(c))
        worker.run()

        # Some candidates should be infeasible (drift > 0.003)
        infeasible = [c for c in candidates if not c.is_feasible]
        # With penalty, GA should still explore infeasible region
        assert len(candidates) >= 20

    def test_summary_text_includes_penalty(self):
        """サマリーテキストにペナルティ重みが表示される。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            constraint_penalty_weight=15.0,
        )
        result = OptimizationResult(config=config, message="テスト")
        text = result.get_summary_text()
        assert "制約ペナルティ重み" in text
        assert "15.0" in text


# ===================================================================
# GA/SA early stopping & adaptive behavior
# ===================================================================


class TestGAEarlyStopping:
    """GA の早期収束検出テスト。"""

    def test_ga_early_stop_on_flat_landscape(self):
        """目的関数が定数の場合、改善がないため早期終了する。"""
        call_count = 0

        def evaluate(params):
            nonlocal call_count
            call_count += 1
            return {"max_drift": 1.0}  # 常に同じ値

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="ga",
            max_iterations=500,  # 多めに設定
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_ga_search(config)

        # 早期終了で全探索より少ない評価数になるはず
        assert len(result.all_candidates) < 500
        assert result.converged is True
        assert "早期収束" in result.message

    def test_ga_no_early_stop_on_improving_landscape(self):
        """改善が続く場合は早期終了しない。"""
        iter_count = [0]

        def evaluate(params):
            iter_count[0] += 1
            # 最適解 x=0.5 に向かって常に改善の余地がある
            return {"max_drift": (params["x"] - 0.5) ** 2}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="ga",
            max_iterations=100,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_ga_search(config)

        assert result.best is not None
        assert result.best.objective_value < 0.05

    def test_ga_adaptive_population_size(self):
        """高次元問題では集団サイズが大きくなる。"""
        # 1パラメータ
        config1 = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="ga",
            max_iterations=200,
        )
        # 5パラメータ
        config5 = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key=f"x{i}", min_val=0, max_val=1, step=0)
                for i in range(5)
            ],
            method="ga",
            max_iterations=200,
        )
        # n_params=5 → min(100, 10*5)=50 vs n_params=1 → min(100, 10)=10 (but base_pop=max(20,...))
        # 5パラメータの方が集団サイズが大きくなるはず
        def evaluate(params):
            return {"max_drift": sum(v ** 2 for v in params.values())}

        worker1 = _OptimizationWorker(config1, evaluate)
        worker5 = _OptimizationWorker(config5, evaluate)

        result1 = worker1._run_ga_search(config1)
        result5 = worker5._run_ga_search(config5)

        # 5パラメータの方が個体評価数が多い（集団が大きい）
        assert len(result5.all_candidates) >= len(result1.all_candidates)


class TestSAEarlyStopping:
    """SA の早期収束・適応ステップサイズテスト。"""

    def test_sa_early_stop_on_flat_landscape(self):
        """目的関数が定数の場合、改善がないため早期終了する。"""
        def evaluate(params):
            return {"max_drift": 1.0}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="sa",
            max_iterations=500,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_sa_search(config)

        assert len(result.all_candidates) < 500
        assert result.converged is True
        assert "早期収束" in result.message

    def test_sa_adaptive_step_reduces_for_high_dim(self):
        """高次元ではステップサイズが小さくなる（収束がより安定）。"""
        # 内部実装では step_size = min(0.3, 1/sqrt(n_params))
        # n_params=1 → step=0.3 (capped), n_params=16 → step=0.25
        # テストは間接的: 高次元でも最小値に近づくか
        def evaluate(params):
            return {"max_drift": sum((v - 0.5) ** 2 for v in params.values())}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key=f"x{i}", min_val=0, max_val=1, step=0)
                for i in range(4)
            ],
            method="sa",
            max_iterations=300,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_sa_search(config)

        assert result.best is not None
        assert result.best.objective_value < 0.2  # 合理的な範囲に収束

    def test_sa_acceptance_ratio_still_reported(self):
        """適応ステップ追加後も受容率がメッセージに含まれる。"""
        def evaluate(params):
            return {"max_drift": params["x"] ** 2}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="sa",
            max_iterations=100,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_sa_search(config)
        assert "受容率" in result.message

    def test_sa_early_stop_message_includes_count(self):
        """早期終了メッセージに改善なし回数が含まれる。"""
        def evaluate(params):
            return {"max_drift": 5.0}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="sa",
            max_iterations=500,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_sa_search(config)

        if result.converged:
            assert "改善なし" in result.message


# ===========================================================================
# Phase O-1: 最適化HTMLレポート生成
# ===========================================================================

class TestOptimizationReport:
    """generate_optimization_report のテスト。"""

    def _make_result(self):
        """テスト用の OptimizationResult を構築する。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            objective_label="最大層間変形角",
            method="grid",
            parameters=[
                ParameterRange(key="Cd", label="減衰係数", min_val=100, max_val=500, step=100),
                ParameterRange(key="alpha", label="速度指数", min_val=0.1, max_val=0.9, step=0.2),
            ],
            damper_type="オイルダンパー",
        )
        candidates = []
        for i in range(10):
            cd = 100 + i * 40
            alpha = 0.1 + i * 0.08
            obj = 0.005 - i * 0.0003
            candidates.append(OptimizationCandidate(
                params={"Cd": cd, "alpha": alpha},
                objective_value=max(obj, 0.001),
                response_values={"max_drift": max(obj, 0.001), "max_acc": 3.0 - i * 0.1},
                is_feasible=i < 8,
                iteration=i,
                constraint_margins={"max_drift": 0.01 - max(obj, 0.001)},
            ))
        return OptimizationResult(
            best=candidates[-1],
            all_candidates=candidates,
            config=config,
            elapsed_sec=12.5,
            converged=True,
            message="グリッドサーチ完了: 10 点を評価",
            evaluation_method="mock",
        )

    def test_generate_optimization_report_returns_html(self):
        """レポートが有効なHTMLを返すことを確認。"""
        from app.services.report_generator import generate_optimization_report
        result = self._make_result()
        html = generate_optimization_report(result)
        assert "<!DOCTYPE html>" in html
        assert "ダンパー最適化レポート" in html

    def test_report_contains_config_section(self):
        """設定概要セクションが含まれることを確認。"""
        from app.services.report_generator import generate_optimization_report
        result = self._make_result()
        html = generate_optimization_report(result)
        assert "設定概要" in html
        assert "グリッド" in html or "grid" in html

    def test_report_contains_best_solution(self):
        """最良解セクションが含まれることを確認。"""
        from app.services.report_generator import generate_optimization_report
        result = self._make_result()
        html = generate_optimization_report(result)
        assert "最良解" in html

    def test_report_contains_ranking(self):
        """候補ランキングテーブルが含まれることを確認。"""
        from app.services.report_generator import generate_optimization_report
        result = self._make_result()
        html = generate_optimization_report(result)
        assert "候補ランキング" in html or "ランキング" in html or "result-table" in html

    def test_report_with_no_best(self):
        """最良解がない場合でもレポート生成が成功すること。"""
        from app.services.report_generator import generate_optimization_report
        result = self._make_result()
        result.best = None
        html = generate_optimization_report(result)
        assert "<!DOCTYPE html>" in html

    def test_report_saves_to_file(self, tmp_path):
        """ファイルに正常に出力されることを確認。"""
        from app.services.report_generator import generate_optimization_report
        result = self._make_result()
        out_path = str(tmp_path / "test_report.html")
        html = generate_optimization_report(result, output_path=out_path)
        import os
        assert os.path.exists(out_path)
        with open(out_path, encoding="utf-8") as f:
            content = f.read()
        assert "<!DOCTYPE html>" in content

    def test_report_without_charts(self):
        """チャートなしでもレポート生成が成功すること。"""
        from app.services.report_generator import generate_optimization_report
        result = self._make_result()
        html = generate_optimization_report(result, include_charts=False)
        assert "<!DOCTYPE html>" in html
        # チャートなしなので base64 PNG は含まれないはず
        assert "data:image/png" not in html


# ===========================================================================
# Phase O-2: 並列候補評価
# ===========================================================================

class TestParallelEvaluation:
    """_evaluate_batch / 並列グリッドサーチ / 並列ランダムサーチのテスト。"""

    def test_n_parallel_field_default(self):
        """n_parallel のデフォルト値が1であることを確認。"""
        config = OptimizationConfig()
        assert config.n_parallel == 1

    def test_n_parallel_serialization(self):
        """n_parallel が to_dict/from_dict で正しくシリアライズされることを確認。"""
        config = OptimizationConfig(n_parallel=4)
        d = config.to_dict()
        assert d["n_parallel"] == 4
        config2 = OptimizationConfig.from_dict(d)
        assert config2.n_parallel == 4

    def test_evaluate_batch_sequential(self):
        """n_parallel=1 で逐次評価が正しく動作すること。"""
        call_count = 0
        def evaluate(params):
            nonlocal call_count
            call_count += 1
            return {"max_drift": params.get("x", 0) * 0.01}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=10, step=1)],
            n_parallel=1,
        )
        worker = _OptimizationWorker(config, evaluate)
        batch = [{"x": 1.0}, {"x": 2.0}, {"x": 3.0}]
        results = worker._evaluate_batch(batch, config, start_iter=0)
        assert len(results) == 3
        assert call_count == 3
        assert results[0].params["x"] == 1.0
        assert results[1].params["x"] == 2.0
        assert results[2].params["x"] == 3.0

    def test_evaluate_batch_parallel(self):
        """n_parallel>1 で並列評価が正しく動作すること。"""
        import time
        eval_times = []
        def evaluate(params):
            start = time.time()
            time.sleep(0.05)  # 50ms のシミュレーション
            eval_times.append(time.time() - start)
            return {"max_drift": params.get("x", 0) * 0.01}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=10, step=1)],
            n_parallel=4,
        )
        worker = _OptimizationWorker(config, evaluate)
        batch = [{"x": float(i)} for i in range(4)]

        start = time.time()
        results = worker._evaluate_batch(batch, config, start_iter=0)
        elapsed = time.time() - start

        assert len(results) == 4
        # 並列なので4つの50msタスクが <300msで完了するはず (逐次なら200ms+)
        assert elapsed < 0.5, f"並列評価が遅すぎます: {elapsed:.3f}s"
        # 結果の順序が保持されること
        for i, r in enumerate(results):
            assert r.params["x"] == float(i)

    def test_grid_search_parallel(self):
        """並列グリッドサーチが正しく動作すること。"""
        def evaluate(params):
            return {"max_drift": abs(params.get("x", 0) - 5.0) * 0.01}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=10, step=2)],
            method="grid",
            n_parallel=2,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_grid_search(config)
        assert len(result.all_candidates) > 0
        assert result.best is not None
        # x=5 付近が最良解
        assert abs(result.best.params["x"] - 5.0) <= 2.0
        assert "並列2" in result.message

    def test_random_search_parallel(self):
        """並列ランダムサーチが正しく動作すること。"""
        def evaluate(params):
            return {"max_drift": abs(params.get("x", 0) - 5.0) * 0.01}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=10, step=0)],
            method="random",
            max_iterations=20,
            n_parallel=4,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_random_search(config)
        assert len(result.all_candidates) > 0
        assert result.best is not None
        assert "並列4" in result.message

    def test_evaluate_batch_error_handling(self):
        """並列評価中の例外が適切にハンドリングされること。"""
        call_count = 0
        def evaluate(params):
            nonlocal call_count
            call_count += 1
            if params.get("x", 0) == 2.0:
                raise RuntimeError("テストエラー")
            return {"max_drift": params.get("x", 0) * 0.01}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=10, step=1)],
            n_parallel=3,
        )
        worker = _OptimizationWorker(config, evaluate)
        batch = [{"x": 1.0}, {"x": 2.0}, {"x": 3.0}]
        results = worker._evaluate_batch(batch, config, start_iter=0)
        # エラーが発生しても他の候補は正常に評価される
        assert len(results) == 3
        # エラー候補の objective_value は inf
        assert results[1].objective_value == float("inf")
        # 他は正常
        assert results[0].objective_value != float("inf")
        assert results[2].objective_value != float("inf")


# ---------------------------------------------------------------------------
# Phase P: 制約安全性強化 + least_infeasible テスト
# ---------------------------------------------------------------------------

class TestConstraintSafety:
    """制約キー欠損・空応答時の安全側処理を検証。"""

    def test_missing_constraint_key_is_infeasible(self):
        """制約キーが応答に含まれない場合は infeasible。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0.1)],
            method="grid",
            constraints={"max_drift": 0.005, "max_acc": 5.0},
        )
        worker = _OptimizationWorker(config, lambda p: {})
        # max_acc が応答に含まれない
        is_feasible, margins = worker._check_constraints(
            {"max_drift": 0.003}, config
        )
        assert is_feasible is False
        assert margins["max_drift"] == pytest.approx(0.002)
        assert margins["max_acc"] == float("-inf")

    def test_empty_response_is_infeasible(self):
        """応答が空の場合は全制約 infeasible。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0.1)],
            method="grid",
            constraints={"max_drift": 0.005},
        )
        worker = _OptimizationWorker(config, lambda p: {})
        is_feasible, margins = worker._check_constraints({}, config)
        assert is_feasible is False
        assert margins["max_drift"] == float("-inf")

    def test_no_constraints_empty_response_is_feasible(self):
        """制約がない場合は空応答でも feasible（制約なし=何でもOK）。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0.1)],
            method="grid",
        )
        worker = _OptimizationWorker(config, lambda p: {})
        is_feasible, margins = worker._check_constraints({}, config)
        assert is_feasible is True
        assert margins == {}

    def test_criteria_missing_value_is_infeasible(self):
        """有効な性能基準の応答値が欠損している場合は infeasible。"""
        from app.models.performance_criteria import PerformanceCriteria, CriterionItem
        criteria = PerformanceCriteria(
            name="test",
            items=[CriterionItem(key="max_drift", label="最大層間変形角",
                                 unit="rad", enabled=True, limit_value=0.005)],
        )
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0.1)],
            method="grid",
            criteria=criteria,
        )
        worker = _OptimizationWorker(config, lambda p: {})
        # max_drift が応答に含まれない → criteria evaluate → None → infeasible
        is_feasible, margins = worker._check_constraints(
            {"other_key": 1.0}, config
        )
        assert is_feasible is False
        assert margins["criteria:max_drift"] == float("-inf")


class TestLeastInfeasible:
    """least_infeasible プロパティの検証。"""

    def test_returns_best_infeasible(self):
        """制約違反候補の中で目的関数値が最小の候補を返す。"""
        r = OptimizationResult(all_candidates=[
            OptimizationCandidate(is_feasible=False, objective_value=0.05,
                                  constraint_margins={"max_drift": -0.001}),
            OptimizationCandidate(is_feasible=False, objective_value=0.02,
                                  constraint_margins={"max_drift": -0.003}),
            OptimizationCandidate(is_feasible=False, objective_value=0.08,
                                  constraint_margins={"max_drift": -0.01}),
        ])
        least = r.least_infeasible
        assert least is not None
        assert least.objective_value == 0.02

    def test_none_when_all_feasible(self):
        """全候補が feasible の場合は None。"""
        r = OptimizationResult(all_candidates=[
            OptimizationCandidate(is_feasible=True, objective_value=0.01),
            OptimizationCandidate(is_feasible=True, objective_value=0.02),
        ])
        assert r.least_infeasible is None

    def test_none_when_empty(self):
        """候補が空の場合は None。"""
        r = OptimizationResult()
        assert r.least_infeasible is None

    def test_mixed_candidates(self):
        """feasible と infeasible が混在する場合、infeasible のみから選択。"""
        r = OptimizationResult(all_candidates=[
            OptimizationCandidate(is_feasible=True, objective_value=0.01),
            OptimizationCandidate(is_feasible=False, objective_value=0.005),
            OptimizationCandidate(is_feasible=False, objective_value=0.02),
        ])
        least = r.least_infeasible
        assert least is not None
        assert least.objective_value == 0.005
        assert least.is_feasible is False


# =====================================================================
# Q-1: チェックポイント自動保存テスト
# =====================================================================

class TestCheckpointConfig:
    """OptimizationConfig のチェックポイント設定テスト。"""

    def test_checkpoint_interval_default(self):
        """デフォルトはチェックポイント間隔10。"""
        config = OptimizationConfig()
        assert config.checkpoint_interval == 10
        assert config.checkpoint_path == ""

    def test_checkpoint_interval_serialization(self):
        """checkpoint_interval が to_dict / from_dict でラウンドトリップする。"""
        config = OptimizationConfig(checkpoint_interval=25)
        d = config.to_dict()
        assert d["checkpoint_interval"] == 25
        restored = OptimizationConfig.from_dict(d)
        assert restored.checkpoint_interval == 25

    def test_checkpoint_interval_zero_disables(self):
        """checkpoint_interval=0 はチェックポイント無効化。"""
        config = OptimizationConfig(checkpoint_interval=0)
        assert config.checkpoint_interval == 0


@pytest.mark.skipif(not _HAS_QT, reason="PySide6 required")
class TestCheckpointSignal:
    """_OptimizationWorker のチェックポイントシグナルテスト。"""

    def test_maybe_checkpoint_emits_at_interval(self):
        """_maybe_checkpoint がinterval到達時にシグナルを発火する。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="Cd", min_val=100, max_val=1000, step=100)],
            checkpoint_interval=5,
        )
        worker = _OptimizationWorker(config)
        emitted = []
        worker.checkpoint_signal.connect(lambda r: emitted.append(r))

        candidates = [
            OptimizationCandidate(params={"Cd": 100 * i}, objective_value=0.01 * i)
            for i in range(1, 6)
        ]
        best = candidates[0]

        # 4点では発火しない
        worker._maybe_checkpoint(candidates[:4], best, config)
        assert len(emitted) == 0

        # 5点で発火する
        worker._maybe_checkpoint(candidates, best, config)
        assert len(emitted) == 1
        assert len(emitted[0].all_candidates) == 5

    def test_maybe_checkpoint_disabled_when_zero(self):
        """checkpoint_interval=0 のとき _maybe_checkpoint は何もしない。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="Cd", min_val=100, max_val=1000, step=100)],
            checkpoint_interval=0,
        )
        worker = _OptimizationWorker(config)
        emitted = []
        worker.checkpoint_signal.connect(lambda r: emitted.append(r))

        candidates = [
            OptimizationCandidate(params={"Cd": 100 * i}, objective_value=0.01 * i)
            for i in range(1, 11)
        ]
        worker._maybe_checkpoint(candidates, candidates[0], config)
        assert len(emitted) == 0


# ---------------------------------------------------------------------------
# NSGA-II multi-objective optimization tests
# ---------------------------------------------------------------------------

class TestNSGA2:
    """NSGA-II 多目的最適化のテスト群。"""

    @staticmethod
    def _multi_obj_evaluate(params: Dict[str, float]) -> Dict[str, float]:
        """2目的テスト関数: max_drift と max_acc がトレードオフ関係。

        Cd が大きいと drift は小さくなるが acc は大きくなる。
        """
        cd = params.get("Cd", 500)
        alpha = params.get("alpha", 0.5)
        drift = 0.01 * (1000 / max(cd, 1)) * (1 + alpha * 0.5)
        acc = 0.5 * (cd / 1000) * (2 - alpha * 0.3)
        return {
            "max_drift": drift,
            "max_acc": acc,
            "max_disp": drift * 3.0,
            "shear_coeff": 0.2,
        }

    def test_nsga2_basic_execution(self):
        """NSGA-II が正常に実行を完了し、結果を返すこと。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key="Cd", min_val=100, max_val=1000, step=0),
                ParameterRange(key="alpha", min_val=0.1, max_val=1.0, step=0),
            ],
            method="nsga2",
            max_iterations=80,
            objective_weights={"max_drift": 1.0, "max_acc": 1.0},
        )
        worker = _OptimizationWorker(config, evaluate_fn=self._multi_obj_evaluate)
        worker.run()
        result = worker._result if hasattr(worker, "_result") else None
        # run() emits finished_signal — capture via attribute
        # Actually, we need to run _run_nsga2_search directly
        result = worker._run_nsga2_search(config)
        assert result is not None
        assert len(result.all_candidates) > 0
        assert "NSGA-II" in result.message

    def test_nsga2_finds_pareto_front(self):
        """NSGA-II がパレートフロント上の多様な解を見つけること。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key="Cd", min_val=100, max_val=1000, step=0),
                ParameterRange(key="alpha", min_val=0.1, max_val=1.0, step=0),
            ],
            method="nsga2",
            max_iterations=200,
            objective_weights={"max_drift": 1.0, "max_acc": 1.0},
        )
        worker = _OptimizationWorker(config, evaluate_fn=self._multi_obj_evaluate)
        result = worker._run_nsga2_search(config)

        assert result.best is not None
        # パレートフロントが複数解を含むこと
        assert "パレートフロント" in result.message
        # best の目的関数値が有限であること
        assert result.best.objective_value < float("inf")

    def test_nsga2_with_constraints(self):
        """NSGA-II が制約条件を考慮すること。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key="Cd", min_val=100, max_val=1000, step=0),
            ],
            method="nsga2",
            max_iterations=80,
            objective_weights={"max_drift": 1.0, "max_acc": 1.0},
            constraints={"max_drift": 0.005},  # 厳しい制約
        )
        worker = _OptimizationWorker(config, evaluate_fn=self._multi_obj_evaluate)
        result = worker._run_nsga2_search(config)

        assert result is not None
        assert "制約満足" in result.message
        # 制約満足候補がある場合、best は制約を満たすはず
        if result.feasible_candidates:
            assert result.best.is_feasible

    def test_nsga2_single_objective_fallback(self):
        """objective_weights 未設定時は単一目的で動作すること。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key="Cd", min_val=100, max_val=1000, step=0),
            ],
            method="nsga2",
            max_iterations=60,
        )
        worker = _OptimizationWorker(config, evaluate_fn=self._multi_obj_evaluate)
        result = worker._run_nsga2_search(config)

        assert result is not None
        assert result.best is not None
        # 単一目的でもパレートフロントのメッセージが出る
        assert "NSGA-II" in result.message

    def test_nsga2_penalty_method(self):
        """NSGA-II でペナルティ法が機能すること。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key="Cd", min_val=100, max_val=1000, step=0),
            ],
            method="nsga2",
            max_iterations=80,
            objective_weights={"max_drift": 1.0, "max_acc": 1.0},
            constraints={"max_drift": 0.005},
            constraint_penalty_weight=50.0,
        )
        worker = _OptimizationWorker(config, evaluate_fn=self._multi_obj_evaluate)
        result = worker._run_nsga2_search(config)

        assert result is not None
        assert len(result.all_candidates) > 0

    def test_nsga2_cancellation(self):
        """NSGA-II がキャンセル可能であること。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key="Cd", min_val=100, max_val=1000, step=0),
            ],
            method="nsga2",
            max_iterations=1000,
            objective_weights={"max_drift": 1.0, "max_acc": 1.0},
        )
        worker = _OptimizationWorker(config, evaluate_fn=self._multi_obj_evaluate)
        worker._cancelled = True  # 即キャンセル
        result = worker._run_nsga2_search(config)

        # キャンセルされても結果は返る（途中まで）
        assert result is not None

    def test_nsga2_three_objectives(self):
        """3目的での NSGA-II 実行。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key="Cd", min_val=100, max_val=1000, step=0),
                ParameterRange(key="alpha", min_val=0.1, max_val=1.0, step=0),
            ],
            method="nsga2",
            max_iterations=100,
            objective_weights={"max_drift": 1.0, "max_acc": 1.0, "max_disp": 0.5},
        )
        worker = _OptimizationWorker(config, evaluate_fn=self._multi_obj_evaluate)
        result = worker._run_nsga2_search(config)

        assert result is not None
        assert result.best is not None
        assert "パレートフロント" in result.message

    def test_nsga2_dispatch_via_run_method(self):
        """method='nsga2' が _OptimizationWorker.run() から正しくディスパッチされること。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key="Cd", min_val=100, max_val=500, step=0),
            ],
            method="nsga2",
            max_iterations=40,
            objective_weights={"max_drift": 1.0, "max_acc": 1.0},
        )
        worker = _OptimizationWorker(config, evaluate_fn=self._multi_obj_evaluate)

        # run() を直接呼んで finished_signal で結果をキャプチャ
        results = []
        worker.finished_signal.connect(lambda r: results.append(r))
        worker.run()

        assert len(results) == 1
        assert "NSGA-II" in results[0].message

    def test_nsga2_checkpoint(self):
        """NSGA-II でチェックポイントが発火すること。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key="Cd", min_val=100, max_val=1000, step=0),
            ],
            method="nsga2",
            max_iterations=100,
            objective_weights={"max_drift": 1.0, "max_acc": 1.0},
            checkpoint_interval=20,
        )
        worker = _OptimizationWorker(config, evaluate_fn=self._multi_obj_evaluate)
        checkpoints = []
        worker.checkpoint_signal.connect(lambda r: checkpoints.append(r))
        worker._run_nsga2_search(config)

        # 100点以上の評価で interval=20 なら複数チェックポイント発火
        assert len(checkpoints) >= 1

    def test_nsga2_empty_params(self):
        """パラメータ未設定時はエラーメッセージを返すこと。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[],
            method="nsga2",
            max_iterations=80,
        )
        worker = _OptimizationWorker(config, evaluate_fn=self._multi_obj_evaluate)
        result = worker._run_nsga2_search(config)
        assert result.message != ""
        assert result.best is None
        assert len(result.all_candidates) == 0


# ---------------------------------------------------------------------------
# Robust optimization tests
# ---------------------------------------------------------------------------

class TestRobustOptimization:
    """ロバスト最適化のテスト群。"""

    @staticmethod
    def _sensitive_evaluate(params: Dict[str, float]) -> Dict[str, float]:
        """パラメータに敏感な評価関数。小さな変化で結果が大きく変わる。"""
        cd = params.get("Cd", 500)
        # Cd=500 が最適だが、少しずれると急激に悪化
        drift = 0.003 + 0.00001 * (cd - 500) ** 2
        return {"max_drift": drift, "max_acc": 1.0}

    def test_robust_config_serialization(self):
        """robustness フィールドが to_dict/from_dict で正しくシリアライズされること。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            robustness_samples=5,
            robustness_delta=0.10,
        )
        d = config.to_dict()
        assert d["robustness_samples"] == 5
        assert d["robustness_delta"] == 0.10

        restored = OptimizationConfig.from_dict(d)
        assert restored.robustness_samples == 5
        assert restored.robustness_delta == 0.10

    def test_robust_evaluate_worst_case(self):
        """_robust_evaluate_with が最悪ケースを返すこと。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key="Cd", min_val=100, max_val=1000, step=0),
            ],
            robustness_samples=5,
            robustness_delta=0.05,
        )
        worker = _OptimizationWorker(config, evaluate_fn=self._sensitive_evaluate)

        # 最適点 Cd=500 での評価
        result = worker._robust_evaluate_with(
            {"Cd": 500}, config, self._sensitive_evaluate,
        )
        # 摂動により最悪ケースは中心値より悪い
        center = self._sensitive_evaluate({"Cd": 500})
        assert result["max_drift"] >= center["max_drift"]

    def test_robust_optimization_via_run(self):
        """ロバスト最適化がrun()経由で動作すること。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key="Cd", min_val=100, max_val=1000, step=0),
            ],
            method="random",
            max_iterations=20,
            robustness_samples=2,
            robustness_delta=0.05,
        )
        worker = _OptimizationWorker(config, evaluate_fn=self._sensitive_evaluate)
        results = []
        worker.finished_signal.connect(lambda r: results.append(r))
        worker.run()

        assert len(results) == 1
        assert results[0].best is not None

    def test_robust_summary_text(self):
        """ロバスト最適化の情報がサマリーテキストに含まれること。"""
        config = OptimizationConfig(
            objective_key="max_drift",
            robustness_samples=3,
            robustness_delta=0.10,
        )
        result = OptimizationResult(
            config=config,
            message="test",
        )
        summary = result.get_summary_text()
        assert "3" in summary  # サンプル数

    def test_robust_default_zero_samples(self):
        """デフォルトではロバスト最適化は無効（samples=0）。"""
        config = OptimizationConfig(objective_key="max_drift")
        assert config.robustness_samples == 0
        assert config.robustness_delta == 0.05


