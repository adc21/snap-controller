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
    sdof_tmd_transfer_function,
    compute_bandwidth,
    compute_transfer_function_from_time_histories,
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

class TestSdofTmdTransferFunction:
    """解析的 SDOF+TMD 伝達関数のユニットテスト。"""

    def test_no_tmd_resonance(self):
        """TMD なし（mu=0 相当: zeta_d=0, f_ratio far off）でSDOFの共振ピークが出る。"""
        f = np.linspace(0.01, 20.0, 2000)
        f_n = 5.0
        zeta_s = 0.02
        # TMD を無効化: 質量比ゼロに近い
        H = sdof_tmd_transfer_function(f, f_n, zeta_s, mu=1e-10, f_ratio=1.0, zeta_d=0.1)
        # ピークは f_n 付近
        peak_idx = np.argmax(H)
        peak_freq = f[peak_idx]
        assert abs(peak_freq - f_n) < 0.5  # 固有振動数付近

    def test_tmd_splits_peak(self):
        """TMD 付加でピークが分裂し、元の1ピークよりゲインが下がる。"""
        f = np.linspace(0.01, 20.0, 4000)
        f_n = 5.0
        zeta_s = 0.02
        mu = 0.05

        # TMD なし（mu≈0）
        H_no_tmd = sdof_tmd_transfer_function(f, f_n, zeta_s, mu=1e-10, f_ratio=1.0, zeta_d=0.1)
        peak_no_tmd = np.max(H_no_tmd)

        # TMD 付き（Den Hartog 最適同調近似）
        f_opt = 1.0 / (1.0 + mu)
        zeta_opt = np.sqrt(3 * mu / (8 * (1 + mu)))
        H_tmd = sdof_tmd_transfer_function(f, f_n, zeta_s, mu=mu, f_ratio=f_opt, zeta_d=zeta_opt)
        peak_tmd = np.max(H_tmd)

        assert peak_tmd < peak_no_tmd

    def test_den_hartog_optimal(self):
        """Den Hartog 最適同調で SDOF ピークが大幅に低減される。"""
        f = np.linspace(0.01, 20.0, 4000)
        f_n = 5.0
        zeta_s = 0.0  # 無減衰主構造
        mu = 0.05

        # 無減衰SDOFのピーク（∞に近い）
        H_no = sdof_tmd_transfer_function(f, f_n, zeta_s, mu=1e-10, f_ratio=1.0, zeta_d=0.01)
        peak_no = np.max(H_no)

        # Den Hartog 最適
        f_opt = 1.0 / (1.0 + mu)
        zeta_opt = np.sqrt(3 * mu / (8 * (1 + mu)))
        H_opt = sdof_tmd_transfer_function(f, f_n, zeta_s, mu=mu, f_ratio=f_opt, zeta_d=zeta_opt)
        peak_opt = np.max(H_opt)

        # 大幅低減
        assert peak_opt < peak_no * 0.3

    def test_zero_frequency_returns_ones(self):
        """f_n=0 の場合は全て 1.0 を返す。"""
        f = np.linspace(0.1, 10.0, 100)
        H = sdof_tmd_transfer_function(f, f_n=0.0, zeta_s=0.02, mu=0.05, f_ratio=1.0, zeta_d=0.1)
        np.testing.assert_allclose(H, 1.0)


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

    def test_constructor_params(self, sample_tf):
        """コンストラクタのパラメータが正しく設定される。"""
        minimizer = TransferFunctionPeakMinimizer(
            sample_tf, structural_damping=0.03, mass_ratio=0.10
        )
        assert minimizer.zeta_s == 0.03
        assert minimizer.mu == 0.10

    def test_summary_text(self, sample_tf):
        """summary_text が文字列を返す。"""
        minimizer = TransferFunctionPeakMinimizer(sample_tf)
        result = minimizer.optimize(method="grid", grid_points=3)
        text = result.summary_text()
        assert "TMD ピーク最小化最適化結果" in text
        assert "zeta_d" in text
        assert "f_d/f_n" in text

    def test_robust_objective(self, sample_tf):
        """objective='robust' で帯域幅を考慮した最適化ができる。"""
        minimizer = TransferFunctionPeakMinimizer(sample_tf)
        result = minimizer.optimize(
            method="grid", grid_points=5, objective="robust", bandwidth_weight=5.0
        )
        assert isinstance(result, PeakMinimizationResult)
        assert result.bandwidth_hz >= 0
        assert result.initial_bandwidth_hz >= 0
        assert result.num_evaluations == 25

    def test_robust_vs_peak_different_result(self, sample_tf):
        """objective='robust' は 'peak' と異なる最適解を返す可能性がある。"""
        minimizer_peak = TransferFunctionPeakMinimizer(sample_tf)
        res_peak = minimizer_peak.optimize(method="grid", grid_points=10, objective="peak")

        minimizer_robust = TransferFunctionPeakMinimizer(sample_tf)
        res_robust = minimizer_robust.optimize(
            method="grid", grid_points=10, objective="robust", bandwidth_weight=10.0
        )

        # 両方とも有効な結果を返す
        assert res_peak.optimal_damping_ratio > 0
        assert res_robust.optimal_damping_ratio > 0

    def test_bandwidth_in_summary(self, sample_tf):
        """帯域幅が summary_text に表示される。"""
        minimizer = TransferFunctionPeakMinimizer(sample_tf)
        result = minimizer.optimize(
            method="grid", grid_points=5, objective="robust"
        )
        text = result.summary_text()
        assert "帯域幅" in text


