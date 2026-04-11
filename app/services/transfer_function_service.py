"""
app/services/transfer_function_service.py

伝達関数・周波数応答解析モジュール
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
from scipy import signal

logger = logging.getLogger(__name__)


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

        # NaN/Inf 防御
        if not np.all(np.isfinite(magnitude_db)):
            logger.warning("compute_transfer_function: NaN/Inf in gain_db, sanitizing")
            magnitude_db = np.where(np.isfinite(magnitude_db), magnitude_db, -200.0)
        if not np.all(np.isfinite(phase_deg)):
            phase_deg = np.where(np.isfinite(phase_deg), phase_deg, 0.0)

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

def compute_bandwidth(
    frequencies: np.ndarray,
    gain_db: np.ndarray,
    threshold_db: float = -3.0,
) -> float:
    """伝達関数のピーク周りの帯域幅を計算する。

    ピークゲインから threshold_db（デフォルト -3 dB）下がった範囲の
    周波数幅を返す。帯域幅が広いほど同調ずれに対してロバスト。

    Parameters
    ----------
    frequencies : array
        周波数 [Hz]
    gain_db : array
        ゲイン [dB]
    threshold_db : float
        ピークからの閾値 [dB]（負値）。デフォルト -3.0。

    Returns
    -------
    float
        帯域幅 [Hz]。算出不能の場合は 0.0。
    """
    if len(frequencies) < 2:
        return 0.0

    peak_val = np.max(gain_db)
    cutoff = peak_val + threshold_db  # threshold_db is negative

    above = gain_db >= cutoff
    if not np.any(above):
        return 0.0

    f_above = frequencies[above]
    return float(f_above[-1] - f_above[0])


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
    bandwidth_hz: float = 0.0
    initial_bandwidth_hz: float = 0.0

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
            "TMD ピーク最小化最適化結果",
            f"  初期ピークゲイン: {self.initial_peak_gain_db:.2f} dB",
            f"  最適化後ピークゲイン: {self.optimized_peak_gain_db:.2f} dB",
            f"  ゲイン低減量: {self.peak_reduction_db:.2f} dB ({reduction_pct:.1f}%)",
            f"  初期ピーク周波数: {self.initial_peak_freq:.2f} Hz",
            f"  最適化後ピーク周波数: {self.optimized_peak_freq:.2f} Hz",
            f"  最適TMD減衰比 (zeta_d): {self.optimal_damping_ratio:.4f}",
            f"  最適同調比 (f_d/f_n): {self.optimal_stiffness_ratio:.4f}",
            f"  評価回数: {self.num_evaluations}",
        ]
        if self.bandwidth_hz > 0:
            lines.append(f"  有効帯域幅 (-3dB): {self.bandwidth_hz:.3f} Hz")
        if self.initial_bandwidth_hz > 0:
            lines.append(f"  初期帯域幅 (-3dB): {self.initial_bandwidth_hz:.3f} Hz")
        return "\n".join(lines)


def sdof_tmd_transfer_function(
    frequencies: np.ndarray,
    f_n: float,
    zeta_s: float,
    mu: float,
    f_ratio: float,
    zeta_d: float,
) -> np.ndarray:
    """SDOF + TMD 系の変位伝達関数 |H(f)| を解析的に計算する。

    主構造（質量 M, 固有振動数 f_n, 減衰比 zeta_s）に
    TMD（質量比 mu = m/M, 同調比 f_ratio = f_d/f_n, 減衰比 zeta_d）を
    付加した 2 自由度系の、調和地動入力に対する主構造変位の
    振幅増幅率を返す。

    Den Hartog の定式化に基づく:
      H(r) = |N(r)| / |D(r)|
    ただし r = omega / omega_n (振動数比)

    Parameters
    ----------
    frequencies : array
        周波数 [Hz]
    f_n : float
        主構造の固有振動数 [Hz]
    zeta_s : float
        主構造の減衰比
    mu : float
        質量比 m_d / M
    f_ratio : float
        同調比 f_d / f_n
    zeta_d : float
        TMD 減衰比

    Returns
    -------
    H : array
        振幅増幅率 |X1 / X_g|（線形スケール）
    """
    if f_n <= 0:
        return np.ones_like(frequencies)

    r = frequencies / f_n  # 振動数比 omega/omega_n

    # 分子: N(r) = (f^2 - r^2) + j*(2*zeta_d*f*r)
    N_real = f_ratio**2 - r**2
    N_imag = 2.0 * zeta_d * f_ratio * r

    # 分母: D(r) = [(1-r^2)(f^2-r^2) - mu*r^2*f^2 - 4*zeta_s*zeta_d*f*r^2]
    #              + j*[2*zeta_s*r*(f^2-r^2) + 2*zeta_d*f*r*(1-r^2-mu*r^2)]
    D_real = (
        (1.0 - r**2) * (f_ratio**2 - r**2)
        - mu * r**2 * f_ratio**2
        - 4.0 * zeta_s * zeta_d * f_ratio * r**2
    )
    D_imag = (
        2.0 * zeta_s * r * (f_ratio**2 - r**2)
        + 2.0 * zeta_d * f_ratio * r * (1.0 - r**2 - mu * r**2)
    )

    N_mag = np.sqrt(N_real**2 + N_imag**2)
    D_mag = np.sqrt(D_real**2 + D_imag**2)

    eps = 1e-30
    H = N_mag / (D_mag + eps)
    # NaN/Inf 防御: 数値不安定時は安全な値に置換
    if not np.all(np.isfinite(H)):
        logger.warning("sdof_tmd_transfer_function: NaN/Inf detected, replacing with 0")
        H = np.where(np.isfinite(H), H, 0.0)
    return H


class TransferFunctionPeakMinimizer:
    """TMD パラメータ最適化による伝達関数ピーク低減。

    解析的 SDOF + TMD 伝達関数モデルを使い、
    TMD の減衰比 (zeta_d) と同調比 (f_ratio = f_d/f_n) を
    最適化してピークゲインを最小化する。

    パラメータの意味:
      - damping_ratio → TMD 減衰比 zeta_d
      - stiffness_ratio → TMD 同調比 f_ratio (= f_d / f_n)
    """

    def __init__(
        self,
        transfer_function: TransferFunctionResult,
        natural_frequency: Optional[float] = None,
        structural_damping: float = 0.02,
        mass_ratio: float = 0.05,
    ) -> None:
        """Initialize minimizer.

        Parameters
        ----------
        transfer_function : TransferFunctionResult
            元の伝達関数（比較・表示用）。
        natural_frequency : float, optional
            主構造の固有振動数 [Hz]。None の場合はピーク周波数を使用。
        structural_damping : float
            主構造の減衰比。デフォルト 0.02（2%）。
        mass_ratio : float
            TMD 質量比 mu = m_d / M。デフォルト 0.05（5%）。
        """
        self.tf = transfer_function
        self.natural_freq = natural_frequency or transfer_function.peak_freq
        self.zeta_s = structural_damping
        self.mu = mass_ratio
        self._eval_count = 0
        self._objective_mode: str = "peak"
        self._bandwidth_weight: float = 0.0

    def _synthesize_damper_response(
        self, damping_ratio: float, stiffness_ratio: float
    ) -> TransferFunctionResult:
        """解析的 SDOF+TMD モデルで制振後の伝達関数を合成する。

        Parameters
        ----------
        damping_ratio : float
            TMD 減衰比 zeta_d。
        stiffness_ratio : float
            TMD 同調比 f_ratio = f_d / f_n。
        """
        f = self.tf.frequencies
        eps = 1e-30

        H = sdof_tmd_transfer_function(
            frequencies=f,
            f_n=self.natural_freq,
            zeta_s=self.zeta_s,
            mu=self.mu,
            f_ratio=stiffness_ratio,
            zeta_d=damping_ratio,
        )

        gain_db = 20.0 * np.log10(H + eps)

        # 位相計算（SDOF+TMD）
        r = f / self.natural_freq if self.natural_freq > 0 else f + eps
        N_real = stiffness_ratio**2 - r**2
        N_imag = 2.0 * damping_ratio * stiffness_ratio * r
        D_real = (
            (1.0 - r**2) * (stiffness_ratio**2 - r**2)
            - self.mu * r**2 * stiffness_ratio**2
            - 4.0 * self.zeta_s * damping_ratio * stiffness_ratio * r**2
        )
        D_imag = (
            2.0 * self.zeta_s * r * (stiffness_ratio**2 - r**2)
            + 2.0 * damping_ratio * stiffness_ratio * r
            * (1.0 - r**2 - self.mu * r**2)
        )
        H_complex = (N_real + 1j * N_imag) / (D_real + 1j * D_imag + eps)
        phase_deg = np.degrees(np.angle(H_complex))

        peak_idx = int(np.argmax(gain_db))

        return TransferFunctionResult(
            frequencies=f.copy(),
            gain_db=gain_db,
            phase_deg=phase_deg,
            coherence=self.tf.coherence.copy() if self.tf.coherence is not None else None,
            input_label=self.tf.input_label,
            output_label=self.tf.output_label + " (TMD付)",
            peak_freq=float(f[peak_idx]),
            peak_gain_db=float(gain_db[peak_idx]),
        )

    def _evaluate_objective(self, damping_ratio: float, stiffness_ratio: float) -> float:
        """Evaluate objective function.

        Supports three modes via self._objective_mode:
        - "peak": minimize peak gain (dB)
        - "bandwidth": minimize peak gain penalized by narrow bandwidth
        - "robust": weighted sum of peak gain and bandwidth penalty
        """
        self._eval_count += 1

        if not (0.001 <= damping_ratio <= 0.5):
            return 1000.0
        if not (0.01 <= stiffness_ratio <= 2.0):
            return 1000.0

        tf_damped = self._synthesize_damper_response(damping_ratio, stiffness_ratio)

        if self._objective_mode == "peak" or self._bandwidth_weight <= 0:
            return tf_damped.peak_gain_db

        # Compute bandwidth penalty: narrower bandwidth → larger penalty
        bw = compute_bandwidth(tf_damped.frequencies, tf_damped.gain_db)
        # Normalize bandwidth by natural frequency to get a dimensionless measure
        bw_norm = bw / self.natural_freq if self.natural_freq > 0 else bw
        # Penalty: invert bandwidth (wider is better → lower penalty)
        bw_penalty = -bw_norm  # negative = wider is rewarded

        return tf_damped.peak_gain_db + self._bandwidth_weight * bw_penalty

    def optimize(
        self,
        damping_range: Tuple[float, float] = (0.01, 0.30),
        stiffness_range: Tuple[float, float] = (0.5, 1.5),
        method: str = "grid",
        grid_points: int = 20,
        objective: str = "peak",
        bandwidth_weight: float = 5.0,
    ) -> PeakMinimizationResult:
        """TMD パラメータ最適化を実行する。

        Parameters
        ----------
        damping_range : tuple
            TMD 減衰比 zeta_d の探索範囲。
        stiffness_range : tuple
            TMD 同調比 f_ratio = f_d/f_n の探索範囲。
            デフォルト (0.5, 1.5)。最適値は通常 1/(1+μ) 付近。
        method : str
            "grid" (グリッドサーチ) or "simplex" (L-BFGS-B)。
        grid_points : int
            グリッドサーチの各軸の分割数。
        objective : str
            目的関数の種類:
            - "peak": ピークゲイン最小化（従来動作）
            - "robust": ピーク最小化 + 帯域幅最大化の重み付き和
        bandwidth_weight : float
            objective="robust" 時の帯域幅項の重み。デフォルト 5.0。
            大きいほど帯域幅（ロバスト性）を重視する。
        """
        self._eval_count = 0
        self._objective_mode = objective
        self._bandwidth_weight = bandwidth_weight if objective == "robust" else 0.0
        initial_peak = self.tf.peak_gain_db
        initial_freq = self.tf.peak_freq
        initial_bw = compute_bandwidth(self.tf.frequencies, self.tf.gain_db)

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
        opt_bw = compute_bandwidth(tf_opt.frequencies, tf_opt.gain_db)

        return PeakMinimizationResult(
            optimal_damping_ratio=result.optimal_damping_ratio,
            optimal_stiffness_ratio=result.optimal_stiffness_ratio,
            initial_peak_gain_db=initial_peak,
            optimized_peak_gain_db=result.optimized_peak_gain_db,
            peak_reduction_db=peak_reduction,
            initial_peak_freq=initial_freq,
            optimized_peak_freq=tf_opt.peak_freq,
            num_evaluations=self._eval_count,
            bandwidth_hz=opt_bw,
            initial_bandwidth_hz=initial_bw,
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


# ---------------------------------------------------------------------------
# SNAP 伝達関数パイプライン
# ---------------------------------------------------------------------------


def compute_transfer_function_from_time_histories(
    input_time_history: np.ndarray,
    output_time_history: np.ndarray,
    dt: float,
    freq_range: Optional[Tuple[float, float]] = None,
    input_label: str = "地動入力",
    output_label: str = "応答",
) -> TransferFunctionResult:
    """時刻歴データのペアから伝達関数を計算する。

    SNAP 解析結果の入力波形（地動加速度）と応答波形（層間変位・加速度等）から
    Welch 法ベースの伝達関数を算出する。

    Parameters
    ----------
    input_time_history : array
        入力時刻歴（地動加速度など）
    output_time_history : array
        出力時刻歴（応答加速度・変位など）
    dt : float
        時刻刻み [秒]
    freq_range : tuple, optional
        表示周波数範囲 (f_min, f_max) [Hz]
    input_label, output_label : str
        ラベル

    Returns
    -------
    TransferFunctionResult
    """
    svc = TransferFunctionService(dt=dt)
    return svc.compute_transfer_function(
        input_signal=input_time_history,
        output_signal=output_time_history,
        input_label=input_label,
        output_label=output_label,
        freq_range=freq_range,
    )


def compute_snap_transfer_function(
    result_loader: "SnapResultLoader",
    input_category: str = "Floor",
    input_record: int = 0,
    input_field: int = 0,
    output_category: str = "Floor",
    output_record: int = -1,
    output_field: int = 0,
    freq_range: Optional[Tuple[float, float]] = None,
) -> Optional[TransferFunctionResult]:
    """SnapResultLoader から伝達関数を計算する。

    入力・出力それぞれのカテゴリ・レコード・フィールドを指定し、
    Welch 法で伝達関数 H(f) = Y(f)/X(f) を計算する。

    典型的な使い方:
    - 入力: Floor[0] (1F = 地動入力相当) の加速度
    - 出力: Floor[-1] (最上階) の加速度
    → 建物の加速度増幅率の周波数応答

    Parameters
    ----------
    result_loader : SnapResultLoader
        SNAP 解析結果ローダー
    input_category, output_category : str
        入出力のカテゴリ (Floor, Story, Damper 等)
    input_record, output_record : int
        レコード番号。-1 は最終レコード（最上階）。
    input_field, output_field : int
        フィールド番号（成分インデックス）
    freq_range : tuple, optional
        表示周波数範囲

    Returns
    -------
    TransferFunctionResult or None
        計算に失敗した場合は None。
    """
    try:
        # 入力側
        bc_in = result_loader.get(input_category)
        if not bc_in or not bc_in.hst or not bc_in.hst.header:
            return None
        hst_in = bc_in.hst
        hst_in.ensure_loaded()
        h_in = hst_in.header

        rec_in = input_record
        if rec_in < 0:
            rec_in = h_in.num_records + rec_in
        x = hst_in.time_series(rec_in, input_field)

        # 出力側
        bc_out = result_loader.get(output_category)
        if not bc_out or not bc_out.hst or not bc_out.hst.header:
            return None
        hst_out = bc_out.hst
        hst_out.ensure_loaded()
        h_out = hst_out.header

        rec_out = output_record
        if rec_out < 0:
            rec_out = h_out.num_records + rec_out
        y = hst_out.time_series(rec_out, output_field)

        if len(x) < 2 or len(y) < 2:
            return None

        # 短い方に合わせる
        n = min(len(x), len(y))
        x = x[:n]
        y = y[:n]

        dt = hst_in.dt

        in_name = bc_in.record_name(rec_in)
        out_name = bc_out.record_name(rec_out)
        in_label = f"{input_category}/{in_name}"
        out_label = f"{output_category}/{out_name}"

        return compute_transfer_function_from_time_histories(
            input_time_history=x,
            output_time_history=y,
            dt=dt,
            freq_range=freq_range,
            input_label=in_label,
            output_label=out_label,
        )
    except Exception:
        logger.warning("compute_snap_transfer_function: failed", exc_info=True)
        return None
