"""
tests/test_optimizer.py
Unit tests for optimizer module — ParameterRange, mock evaluate, GP, EI,
and Bayesian search logic, plus GA and SA.

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
# PySide6 mock
# ---------------------------------------------------------------------------
_HAS_QT = False
try:
    from PySide6.QtCore import QObject
    _HAS_QT = True
except (ImportError, OSError):
    _mock_qtcore = MagicMock()

    class _FakeSignal:
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

from app.services.optimizer import (
    ParameterRange,
    OptimizationConfig,
    OptimizationCandidate,
    OptimizationResult,
    _OptimizationWorker,
)

needs_qt = pytest.mark.skipif(not _HAS_QT, reason="PySide6 runtime not available")


# ===================================================================
# GA and SA Tests
# ===================================================================

class TestGeneticAlgorithm:
    """Test the Genetic Algorithm search method."""

    def test_finds_1d_minimum(self):
        """GA should find the minimum of a simple 1D function."""
        def evaluate(params):
            return {"max_drift": (params["x"] - 0.4) ** 2}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="genetic",
            max_iterations=40,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_genetic_algorithm(config)

        assert result.best is not None
        assert result.best.objective_value < 0.2
        assert abs(result.best.params["x"] - 0.4) < 0.3

    def test_finds_2d_minimum(self):
        """GA should find the minimum of a 2D quadratic function."""
        def evaluate(params):
            return {
                "max_drift": (params["x"] - 0.5) ** 2 + (params["y"] - 0.6) ** 2
            }

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key="x", min_val=0, max_val=1, step=0),
                ParameterRange(key="y", min_val=0, max_val=1, step=0),
            ],
            method="genetic",
            max_iterations=50,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_genetic_algorithm(config)

        assert result.best is not None
        assert result.best.objective_value < 0.3

    def test_message_includes_genetic_label(self):
        """Result message should mention genetic algorithm."""
        def evaluate(params):
            return {"max_drift": params["x"] ** 2}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="genetic",
            max_iterations=20,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_genetic_algorithm(config)
        assert "遺伝的" in result.message


class TestSimulatedAnnealing:
    """Test the Simulated Annealing search method."""

    def test_finds_1d_minimum(self):
        """SA should find the minimum of a simple 1D function."""
        def evaluate(params):
            return {"max_drift": (params["x"] - 0.3) ** 2}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="simulated_annealing",
            max_iterations=80,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_simulated_annealing(config)

        assert result.best is not None
        assert result.best.objective_value < 0.15
        assert abs(result.best.params["x"] - 0.3) < 0.3

    def test_finds_2d_minimum(self):
        """SA should find the minimum of a 2D quadratic function."""
        def evaluate(params):
            return {
                "max_drift": (params["x"] - 0.5) ** 2 + (params["y"] - 0.7) ** 2
            }

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[
                ParameterRange(key="x", min_val=0, max_val=1, step=0),
                ParameterRange(key="y", min_val=0, max_val=1, step=0),
            ],
            method="simulated_annealing",
            max_iterations=200,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_simulated_annealing(config)

        assert result.best is not None
        assert result.best.objective_value < 0.5

    def test_message_includes_annealing_label(self):
        """Result message should mention simulated annealing."""
        def evaluate(params):
            return {"max_drift": params["x"] ** 2}

        config = OptimizationConfig(
            objective_key="max_drift",
            parameters=[ParameterRange(key="x", min_val=0, max_val=1, step=0)],
            method="simulated_annealing",
            max_iterations=40,
        )
        worker = _OptimizationWorker(config, evaluate)
        result = worker._run_simulated_annealing(config)
        assert "焼きなまし法" in result.message
