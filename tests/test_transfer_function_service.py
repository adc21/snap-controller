"""
tests/test_transfer_function_service.py

TransferFunctionService および TransferFunctionPeakMinimizer のユニットテスト。
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.transfer_function_service import (
    TransferFunctionResult,
    TransferFunctionService,
    TransferFunctionPeakMinimizer,
    PeakMinimizationResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sine_signals(
    freq: float = 5.0,
    dt: float = 0.005,
    duration: float = 10.0,
    amplitude_ratio: float = 2.0,
    noise_level: float = 0.01,
) -> tuple[np.ndarray, np.ndarray]:
    """入力=正弦波、出力=振幅倍率付き正弦波+ノイズを生成。"""
    t = np.arange(0, duration, dt)
    x = np.sin(2 * np.pi * freq * t)
    y = amplitude_ratio * np.sin(2 * np.pi * freq * t) + noise_level * np.random.randn(len(t))
    return x, y


# ---------------------------------------------------------------------------
# TransferFunctionResult
# ---------------------------------------------------------------------------

class TestTransferFunctionResult:
    def test_to_dict(self):
        result = TransferFunctionResult(
            frequencies=np.array([1.0, 2.0, 3.0]),
            gain_db=np.array([0.0, 6.0, 3.0]),
            phase_deg=np.array([0.0, -45.0, -90.0]),
            peak_freq=2.0,
            peak_gain_db=6.0,
            freq_resolution=1.0,
        )
        d = result.to_dict()
        assert d["peak_frequency_Hz"] == 2.0
        assert d["peak_gain_dB"] == 6.0
        assert d["num_frequencies"] == 3

    def test_summary_text(self):
        result = TransferFunctionResult(
            frequencies=np.array([1.0]),
            gain_db=np.array([10.0]),
            phase_deg=np.array([0.0]),
            peak_freq=1.0,
            peak_gain_db=10.0,
            freq_resolution=0.1,
            input_label="Acc",
            output_label="Disp",
        )
        text = result.summary_text()
        assert "伝達関数解析結果" in text
        assert "1.00 Hz" in text

    def test_summary_text_with_coherence(self):
        result = TransferFunctionResult(
            frequencies=np.array([1.0, 2.0]),
            gain_db=np.array([0.0, 6.0]),
            phase_deg=np.array([0.0, -45.0]),
            coherence=np.array([0.9, 0.95]),
            peak_freq=2.0,
            peak_gain_db=6.0,
        )
        text = result.summary_text()
        assert "コヒーレンス" in text


# ---------------------------------------------------------------------------
# TransferFunctionService
# ---------------------------------------------------------------------------

class TestTransferFunctionService:
    def test_basic_computation(self):
        """基本的な伝達関数計算が完了する。"""
        svc = TransferFunctionService(dt=0.005)
        x, y = _make_sine_signals(freq=5.0, duration=10.0)
        result = svc.compute_transfer_function(x, y)

        assert isinstance(result, TransferFunctionResult)
        assert len(result.frequencies) > 0
        assert len(result.gain_db) == len(result.frequencies)
        assert len(result.phase_deg) == len(result.frequencies)
        assert result.coherence is not None

    def test_peak_detection(self):
        """ピーク周波数が入力の支配周波数付近にある。"""
        svc = TransferFunctionService(dt=0.005)
        x, y = _make_sine_signals(freq=5.0, duration=20.0, amplitude_ratio=3.0)
        result = svc.compute_transfer_function(x, y)

        # ピーク周波数が 5 Hz 前後に来る（Welch法の分解能により若干ずれる）
        assert result.peak_freq > 0

    def test_length_mismatch_raises(self):
        """入出力長不一致で ValueError。"""
        svc = TransferFunctionService()
        with pytest.raises(ValueError, match="一致しません"):
            svc.compute_transfer_function(np.array([1, 2, 3]), np.array([1, 2]))

    def test_too_short_raises(self):
        """信号が短すぎる場合に ValueError。"""
        svc = TransferFunctionService()
        with pytest.raises(ValueError, match="短すぎます"):
            svc.compute_transfer_function(np.array([1.0]), np.array([2.0]))

    def test_freq_range_filtering(self):
        """周波数範囲でフィルタリングできる。"""
        svc = TransferFunctionService(dt=0.005)
        x, y = _make_sine_signals(freq=5.0, duration=10.0)
        result = svc.compute_transfer_function(x, y, freq_range=(1.0, 10.0))

        assert result.frequencies[0] >= 1.0
        assert result.frequencies[-1] <= 10.0

    def test_labels(self):
        """ラベルが正しく設定される。"""
        svc = TransferFunctionService(dt=0.005)
        x, y = _make_sine_signals()
        result = svc.compute_transfer_function(
            x, y, input_label="加速度", output_label="変位"
        )
        assert result.input_label == "加速度"
        assert result.output_label == "変位"

    def test_multiple_outputs(self):
        """複数出力の一括計算ができる。"""
        svc = TransferFunctionService(dt=0.005)
        x, _ = _make_sine_signals(freq=5.0, duration=10.0)
        outputs = {
            "1F": 2.0 * x + 0.01 * np.random.randn(len(x)),
            "2F": 3.0 * x + 0.01 * np.random.randn(len(x)),
        }
        results = svc.compute_frequency_response_multiple_outputs(x, outputs)
        assert "1F" in results
        assert "2F" in results
        assert isinstance(results["1F"], TransferFunctionResult)

    def test_estimate_modal_parameters(self):
        """モーダルパラメータ推定が辞書を返す。"""
        svc = TransferFunctionService(dt=0.005)
        x, y = _make_sine_signals(freq=5.0, duration=20.0)
        tf = svc.compute_transfer_function(x, y)
        params = svc.estimate_modal_parameters(tf)
        assert "frequency_Hz" in params
        assert "damping_ratio" in params
        assert params["frequency_Hz"] >= 0


# ---------------------------------------------------------------------------
# TransferFunctionPeakMinimizer
# ---------------------------------------------------------------------------

class TestTransferFunctionPeakMinimizer:
    @pytest.fixture
    def sample_tf(self) -> TransferFunctionResult:
        svc = TransferFunctionService(dt=0.005)
        x, y = _make_sine_signals(freq=5.0, duration=20.0, amplitude_ratio=5.0)
        return svc.compute_transfer_function(x, y)

    def test_grid_optimization(self, sample_tf):
        """グリッドサーチで最適化が完了する。"""
        minimizer = TransferFunctionPeakMinimizer(sample_tf)
        result = minimizer.optimize(method="grid", grid_points=5)

        assert isinstance(result, PeakMinimizationResult)
        assert result.num_evaluations == 25  # 5x5
        assert result.optimal_damping_ratio > 0
        assert result.optimal_stiffness_ratio > 0

    def test_simplex_optimization(self, sample_tf):
        """シンプレックス法で最適化が完了する。"""
        minimizer = TransferFunctionPeakMinimizer(sample_tf)
        result = minimizer.optimize(method="simplex")

        assert isinstance(result, PeakMinimizationResult)
        assert result.num_evaluations > 0

    def test_peak_reduction(self, sample_tf):
        """最適化でピークゲインが低減される。"""
        minimizer = TransferFunctionPeakMinimizer(sample_tf)
        result = minimizer.optimize(method="grid", grid_points=10)

        assert result.optimized_peak_gain_db <= result.initial_peak_gain_db

    def test_summary_text(self, sample_tf):
        """summary_text が文字列を返す。"""
        minimizer = TransferFunctionPeakMinimizer(sample_tf)
        result = minimizer.optimize(method="grid", grid_points=3)
        text = result.summary_text()
        assert "ピーク最小化最適化結果" in text
