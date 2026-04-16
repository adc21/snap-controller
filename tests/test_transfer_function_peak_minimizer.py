"""
tests/test_transfer_function_peak_minimizer.py

Transfer function peak minimization optimizer tests.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.services.transfer_function_service import (
    TransferFunctionService,
    TransferFunctionResult,
    TransferFunctionPeakMinimizer,
    PeakMinimizationResult,
)


class TestPeakMinimizationResult:
    """PeakMinimizationResult のテスト。"""

    def test_init(self) -> None:
        result = PeakMinimizationResult(
            optimal_damping_ratio=0.15,
            optimal_stiffness_ratio=0.20,
            initial_peak_gain_db=20.0,
            optimized_peak_gain_db=15.0,
            peak_reduction_db=5.0,
            initial_peak_freq=2.0,
            optimized_peak_freq=2.05,
            num_evaluations=100,
        )
        assert result.optimal_damping_ratio == 0.15
        assert result.peak_reduction_db == 5.0

    def test_summary_text(self) -> None:
        result = PeakMinimizationResult(
            optimal_damping_ratio=0.15,
            optimal_stiffness_ratio=0.20,
            initial_peak_gain_db=20.0,
            optimized_peak_gain_db=15.0,
            peak_reduction_db=5.0,
            initial_peak_freq=2.0,
            optimized_peak_freq=2.05,
            num_evaluations=100,
        )
        text = result.summary_text()
        assert "ピーク最小化最適化結果" in text
        assert "20.00 dB" in text
        assert "15.00 dB" in text
        assert "5.00 dB" in text


class TestTransferFunctionPeakMinimizer:
    """TransferFunctionPeakMinimizer のテスト。"""

    def _create_sample_tf(self) -> TransferFunctionResult:
        """テスト用の標準的な伝達関数を作成。"""
        f = np.logspace(-1, 1.5, 100)  # 0.1～31.6 Hz
        # 2 Hz でピークを持つ 2 次共振を模擬
        freq_ratio = f / 2.0
        gain_linear = 1.0 / np.sqrt((1 - freq_ratio ** 2) ** 2 + (2 * 0.05 * freq_ratio) ** 2)
        gain_db = 20 * np.log10(gain_linear)

        phase_rad = -np.arctan2(2 * 0.05 * freq_ratio, 1 - freq_ratio ** 2)
        phase_deg = np.degrees(phase_rad)

        return TransferFunctionResult(
            frequencies=f,
            gain_db=gain_db,
            phase_deg=phase_deg,
            coherence=np.ones_like(f),
            input_label="Base",
            output_label="1F",
            peak_freq=2.0,
            peak_gain_db=float(np.max(gain_db)),
            freq_resolution=0.05,
        )

    def test_init(self) -> None:
        tf = self._create_sample_tf()
        minimizer = TransferFunctionPeakMinimizer(tf)
        assert minimizer.natural_freq == 2.0  # ピーク周波数から推定

    def test_init_with_explicit_frequency(self) -> None:
        tf = self._create_sample_tf()
        minimizer = TransferFunctionPeakMinimizer(tf, natural_frequency=1.8)
        assert minimizer.natural_freq == 1.8

    def test_synthesize_damper_response(self) -> None:
        tf = self._create_sample_tf()
        minimizer = TransferFunctionPeakMinimizer(tf)

        # ダンパーなしでは元のゲイン
        tf_orig_peak = tf.peak_gain_db

        # ダンパー付き
        tf_damped = minimizer._synthesize_damper_response(
            damping_ratio=0.15, stiffness_ratio=0.20
        )

        # ダンパーによってピークゲインが低下すべき
        assert tf_damped.peak_gain_db < tf_orig_peak

        # 周波数は同じ長さ
        assert len(tf_damped.frequencies) == len(tf.frequencies)

    def test_evaluate_objective(self) -> None:
        tf = self._create_sample_tf()
        minimizer = TransferFunctionPeakMinimizer(tf)

        # 妥当なパラメータ範囲
        peak_damped = minimizer._evaluate_objective(
            damping_ratio=0.15, stiffness_ratio=0.20
        )
        assert isinstance(peak_damped, float)
        assert peak_damped < 1000.0  # ペナルティではない

        # パラメータが範囲外の場合
        peak_invalid = minimizer._evaluate_objective(
            damping_ratio=-0.1, stiffness_ratio=0.20
        )
        assert peak_invalid == 1000.0  # ペナルティ

    def test_optimize_grid(self) -> None:
        tf = self._create_sample_tf()
        minimizer = TransferFunctionPeakMinimizer(tf)

        result = minimizer.optimize(
            damping_range=(0.01, 0.30),
            stiffness_range=(0.01, 0.50),
            method="grid",
            grid_points=10,
        )

        assert isinstance(result, PeakMinimizationResult)
        assert result.optimal_damping_ratio > 0
        assert result.optimal_stiffness_ratio > 0
        assert result.optimized_peak_gain_db < result.initial_peak_gain_db
        assert result.peak_reduction_db > 0
        assert result.num_evaluations > 0

    def test_optimize_simplex(self) -> None:
        tf = self._create_sample_tf()
        minimizer = TransferFunctionPeakMinimizer(tf)

        result = minimizer.optimize(
            damping_range=(0.01, 0.30),
            stiffness_range=(0.01, 0.50),
            method="simplex",
        )

        assert isinstance(result, PeakMinimizationResult)
        assert result.optimal_damping_ratio > 0
        assert result.optimal_stiffness_ratio > 0
        assert result.optimized_peak_gain_db < result.initial_peak_gain_db

    def test_optimization_improves_peak(self) -> None:
        """最適化がピークゲインを改善することを確認。"""
        tf = self._create_sample_tf()
        minimizer = TransferFunctionPeakMinimizer(tf)

        result = minimizer.optimize(method="grid", grid_points=15)

        # ピークゲインが低下していること
        improvement = result.initial_peak_gain_db - result.optimized_peak_gain_db
        assert improvement > 0, f"No improvement: {improvement} dB"

    def test_multiple_optimizations_convergence(self) -> None:
        """複数回の最適化が収束することを確認。"""
        tf = self._create_sample_tf()
        minimizer1 = TransferFunctionPeakMinimizer(tf)
        result1 = minimizer1.optimize(method="grid", grid_points=10)

        minimizer2 = TransferFunctionPeakMinimizer(tf)
        result2 = minimizer2.optimize(method="grid", grid_points=10)

        # ピークゲインがほぼ同じ（収束している）
        assert abs(result1.optimized_peak_gain_db - result2.optimized_peak_gain_db) < 0.1

    def test_eval_count_tracked(self) -> None:
        """評価回数が正確に追跡されることを確認。"""
        tf = self._create_sample_tf()
        minimizer = TransferFunctionPeakMinimizer(tf)

        grid_points = 8
        result = minimizer.optimize(method="grid", grid_points=grid_points)

        # グリッドサーチは grid_points^2 回の評価
        expected_evals = grid_points * grid_points
        assert result.num_evaluations == expected_evals

    def test_parameter_bounds_respected(self) -> None:
        """最適パラメータが指定範囲内であることを確認。"""
        tf = self._create_sample_tf()
        minimizer = TransferFunctionPeakMinimizer(tf)

        d_min, d_max = (0.02, 0.25)
        s_min, s_max = (0.02, 0.40)

        result = minimizer.optimize(
            damping_range=(d_min, d_max),
            stiffness_range=(s_min, s_max),
            method="grid",
            grid_points=10,
        )

        assert d_min <= result.optimal_damping_ratio <= d_max
        assert s_min <= result.optimal_stiffness_ratio <= s_max

    def test_zero_frequency_edge_case(self) -> None:
        """周波数がゼロの場合のエッジケース処理。"""
        # 周波数が非常に小さいケース
        f = np.array([0.001, 0.01, 0.1, 1.0, 2.0, 5.0, 10.0])
        gain_db = np.array([0, 5, 10, 15, 20, 15, 10])

        tf = TransferFunctionResult(
            frequencies=f,
            gain_db=gain_db,
            phase_deg=np.zeros_like(f),
            input_label="Test",
            output_label="Test",
            peak_freq=2.0,
            peak_gain_db=20.0,
        )

        minimizer = TransferFunctionPeakMinimizer(tf, natural_frequency=2.0)
        result = minimizer.optimize(method="grid", grid_points=5)

        assert result.optimized_peak_gain_db < 20.0


class TestIntegrationTransferFunctionPeakMinimization:
    """伝達関数とピーク最小化の統合テスト。"""

    def test_service_with_minimizer(self) -> None:
        """TransferFunctionService と Minimizer の統合。"""
        from scipy import signal as sp_signal

        dt = 0.01
        duration = 20.0
        t = np.arange(0, duration, dt)
        n = len(t)

        np.random.seed(42)
        x = np.random.randn(n)

        # 2 Hz で共振する 2 次系
        omega0 = 2 * math.pi * 2.0
        zeta = 0.05
        b_ct = [omega0 ** 2]
        a_ct = [1.0, 2 * zeta * omega0, omega0 ** 2]
        fs = 1.0 / dt
        b_dt, a_dt = sp_signal.bilinear(b_ct, a_ct, fs=fs)
        y = sp_signal.lfilter(b_dt, a_dt, x)

        # 伝達関数を計算
        svc = TransferFunctionService(dt=dt, nperseg=2048)
        tf_result = svc.compute_transfer_function(
            input_signal=x, output_signal=y, input_label="input", output_label="output"
        )

        assert tf_result.peak_freq > 0
        assert tf_result.peak_gain_db > 0

        # ピーク最小化を実行
        minimizer = TransferFunctionPeakMinimizer(tf_result)
        opt_result = minimizer.optimize(method="grid", grid_points=12)

        # ピークが低下していることを確認
        assert opt_result.optimized_peak_gain_db < tf_result.peak_gain_db
        assert opt_result.peak_reduction_db > 0

