"""
app/services/transfer_function_service.py

伝達関数・周波数応答解析モジュール
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
from scipy import signal


@dataclass
class TransferFunctionResult:
    """伝達関数（周波数応答）の計算結果。"""

    frequencies: np.ndarray
    gain_db: np.ndarray
    phase_deg: np.ndarray
    coherence: Optional[np.ndarray] = None

    input_label: str = "Input"
    output_label: str = "Output"

    peak_freq: float = 0.0
    peak_gain_db: float = 0.0
    freq_resolution: float = 0.0

    def to_dict(self) -> Dict:
        """結果を辞書形式で返す。"""
        return {
            "peak_frequency_Hz": self.peak_freq,
            "peak_gain_dB": self.peak_gain_db,
            "freq_resolution_Hz": self.freq_resolution,
            "num_frequencies": len(self.frequencies),
            "input_label": self.input_label,
            "output_label": self.output_label,
        }

    def summary_text(self) -> str:
        """結果をテキストで要約。"""
        lines = [
            f"伝達関数解析結果: {self.output_label} / {self.input_label}",
            f"  1次ピーク周波数: {self.peak_freq:.2f} Hz",
            f"  1次ピークゲイン: {self.peak_gain_db:.2f} dB",
            f"  周波数分解能: {self.freq_resolution:.4f} Hz",
            f"  計算周波数点数: {len(self.frequencies)}",
        ]
        if self.coherence is not None:
            mean_coh = np.mean(self.coherence)
            lines.append(f"  平均コヒーレンス: {mean_coh:.3f}")
        return "\n".join(lines)


class TransferFunctionService:
    """時刻歴データから伝達関数を計算するサービス。"""

    def __init__(
        self,
        dt: float = 0.005,
        nperseg: Optional[int] = None,
        window: str = "hann",
        noverlap_ratio: float = 0.5,
        welch_nfft: Optional[int] = None,
    ) -> None:
        """
        初期化。

        Parameters
        ----------
        dt : float
            時刻刻み [秒]。デフォルト 0.005 秒（200 Hz）。
        nperseg : int, optional
            Welch 法の 1 セグメント長 [サンプル]。
        window : str
            窓関数。"hann", "hamming", "blackman" など。
        noverlap_ratio : float
            セグメントオーバーラップ比（0～1）。デフォルト 0.5。
        welch_nfft : int, optional
            FFT 長。指定なければ nperseg から自動決定。
        """
        self.dt = dt
        self.nperseg = nperseg
        self.window = window
        self.noverlap_ratio = noverlap_ratio
        self.welch_nfft = welch_nfft

    def compute_transfer_function(
        self,
        input_signal: np.ndarray,
        output_signal: np.ndarray,
        input_label: str = "Input",
        output_label: str = "Output",
        detrend: str = "linear",
        freq_range: Optional[Tuple[float, float]] = None,
    ) -> TransferFunctionResult:
        """伝達関数を計算。"""
        x = np.asarray(input_signal, dtype=np.float64).ravel()
        y = np.asarray(output_signal, dtype=np.float64).ravel()

        if len(x) != len(y):
            raise ValueError(
                f"入出力信号の長さが一致しません: {len(x)} vs {len(y)}"
            )

        if len(x) < 2:
            raise ValueError("時系列が短すぎます（最低 2 サンプル必要）")

        # デトレンド
        if detrend:
            x = signal.detrend(x, type=detrend)
            y = signal.detrend(y, type=detrend)

        # Welch 法パラメータの決定
        n_samples = len(x)
        nperseg = self.nperseg or min(n_samples // 4, 2048)
        noverlap = int(nperseg * self.noverlap_ratio)
        nfft = self.welch_nfft or nperseg * 2

        # Welch 法で電力スペクトラム及び相互スペクトラムを計算
        f, pxx = signal.welch(
            x,
            fs=1.0 / self.dt,
            window=self.window,
            nperseg=nperseg,
            noverlap=noverlap,
            nfft=nfft,
        )
        f, pyy = signal.welch(
            y,
            fs=1.0 / self.dt,
            window=self.window,
            nperseg=nperseg,
            noverlap=noverlap,
            nfft=nfft,
        )
        f, pxy = signal.csd(
            x,
            y,
            fs=1.0 / self.dt,
            window=self.window,
            nperseg=nperseg,
            noverlap=noverlap,
            nfft=nfft,
        )

        # 伝達関数 H(f) = Pxy(f) / Pxx(f)
        eps = 1e-12
        h = np.zeros_like(pxy)
        np.divide(pxy, pxx + eps, where=(pxx > eps), out=h)
        h[pxx <= eps] = 0

        # ゲイン [dB] と位相 [degree]
        magnitude = np.abs(h)
        magnitude_db = 20 * np.log10(magnitude + eps)
        phase_rad = np.angle(h)
        phase_deg = np.degrees(phase_rad)

        # コヒーレンス
        coh_squared = np.zeros_like(pxx, dtype=float)
        np.divide(
            np.abs(pxy) ** 2,
            (pxx + eps) * (pyy + eps),
            where=((pxx > eps) & (pyy > eps)),
            out=coh_squared,
        )
        coh_squared = np.clip(coh_squared, 0, 1)
        coherence = np.sqrt(coh_squared)

        # 周波数範囲でのフィルタリング
        if freq_range is not None:
            fmin, fmax = freq_range
            mask = (f >= fmin) & (f <= fmax)
            f = f[mask]
            magnitude_db = magnitude_db[mask]
            phase_deg = phase_deg[mask]
            coherence = coherence[mask]

        # 1次ピークの検出
        peak_freq, peak_gain_db = self._find_first_peak(f, magnitude_db)

        # 周波数分解能
        freq_resolution = f[1] - f[0] if len(f) > 1 else 0.0

        return TransferFunctionResult(
            frequencies=f,
            gain_db=magnitude_db,
            phase_deg=phase_deg,
            coherence=coherence,
            input_label=input_label,
            output_label=output_label,
            peak_freq=peak_freq,
            peak_gain_db=peak_gain_db,
            freq_resolution=freq_resolution,
        )

    @staticmethod
    def _find_first_peak(
        frequencies: np.ndarray, gain_db: np.ndarray, min_freq: float = 0.1
    ) -> Tuple[float, float]:
        """ゲイン曲線から 1 次ピークを検出。"""
        mask = frequencies >= min_freq
        if not np.any(mask):
            return 0.0, float("-inf")

        f_filtered = frequencies[mask]
        g_filtered = gain_db[mask]

        peak_idx = np.argmax(g_filtered)
        peak_freq = float(f_filtered[peak_idx])
        peak_gain = float(g_filtered[peak_idx])

        return peak_freq, peak_gain

    def compute_frequency_response_multiple_outputs(
        self,
        input_signal: np.ndarray,
        output_signals: Dict[str, np.ndarray],
        freq_range: Optional[Tuple[float, float]] = None,
    ) -> Dict[str, TransferFunctionResult]:
        """単一入力・複数出力の伝達関数を一括計算。"""
        results = {}
        for label, output_signal in output_signals.items():
            result = self.compute_transfer_function(
                input_signal=input_signal,
                output_signal=output_signal,
                input_label="Input",
                output_label=label,
                freq_range=freq_range,
            )
            results[label] = result
        return results

    def estimate_modal_parameters(
        self, transfer_function: TransferFunctionResult, mode_index: int = 1
    ) -> Dict[str, float]:
        """伝達関数からモーダルパラメータを推定。"""
        f = transfer_function.frequencies
        gain = transfer_function.gain_db

        peak_freq, peak_gain = transfer_function.peak_freq, transfer_function.peak_gain_db

        if peak_freq <= 0:
            return {"frequency_Hz": 0.0, "damping_ratio": 0.0}

        half_power_gain = peak_gain - 3.0
        mask_half = gain >= half_power_gain
        f_half = f[mask_half]

        if len(f_half) < 2:
            return {"frequency_Hz": peak_freq, "damping_ratio": 0.0}

        delta_f = f_half[-1] - f_half[0]
        damping_ratio = delta_f / (2 * peak_freq) if peak_freq > 0 else 0.0

        return {
            "frequency_Hz": peak_freq,
            "damping_ratio": min(damping_ratio, 0.5),
        }


# Phase 2-C: Transfer Function Peak Minimization Optimizer

@dataclass
class PeakMinimizationResult:
    """Peak minimization optimization result."""
    optimal_damping_ratio: float
    optimal_stiffness_ratio: float
    initial_peak_gain_db: float
    optimized_peak_gain_db: float
    peak_reduction_db: float
    initial_peak_freq: float
    optimized_peak_freq: float
    num_evaluations: int

    def summary_text(self) -> str:
        """最適化結果をテキスト形式でまとめます。"""
        reduction_pct = (
            (self.initial_peak_gain_db - self.optimized_peak_gain_db)
            / abs(self.initial_peak_gain_db)
            * 100
            if self.initial_peak_gain_db != 0
            else 0
        )
        lines = [
            "ピーク最小化最適化結果",
            f"  初期ピークゲイン: {self.initial_peak_gain_db:.2f} dB",
            f"  最適化後ピークゲイン: {self.optimized_peak_gain_db:.2f} dB",
            f"  ゲイン低減量: {self.peak_reduction_db:.2f} dB ({reduction_pct:.1f}%)",
            f"  初期ピーク周波数: {self.initial_peak_freq:.2f} Hz",
            f"  最適化後ピーク周波数: {self.optimized_peak_freq:.2f} Hz",
            f"  最適減衰定数: {self.optimal_damping_ratio:.4f}",
            f"  最適剛性比: {self.optimal_stiffness_ratio:.4f}",
            f"  評価回数: {self.num_evaluations}",
        ]
        return "\n".join(lines)


class TransferFunctionPeakMinimizer:
    """Optimize damper parameters to minimize transfer function peak gain."""

    def __init__(
        self,
        transfer_function: TransferFunctionResult,
        natural_frequency: Optional[float] = None,
    ) -> None:
        """Initialize minimizer."""
        self.tf = transfer_function
        self.natural_freq = natural_frequency or transfer_function.peak_freq
        self._eval_count = 0

    def _synthesize_damper_response(
        self, damping_ratio: float, stiffness_ratio: float
    ) -> TransferFunctionResult:
        """Synthesize damped transfer function."""
        f = self.tf.frequencies
        gain_db_orig = self.tf.gain_db.copy()

        peak_idx = np.argmax(gain_db_orig)
        if peak_idx == 0 or peak_idx >= len(f) - 1:
            peak_idx = np.clip(peak_idx, 1, len(f) - 2)

        freq_ratio = f / self.natural_freq if self.natural_freq > 0 else f + 1e-6
        attenuation_db = -20 * np.log10(
            1 + stiffness_ratio * (1 + 2 * damping_ratio) + 1e-12
        )
        gain_db_damped = gain_db_orig + attenuation_db

        return TransferFunctionResult(
            frequencies=f.copy(),
            gain_db=gain_db_damped,
            phase_deg=self.tf.phase_deg.copy(),
            coherence=self.tf.coherence.copy() if self.tf.coherence is not None else None,
            input_label=self.tf.input_label,
            output_label=self.tf.output_label + " (damped)",
            peak_freq=float(f[np.argmax(gain_db_damped)]),
            peak_gain_db=float(np.max(gain_db_damped)),
        )

    def _evaluate_objective(self, damping_ratio: float, stiffness_ratio: float) -> float:
        """Evaluate objective function (peak gain to minimize)."""
        self._eval_count += 1

        if not (0.001 <= damping_ratio <= 0.5):
            return 1000.0
        if not (0.001 <= stiffness_ratio <= 1.0):
            return 1000.0

        tf_damped = self._synthesize_damper_response(damping_ratio, stiffness_ratio)
        return tf_damped.peak_gain_db

    def optimize(
        self,
        damping_range: Tuple[float, float] = (0.01, 0.30),
        stiffness_range: Tuple[float, float] = (0.01, 0.50),
        method: str = "grid",
        grid_points: int = 20,
    ) -> PeakMinimizationResult:
        """Run peak minimization optimization."""
        self._eval_count = 0
        initial_peak = self.tf.peak_gain_db
        initial_freq = self.tf.peak_freq

        if method == "grid":
            result = self._optimize_grid(
                damping_range, stiffness_range, grid_points
            )
        else:
            result = self._optimize_simplex(damping_range, stiffness_range)

        tf_opt = self._synthesize_damper_response(
            result.optimal_damping_ratio, result.optimal_stiffness_ratio
        )
        peak_reduction = initial_peak - result.optimized_peak_gain_db

        return PeakMinimizationResult(
            optimal_damping_ratio=result.optimal_damping_ratio,
            optimal_stiffness_ratio=result.optimal_stiffness_ratio,
            initial_peak_gain_db=initial_peak,
            optimized_peak_gain_db=result.optimized_peak_gain_db,
            peak_reduction_db=peak_reduction,
            initial_peak_freq=initial_freq,
            optimized_peak_freq=tf_opt.peak_freq,
            num_evaluations=self._eval_count,
        )

    def _optimize_grid(
        self,
        damping_range: Tuple[float, float],
        stiffness_range: Tuple[float, float],
        grid_points: int,
    ) -> PeakMinimizationResult:
        """Grid search optimization."""
        d_min, d_max = damping_range
        s_min, s_max = stiffness_range

        d_vals = np.linspace(d_min, d_max, grid_points)
        s_vals = np.linspace(s_min, s_max, grid_points)

        best_peak = float("inf")
        best_d = d_min
        best_s = s_min

        for d in d_vals:
            for s in s_vals:
                peak = self._evaluate_objective(d, s)
                if peak < best_peak:
                    best_peak = peak
                    best_d = d
                    best_s = s

        return PeakMinimizationResult(
            optimal_damping_ratio=best_d,
            optimal_stiffness_ratio=best_s,
            initial_peak_gain_db=self.tf.peak_gain_db,
            optimized_peak_gain_db=best_peak,
            peak_reduction_db=self.tf.peak_gain_db - best_peak,
            initial_peak_freq=self.tf.peak_freq,
            optimized_peak_freq=0.0,
            num_evaluations=self._eval_count,
        )

    def _optimize_simplex(
        self,
        damping_range: Tuple[float, float],
        stiffness_range: Tuple[float, float],
    ) -> PeakMinimizationResult:
        """Simplex method optimization."""
        from scipy.optimize import minimize

        def objective(params: np.ndarray) -> float:
            d, s = params
            return self._evaluate_objective(float(d), float(s))

        x0 = np.array(
            [
                (damping_range[0] + damping_range[1]) / 2,
                (stiffness_range[0] + stiffness_range[1]) / 2,
            ]
        )

        bounds = [damping_range, stiffness_range]

        result = minimize(
            objective,
            x0,
            method="L-BFGS-B",
            bounds=bounds,
            options={"ftol": 1e-4, "maxiter": 100},
        )

        best_d, best_s = result.x
        best_peak = float(result.fun)

        return PeakMinimizationResult(
            optimal_damping_ratio=float(best_d),
            optimal_stiffness_ratio=float(best_s),
            initial_peak_gain_db=self.tf.peak_gain_db,
            optimized_peak_gain_db=best_peak,
            peak_reduction_db=self.tf.peak_gain_db - best_peak,
            initial_peak_freq=self.tf.peak_freq,
            optimized_peak_freq=0.0,
            num_evaluations=self._eval_count,
        )