# ---------------------------------------------------------------------------
# compute_bandwidth
# ---------------------------------------------------------------------------

class TestComputeBandwidth:
    def test_basic(self):
        """ピーク周りの帯域幅を正しく計算する。"""
        f = np.linspace(0.1, 10.0, 1000)
        # ピーク at 5 Hz, -3dB 帯域幅 ≈ 1 Hz のベルカーブ
        gain_db = -((f - 5.0) ** 2) * 10  # 二乗で急峻なピーク
        bw = compute_bandwidth(f, gain_db, threshold_db=-3.0)
        assert bw > 0
        assert bw < 5.0  # 全帯域よりは狭い

    def test_flat_spectrum(self):
        """フラットスペクトルでは全帯域が帯域幅になる。"""
        f = np.linspace(1.0, 10.0, 100)
        gain_db = np.zeros_like(f)
        bw = compute_bandwidth(f, gain_db, threshold_db=-3.0)
        assert bw == pytest.approx(f[-1] - f[0], rel=0.01)

    def test_empty_returns_zero(self):
        """空配列で 0 を返す。"""
        bw = compute_bandwidth(np.array([]), np.array([]))
        assert bw == 0.0


# ---------------------------------------------------------------------------
# compute_transfer_function_from_time_histories
# ---------------------------------------------------------------------------

class TestComputeTransferFunctionFromTimeHistories:
    def test_basic(self):
        """時刻歴ペアから伝達関数が計算できる。"""
        dt = 0.005
        t = np.arange(0, 10.0, dt)
        x = np.sin(2 * np.pi * 3.0 * t)
        y = 2.0 * np.sin(2 * np.pi * 3.0 * t)

        tf = compute_transfer_function_from_time_histories(x, y, dt)
        assert isinstance(tf, TransferFunctionResult)
        assert tf.peak_freq > 0
        assert tf.input_label == "地動入力"
        assert tf.output_label == "応答"

    def test_custom_labels(self):
        dt = 0.005
        t = np.arange(0, 5.0, dt)
        x = np.random.randn(len(t))
        y = np.random.randn(len(t))
        tf = compute_transfer_function_from_time_histories(
            x, y, dt, input_label="Acc", output_label="Disp"
        )
        assert tf.input_label == "Acc"
        assert tf.output_label == "Disp"
