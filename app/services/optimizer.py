"""
app/services/optimizer.py
ダンパー最適化エンジン。

指定した応答値（目的関数）を最小化する最適なダンパーパラメータを
自動探索するサービスクラスです。

探索手法:
  - グリッドサーチ（全パラメータの直積）
  - ランダムサーチ（モンテカルロ）
  - ラテン超方格サンプリング（LHS: 空間充填サンプリング）
  - ベイズ最適化（ガウス過程回帰 + 獲得関数による効率的探索）
  - 遺伝的アルゴリズム（GA）（BLX-α交叉 + ガウシアン突然変異 + エリート保存）
  - 焼きなまし法（SA）（指数冷却 + メトロポリス基準）
  - NSGA-II（多目的最適化: 非優越ソート + クラウディング距離）

使い方:
  1. OptimizationConfig で目的関数・制約・探索範囲を設定
  2. DamperOptimizer.optimize() を呼び出して探索を実行
  3. OptimizationResult から最適解を取得
"""

from __future__ import annotations

import concurrent.futures
import csv
import itertools
import json
import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

from PySide6.QtCore import QObject, QThread, Signal

from app.models import AnalysisCase, AnalysisCaseStatus
from app.models.performance_criteria import PerformanceCriteria


# ---------------------------------------------------------------------------
# ガウス過程回帰 (Gaussian Process Regression) — ベイズ最適化の代理モデル
# ---------------------------------------------------------------------------

class _GaussianProcessRegressor:
    """
    シンプルなガウス過程回帰。

    RBF (Radial Basis Function) カーネルを使用し、
    ハイパーパラメータは最尤推定で自動調整します。
    外部ライブラリ不要で numpy のみに依存します。

    Parameters
    ----------
    length_scale : float
        RBF カーネルの長さスケール初期値。
    noise : float
        観測ノイズの分散。
    """

    def __init__(self, length_scale: float = 1.0, noise: float = 1e-6) -> None:
        self._length_scale = length_scale
        self._noise = noise
        self._X: Optional[np.ndarray] = None  # (n, d)
        self._y: Optional[np.ndarray] = None  # (n,)
        self._K_inv: Optional[np.ndarray] = None
        self._alpha: Optional[np.ndarray] = None  # K_inv @ y

    def _rbf_kernel(self, X1: np.ndarray, X2: np.ndarray) -> np.ndarray:
        """RBF (ガウス) カーネル行列を計算します。"""
        # ||x1 - x2||^2 の計算
        sq1 = np.sum(X1 ** 2, axis=1, keepdims=True)
        sq2 = np.sum(X2 ** 2, axis=1, keepdims=True)
        dist_sq = sq1 + sq2.T - 2.0 * X1 @ X2.T
        dist_sq = np.maximum(dist_sq, 0.0)
        return np.exp(-0.5 * dist_sq / (self._length_scale ** 2))

    def fit(self, X: np.ndarray, y: np.ndarray) -> None:
        """
        訓練データでモデルを学習します。

        Parameters
        ----------
        X : ndarray, shape (n, d)
            入力特徴量。
        y : ndarray, shape (n,)
            目的関数値。
        """
        self._X = X.copy()
        self._y = y.copy()

        # 長さスケールの簡易調整: データの標準偏差に基づく
        if X.shape[0] > 1:
            std = np.std(X, axis=0)
            std = std[std > 0]
            if len(std) > 0:
                self._length_scale = float(np.median(std))

        K = self._rbf_kernel(X, X) + self._noise * np.eye(len(X))
        # 安定性のために正則化
        K += 1e-8 * np.eye(len(X))

        try:
            L = np.linalg.cholesky(K)
            self._alpha = np.linalg.solve(L.T, np.linalg.solve(L, y))
            L_inv = np.linalg.solve(L, np.eye(len(X)))
            self._K_inv = L_inv.T @ L_inv
        except np.linalg.LinAlgError:
            # コレスキー分解失敗時はフォールバック（正則化を強化）
            logger.debug("GP: Cholesky failed, falling back to regularized inverse")
            self._K_inv = np.linalg.inv(K + 0.01 * np.eye(len(X)))
            self._alpha = self._K_inv @ y

    def predict(self, X_new: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        予測と不確実性を返します。

        Parameters
        ----------
        X_new : ndarray, shape (m, d)
            予測点。

        Returns
        -------
        mean : ndarray, shape (m,)
            予測平均値。
        std : ndarray, shape (m,)
            予測標準偏差（不確実性）。
        """
        if self._X is None or self._alpha is None:
            return np.zeros(len(X_new)), np.ones(len(X_new))

        K_star = self._rbf_kernel(X_new, self._X)
        mean = K_star @ self._alpha

        K_ss = self._rbf_kernel(X_new, X_new)
        var = np.diag(K_ss) - np.sum(K_star @ self._K_inv * K_star, axis=1)
        var = np.maximum(var, 1e-10)
        std = np.sqrt(var)

        return mean, std


def _expected_improvement(
    mu: np.ndarray, sigma: np.ndarray, y_best: float, xi: float = 0.01
) -> np.ndarray:
    """
    Expected Improvement (EI) 獲得関数。

    最小化問題なので y_best は現在の最良値（最小値）。

    Parameters
    ----------
    mu : ndarray
        予測平均。
    sigma : ndarray
        予測標準偏差。
    y_best : float
        現在の最良目的関数値。
    xi : float
        探索と利用のトレードオフパラメータ。

    Returns
    -------
    ei : ndarray
        各点の Expected Improvement 値。
    """
    from scipy.stats import norm  # type: ignore

    with np.errstate(divide="ignore", invalid="ignore"):
        improvement = y_best - mu - xi
        Z = improvement / sigma
        ei = improvement * norm.cdf(Z) + sigma * norm.pdf(Z)
        # sigma が 0 の場合は EI = 0
        ei = np.where(sigma > 1e-10, ei, 0.0)
    return ei


def _expected_improvement_no_scipy(
    mu: np.ndarray, sigma: np.ndarray, y_best: float, xi: float = 0.01
) -> np.ndarray:
    """
    scipy なしの EI 実装（標準正規分布の近似を使用）。
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        improvement = y_best - mu - xi
        Z = np.where(sigma > 1e-10, improvement / sigma, 0.0)
        # 標準正規分布の CDF 近似 (Abramowitz & Stegun)
        cdf_Z = 0.5 * (1.0 + np.vectorize(math.erf)(Z / math.sqrt(2.0)))
        # 標準正規分布の PDF
        pdf_Z = np.exp(-0.5 * Z ** 2) / math.sqrt(2.0 * math.pi)
        ei = improvement * cdf_Z + sigma * pdf_Z
        ei = np.where(sigma > 1e-10, ei, 0.0)
    return ei


def _probability_of_improvement(
    mu: np.ndarray, sigma: np.ndarray, y_best: float, xi: float = 0.01
) -> np.ndarray:
    """
    Probability of Improvement (PI) 獲得関数。

    現在の最良値 y_best を xi だけ改善する確率を計算します。
    EI より保守的（利用寄り）な探索を行います。

    Parameters
    ----------
    mu : ndarray
        予測平均。
    sigma : ndarray
        予測標準偏差。
    y_best : float
        現在の最良目的関数値。
    xi : float
        改善閾値パラメータ。大きいほど探索寄り。

    Returns
    -------
    pi : ndarray
        各点の改善確率。
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        Z = np.where(sigma > 1e-10, (y_best - mu - xi) / sigma, 0.0)
        # CDF 計算
        cdf_Z = 0.5 * (1.0 + np.vectorize(math.erf)(Z / math.sqrt(2.0)))
        pi = np.where(sigma > 1e-10, cdf_Z, 0.0)
    return pi


def _upper_confidence_bound(
    mu: np.ndarray, sigma: np.ndarray, y_best: float, kappa: float = 2.0
) -> np.ndarray:
    """
    Upper Confidence Bound (UCB) 獲得関数（最小化版: LCB）。

    mu - kappa * sigma を最小化 → 負値を返して argmax で使えるようにする。
    kappa が大きいほど探索（不確実な領域の探索）を重視。

    Parameters
    ----------
    mu : ndarray
        予測平均。
    sigma : ndarray
        予測標準偏差。
    y_best : float
        現在の最良目的関数値（未使用だがインターフェース統一のため）。
    kappa : float
        探索・利用バランスパラメータ。推奨: 1.0〜3.0。

    Returns
    -------
    lcb_neg : ndarray
        負の LCB 値（argmax で使用するため符号反転）。
    """
    # LCB = mu - kappa * sigma を最小化したい
    # argmax で使うため -LCB = -mu + kappa * sigma を返す
    return -mu + kappa * sigma


def _compute_acquisition(
    acq_func: str,
    mu: np.ndarray,
    sigma: np.ndarray,
    y_best: float,
    xi: float = 0.01,
    kappa: float = 2.0,
) -> np.ndarray:
    """獲得関数を選択して評価する統合関数。

    Parameters
    ----------
    acq_func : str
        獲得関数名。"ei", "pi", "ucb" のいずれか。
    mu, sigma : ndarray
        GP 予測の平均と標準偏差。
    y_best : float
        現在の最良目的関数値。
    xi : float
        EI/PI の探索パラメータ。
    kappa : float
        UCB の探索パラメータ。

    Returns
    -------
    values : ndarray
        各候補点の獲得関数値（大きいほど良い）。
    """
    if acq_func == "pi":
        return _probability_of_improvement(mu, sigma, y_best, xi=xi)
    elif acq_func == "ucb":
        return _upper_confidence_bound(mu, sigma, y_best, kappa=kappa)
    else:  # "ei" (default)
        try:
            return _expected_improvement(mu, sigma, y_best, xi=xi)
        except Exception:
            return _expected_improvement_no_scipy(mu, sigma, y_best, xi=xi)


# ---------------------------------------------------------------------------
# 最適化設定
# ---------------------------------------------------------------------------

@dataclass
class ParameterRange:
    """
    探索するパラメータの範囲定義。

    Attributes
    ----------
    key : str
        パラメータキー（例: "Cd", "alpha", "Qd"）。
    label : str
        表示名。
    min_val : float
        最小値。
    max_val : float
        最大値。
    step : float
        刻み幅（グリッドサーチ用、0の場合連続値）。
    is_integer : bool
        整数パラメータかどうか。
    """
    key: str = ""
    label: str = ""
    min_val: float = 0.0
    max_val: float = 1.0
    step: float = 0.0
    is_integer: bool = False

    def discrete_values(self, max_points: int = 100) -> List[float]:
        """離散化した値のリストを生成します。"""
        if self.step > 0:
            n = int(math.floor((self.max_val - self.min_val) / self.step + 0.5)) + 1
            n = min(n, max_points)
            values = [
                round(self.min_val + i * self.step, 10)
                for i in range(n)
                if self.min_val + i * self.step <= self.max_val + self.step * 0.01
            ]
        else:
            # 連続値を等分割
            n = min(20, max_points)
            values = [
                round(self.min_val + i * (self.max_val - self.min_val) / (n - 1), 10)
                for i in range(n)
            ]
        if self.is_integer:
            values = list(sorted(set(int(v) for v in values)))
        return values[:max_points]

    def random_value(self) -> float:
        """範囲内のランダムな値を返します。"""
        val = random.uniform(self.min_val, self.max_val)
        if self.is_integer:
            val = round(val)
        elif self.step > 0:
            val = round(val / self.step) * self.step
        return val

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key, "label": self.label,
            "min_val": self.min_val, "max_val": self.max_val,
            "step": self.step, "is_integer": self.is_integer,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ParameterRange":
        return cls(
            key=d["key"], label=d.get("label", d["key"]),
            min_val=d["min_val"], max_val=d["max_val"],
            step=d.get("step", 0.0), is_integer=d.get("is_integer", False),
        )


@dataclass
class OptimizationConfig:
    """
    最適化の設定。

    Attributes
    ----------
    objective_key : str
        最小化する応答値のキー（例: "max_drift"）。
    objective_label : str
        目的関数の日本語ラベル。
    parameters : list of ParameterRange
        探索するパラメータ範囲のリスト。
    constraints : dict
        制約条件。{response_key: max_allowed_value}
    method : str
        探索手法（"grid", "random", "bayesian"）。
    max_iterations : int
        最大反復数（random/bayesianの場合）。
    criteria : PerformanceCriteria, optional
        目標性能基準（制約として使用）。
    damper_type : str
        探索対象のダンパー種類名。
    base_case : AnalysisCase, optional
        ベースとなる解析ケース。
    """
    objective_key: str = "max_drift"
    objective_label: str = "最大層間変形角"
    parameters: List[ParameterRange] = field(default_factory=list)
    constraints: Dict[str, float] = field(default_factory=dict)
    method: str = "grid"
    max_iterations: int = 100
    criteria: Optional[PerformanceCriteria] = None
    damper_type: str = ""
    base_case: Optional[AnalysisCase] = None
    objective_weights: Dict[str, float] = field(default_factory=dict)
    warm_start_candidates: List["OptimizationCandidate"] = field(default_factory=list)
    """前回の最適化結果から引き継ぐ候補リスト（ウォームスタート用）。"""
    constraint_penalty_weight: float = 0.0
    """制約ペナルティ重み。0の場合は従来のハード制約。正の値でペナルティ法を使用。
    ペナルティ = weight × Σ max(0, -margin_i) で目的関数に加算される。
    構造設計では 10.0〜100.0 程度が有効。"""
    n_parallel: int = 1
    """並列評価数。1の場合は逐次評価（デフォルト）。
    2以上の場合、グリッドサーチ/ランダムサーチで ThreadPoolExecutor を使用して
    複数候補を同時にSNAP実行する。SNAP解析では4〜8が目安。"""
    checkpoint_interval: int = 10
    """チェックポイント保存間隔（評価回数）。この回数ごとに中間結果を自動保存する。
    0の場合はチェックポイントを無効化。デフォルト10。"""
    checkpoint_path: str = ""
    """チェックポイントファイルパス。空の場合はチェックポイントを保存しない。"""
    robustness_samples: int = 0
    """ロバスト最適化のサンプル数。0の場合は通常の最適化。
    正の値の場合、各候補を中心値+N個の摂動パラメータで評価し、
    最悪ケース（max）を目的関数値として採用する。
    製造誤差やモデル不確実性に対する頑健な設計に有用。"""
    robustness_delta: float = 0.05
    """ロバスト最適化のパラメータ摂動幅（比率）。デフォルト5%。
    各パラメータを [val*(1-delta), val*(1+delta)] の範囲で摂動させる。"""
    cost_coefficients: Dict[str, float] = field(default_factory=dict)
    """コスト係数。{param_key: cost_per_unit} の形式で指定。
    例: {"Cd": 0.5, "Qd": 0.01} → コスト = 0.5*Cd + 0.01*Qd
    空の場合はコスト項なし（従来動作）。"""
    cost_weight: float = 0.0
    """コスト重み。目的関数 = response_obj + cost_weight × コスト。
    0の場合はコスト項なし。構造設計では 0.001〜0.1 程度で応答とコストのバランスを調整。"""
    envelope_mode: str = ""
    """多波エンベロープの集約モード。"max"=最大値（保守側）, "mean"=平均値。
    空文字の場合は単一波（従来動作）。"""
    envelope_wave_names: List[str] = field(default_factory=list)
    """多波エンベロープ最適化で使用する波形名リスト。"""
    acquisition_function: str = "ei"
    """ベイズ最適化の獲得関数。"ei"=Expected Improvement, "pi"=Probability of Improvement,
    "ucb"=Upper Confidence Bound (LCB)。デフォルト "ei"。
    EI: 探索と利用のバランスが良い汎用的な選択。
    PI: 利用寄りで収束が速いが局所解に陥りやすい。
    UCB: kappa で探索度合いを直接制御でき、高次元で有効。"""
    acquisition_kappa: float = 2.0
    """UCB 獲得関数の探索パラメータ κ。大きいほど不確実な領域を重視。
    推奨: 1.0（利用寄り）〜 3.0（探索寄り）。デフォルト 2.0。"""
    ga_adaptive_mutation: bool = False
    """GA で適応的突然変異率を使用するか。True の場合、世代が進むにつれて
    突然変異率を線形減衰させ、序盤は探索・終盤は利用を重視する。
    交叉率も逆方向に増加させて終盤の局所精錬を促進する。"""
    random_seed: Optional[int] = None
    """乱数シード。整数を指定すると全確率的手法で再現性のある結果を得られる。
    None の場合は毎回異なるランダムシードを使用（デフォルト）。
    構造設計のレビューや結果の再現性確認に有用。"""

    def compute_objective(self, response: Dict[str, float], params: Optional[Dict[str, float]] = None) -> float:
        """応答値辞書から目的関数値を計算する。

        objective_weights が空の場合は単一目的（objective_key）、
        設定されている場合は重み付き和を返す。
        cost_coefficients と cost_weight が設定されている場合はコスト項を加算。
        """
        if self.objective_weights:
            total = 0.0
            for key, weight in self.objective_weights.items():
                val = response.get(key, float("inf"))
                if val == float("inf"):
                    return float("inf")
                total += weight * val
            obj = total
        else:
            obj = response.get(self.objective_key, float("inf"))

        if obj == float("inf"):
            return obj

        # コスト項を加算
        if self.cost_weight > 0 and self.cost_coefficients and params:
            cost = sum(
                coeff * params.get(key, 0.0)
                for key, coeff in self.cost_coefficients.items()
            )
            obj += self.cost_weight * cost

        return obj

    def to_dict(self) -> Dict[str, Any]:
        return {
            "objective_key": self.objective_key,
            "objective_label": self.objective_label,
            "parameters": [p.to_dict() for p in self.parameters],
            "constraints": dict(self.constraints),
            "method": self.method,
            "max_iterations": self.max_iterations,
            "damper_type": self.damper_type,
            "objective_weights": dict(self.objective_weights),
            "constraint_penalty_weight": self.constraint_penalty_weight,
            "n_parallel": self.n_parallel,
            "checkpoint_interval": self.checkpoint_interval,
            "robustness_samples": self.robustness_samples,
            "robustness_delta": self.robustness_delta,
            "cost_coefficients": dict(self.cost_coefficients),
            "cost_weight": self.cost_weight,
            "envelope_mode": self.envelope_mode,
            "envelope_wave_names": list(self.envelope_wave_names),
            "acquisition_function": self.acquisition_function,
            "acquisition_kappa": self.acquisition_kappa,
            "ga_adaptive_mutation": self.ga_adaptive_mutation,
            "random_seed": self.random_seed,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OptimizationConfig":
        return cls(
            objective_key=d.get("objective_key", "max_drift"),
            objective_label=d.get("objective_label", ""),
            parameters=[ParameterRange.from_dict(p) for p in d.get("parameters", [])],
            constraints=d.get("constraints", {}),
            method=d.get("method", "grid"),
            max_iterations=d.get("max_iterations", 100),
            damper_type=d.get("damper_type", ""),
            objective_weights=d.get("objective_weights", {}),
            constraint_penalty_weight=d.get("constraint_penalty_weight", 0.0),
            n_parallel=d.get("n_parallel", 1),
            checkpoint_interval=d.get("checkpoint_interval", 10),
            robustness_samples=d.get("robustness_samples", 0),
            robustness_delta=d.get("robustness_delta", 0.05),
            cost_coefficients=d.get("cost_coefficients", {}),
            cost_weight=d.get("cost_weight", 0.0),
            envelope_mode=d.get("envelope_mode", ""),
            envelope_wave_names=d.get("envelope_wave_names", []),
            acquisition_function=d.get("acquisition_function", "ei"),
            acquisition_kappa=d.get("acquisition_kappa", 2.0),
            ga_adaptive_mutation=d.get("ga_adaptive_mutation", False),
            random_seed=d.get("random_seed"),
        )


# ---------------------------------------------------------------------------
# 最適化結果
# ---------------------------------------------------------------------------

@dataclass
class OptimizationCandidate:
    """1つの探索候補とその評価結果。"""
    params: Dict[str, float] = field(default_factory=dict)
    objective_value: float = float("inf")
    response_values: Dict[str, float] = field(default_factory=dict)
    is_feasible: bool = True  # 制約を満たすか
    iteration: int = 0
    constraint_margins: Dict[str, float] = field(default_factory=dict)
    """各制約のマージン（負=違反量, 正=余裕量）。例: {"max_drift": -0.002} は制約超過を示す。"""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "params": dict(self.params),
            "objective_value": self.objective_value,
            "response_values": dict(self.response_values),
            "is_feasible": self.is_feasible,
            "iteration": self.iteration,
            "constraint_margins": dict(self.constraint_margins),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OptimizationCandidate":
        return cls(
            params=d.get("params", {}),
            objective_value=d.get("objective_value", float("inf")),
            response_values=d.get("response_values", {}),
            is_feasible=d.get("is_feasible", True),
            iteration=d.get("iteration", 0),
            constraint_margins=d.get("constraint_margins", {}),
        )


@dataclass
class OptimizationResult:
    """
    最適化の最終結果。

    Attributes
    ----------
    best : OptimizationCandidate or None
        最良解。
    all_candidates : list of OptimizationCandidate
        全候補の評価結果。
    config : OptimizationConfig
        使用した設定。
    elapsed_sec : float
        計算時間（秒）。
    converged : bool
        収束したかどうか。
    message : str
        結果メッセージ。
    """
    best: Optional[OptimizationCandidate] = None
    all_candidates: List[OptimizationCandidate] = field(default_factory=list)
    config: Optional[OptimizationConfig] = None
    elapsed_sec: float = 0.0
    converged: bool = False
    message: str = ""
    evaluation_method: str = "mock"  # "mock" or "snap"
    evaluator_stats: Optional[Dict[str, int]] = None  # SNAP評価統計
    robustness_stats: Optional[Dict[str, int]] = None  # ロバスト摂動統計

    @property
    def feasible_candidates(self) -> List[OptimizationCandidate]:
        """制約を満たす候補のみ。"""
        return [c for c in self.all_candidates if c.is_feasible]

    @property
    def ranked_candidates(self) -> List[OptimizationCandidate]:
        """目的関数値でソートされた候補リスト（制約満足のみ）。"""
        feasible = self.feasible_candidates
        return sorted(feasible, key=lambda c: c.objective_value)

    @property
    def least_infeasible(self) -> Optional[OptimizationCandidate]:
        """制約違反候補の中で最も目的関数値が良い候補。

        全候補が制約違反の場合に「最も惜しい解」を設計者に提示するのに使用。
        制約マージンの最小違反量でソート（違反が少ない順）。
        """
        infeasible = [c for c in self.all_candidates if not c.is_feasible]
        if not infeasible:
            return None
        # 目的関数値が良い順にソート（inf除外を優先）
        return min(infeasible, key=lambda c: c.objective_value)

    @property
    def all_ranked_candidates(self) -> List[OptimizationCandidate]:
        """全候補を制約満足優先・目的関数値順でソート。

        制約を満たす候補が先、満たさない候補が後に並びます。
        設計者が検索空間全体を把握するのに有用です。
        """
        feasible = sorted(
            [c for c in self.all_candidates if c.is_feasible],
            key=lambda c: c.objective_value,
        )
        infeasible = sorted(
            [c for c in self.all_candidates if not c.is_feasible],
            key=lambda c: c.objective_value,
        )
        return feasible + infeasible

    def get_summary_text(self) -> str:
        """結果のテキストサマリーを返します。"""
        lines = ["=" * 50]
        lines.append("ダンパー最適化 結果サマリー")
        lines.append("=" * 50)

        if self.config:
            lines.append(f"目的関数: {self.config.objective_label} を最小化")
            lines.append(f"探索手法: {self.config.method}")
            lines.append(f"ダンパー種類: {self.config.damper_type or '未指定'}")

        eval_label = "SNAP実解析" if self.evaluation_method == "snap" else "モック評価（デモ用）"
        lines.append(f"評価方式: {eval_label}")
        if self.config and self.config.method == "bayesian":
            acq_labels = {"ei": "Expected Improvement", "pi": "Probability of Improvement", "ucb": "Upper Confidence Bound"}
            acq_name = acq_labels.get(self.config.acquisition_function, self.config.acquisition_function)
            acq_info = f"獲得関数: {acq_name}"
            if self.config.acquisition_function == "ucb":
                acq_info += f" (κ={self.config.acquisition_kappa:.1f})"
            lines.append(acq_info)
        if self.config and self.config.method == "ga" and self.config.ga_adaptive_mutation:
            lines.append("GA適応的突然変異: 有効（世代進行に応じてレート減衰）")
        if self.config and self.config.constraint_penalty_weight > 0:
            lines.append(f"制約ペナルティ重み: {self.config.constraint_penalty_weight:.1f}")
        if self.config and self.config.robustness_samples > 0:
            lines.append(
                f"ロバスト最適化: {self.config.robustness_samples}サンプル, "
                f"摂動幅 ±{self.config.robustness_delta*100:.0f}%"
            )
        if self.config and self.config.cost_weight > 0:
            lines.append(
                f"コスト重み: {self.config.cost_weight:.4g} "
                f"(係数: {self.config.cost_coefficients})"
            )
        if self.config and self.config.random_seed is not None:
            lines.append(f"乱数シード: {self.config.random_seed}")
        if self.config and self.config.envelope_mode:
            lines.append(
                f"多波エンベロープ: {self.config.envelope_mode} "
                f"({len(self.config.envelope_wave_names)}波: "
                f"{', '.join(self.config.envelope_wave_names[:5])}"
                f"{'...' if len(self.config.envelope_wave_names) > 5 else ''})"
            )
        lines.append(f"計算時間: {self.elapsed_sec:.2f} sec")
        lines.append(f"評価数: {len(self.all_candidates)}")
        lines.append(f"制約満足数: {len(self.feasible_candidates)}")

        if self.evaluator_stats:
            s = self.evaluator_stats
            lines.append(
                f"SNAP統計: 成功 {s.get('success', 0)}, "
                f"エラー {s.get('error', 0)}, "
                f"キャッシュヒット {s.get('cache_hits', 0)}"
            )

        if self.robustness_stats:
            rs = self.robustness_stats
            rate = rs.get("success_rate", 1.0) * 100
            lines.append(
                f"ロバスト摂動: 成功 {rs.get('success', 0)}/{rs.get('total', 0)} "
                f"(成功率 {rate:.0f}%)"
            )
            if rate < 80:
                lines.append(
                    "⚠ ロバスト摂動の成功率が低いため、結果の信頼性が低い可能性があります"
                )

        if self.best:
            lines.append("")
            lines.append("--- 最良解 ---")
            lines.append(f"目的関数値: {self.best.objective_value:.6g}")
            lines.append("パラメータ:")
            for k, v in self.best.params.items():
                lines.append(f"  {k} = {v}")
            lines.append("応答値:")
            for k, v in self.best.response_values.items():
                lines.append(f"  {k} = {v:.6g}")
        else:
            lines.append("")
            lines.append("最適解が見つかりませんでした。")

        lines.append(f"\nメッセージ: {self.message}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "best": self.best.to_dict() if self.best else None,
            "all_candidates": [c.to_dict() for c in self.all_candidates],
            "config": self.config.to_dict() if self.config else None,
            "elapsed_sec": self.elapsed_sec,
            "converged": self.converged,
            "message": self.message,
            "evaluation_method": self.evaluation_method,
            "evaluator_stats": self.evaluator_stats,
            "robustness_stats": self.robustness_stats,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "OptimizationResult":
        best_d = d.get("best")
        config_d = d.get("config")
        return cls(
            best=OptimizationCandidate.from_dict(best_d) if best_d else None,
            all_candidates=[
                OptimizationCandidate.from_dict(c)
                for c in d.get("all_candidates", [])
            ],
            config=OptimizationConfig.from_dict(config_d) if config_d else None,
            elapsed_sec=d.get("elapsed_sec", 0.0),
            converged=d.get("converged", False),
            message=d.get("message", ""),
            evaluation_method=d.get("evaluation_method", "mock"),
            evaluator_stats=d.get("evaluator_stats"),
            robustness_stats=d.get("robustness_stats"),
        )

    def save_json(self, path: str) -> None:
        """最適化結果をJSONファイルに保存します。"""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load_json(cls, path: str) -> "OptimizationResult":
        """JSONファイルから最適化結果を読み込みます。"""
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)


# ---------------------------------------------------------------------------
# 評価関数（モックベース — 実際にはSNAP実行で置き換え）
# ---------------------------------------------------------------------------

def _mock_evaluate(
    params: Dict[str, float],
    base_summary: Dict[str, Any],
    objective_key: str,
) -> Dict[str, float]:
    """
    パラメータに基づくモック評価。

    実際にはSNAP解析を実行して結果を取得しますが、
    デモ用にパラメータから応答値を推定する簡易モデルを使用します。

    これは以下の簡易モデルに基づいています:
    - 減衰係数Cdが大きいほど変位・変形角は小さくなる（対数的）
    - 降伏荷重Qdが大きいほどせん断力係数は大きくなるが変位は小さくなる
    - 速度指数alphaは中間値（0.3-0.5）で最適
    """
    result = {}

    # ベース値を取得
    base_drift = base_summary.get("max_drift", 0.005)
    base_acc = base_summary.get("max_acc", 3.0)
    base_disp = base_summary.get("max_disp", 0.05)
    base_vel = base_summary.get("max_vel", 0.3)
    base_shear = base_summary.get("shear_coeff", 0.15)
    base_otm = base_summary.get("max_otm", 5000.0)

    # パラメータの影響を簡易モデルで計算
    cd = params.get("Cd", params.get("Ce", 300.0))
    alpha = params.get("alpha", params.get("α", 0.4))
    qd = params.get("Qd", params.get("Qy", params.get("Fy", 200.0)))
    k = params.get("K", params.get("K1", params.get("Kd", 50000.0)))

    # 減衰効果係数（Cdの影響）
    cd_ref = 300.0
    damping_effect = 1.0 / (1.0 + 0.3 * math.log(max(cd, 1) / cd_ref + 1))

    # 速度指数の影響（最適値は0.3-0.5付近）
    alpha_opt = 0.4
    alpha_penalty = 1.0 + 0.5 * abs(alpha - alpha_opt)

    # 降伏荷重の影響
    qd_ref = 200.0
    yield_effect = 0.9 + 0.1 * (qd / qd_ref)

    # 剛性の影響
    k_ref = 50000.0
    stiffness_effect = 1.0 / (1.0 + 0.1 * math.log(max(k, 1) / k_ref + 1))

    # ランダムノイズ（解析の不確実性を模擬）
    noise = 1.0 + random.gauss(0, 0.02)

    # 応答値を計算
    result["max_drift"] = base_drift * damping_effect * alpha_penalty * stiffness_effect * noise
    result["max_acc"] = base_acc * (0.7 + 0.3 * damping_effect) * yield_effect * noise
    result["max_disp"] = base_disp * damping_effect * alpha_penalty * noise
    result["max_vel"] = base_vel * damping_effect * noise
    result["shear_coeff"] = base_shear * yield_effect * (0.8 + 0.2 / damping_effect) * noise
    result["max_otm"] = base_otm * yield_effect * damping_effect * noise
    result["max_story_disp"] = result["max_disp"] * 0.3 * noise

    # 伝達関数1次ピークゲイン（dB）の簡易モデル
    # 減衰が大きいほどピーク倍率は下がる / 質量比の効果も反映
    base_peak = base_summary.get("peak_gain_db", 20.0)
    mass_ratio = params.get("mu", params.get("mass_ratio", 0.03))
    zeta_d = params.get("zeta_d", params.get("damping_ratio", 0.1))
    # TMD理論: ピーク低減は √(μ) と ζ_d に比例
    tmd_effect = 1.0 / (1.0 + 3.0 * math.sqrt(max(mass_ratio, 0.001)) * max(zeta_d, 0.01))
    result["peak_gain_db"] = base_peak * tmd_effect * damping_effect * noise

    return result


# ---------------------------------------------------------------------------
# 最適化ワーカー (QThread)
# ---------------------------------------------------------------------------

class _OptimizationWorker(QThread):
    """バックグラウンドで最適化を実行するスレッド。"""

    progress = Signal(int, int, str)  # (current, total, message)
    candidate_found = Signal(object)  # OptimizationCandidate
    finished_signal = Signal(object)  # OptimizationResult
    checkpoint_signal = Signal(object)  # OptimizationResult (intermediate)

    def __init__(
        self,
        config: OptimizationConfig,
        evaluate_fn: Optional[Callable] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._is_snap = evaluate_fn is not None
        self._evaluate_fn = evaluate_fn or self._default_evaluate
        self._cancelled = False
        self._robustness_success = 0
        self._robustness_failed = 0

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        config = self._config
        start_time = time.time()

        # 乱数シード設定（再現性の確保）
        if config.random_seed is not None:
            np.random.seed(config.random_seed)
            random.seed(config.random_seed)
            logger.info("乱数シード設定: %d", config.random_seed)

        # ロバスト最適化: 評価関数をラップして最悪ケース評価にする
        if config.robustness_samples > 0:
            base_fn = self._evaluate_fn

            def _robust_wrapper(params: Dict[str, float]) -> Dict[str, float]:
                return self._robust_evaluate_with(
                    params, config, base_fn,
                )

            self._evaluate_fn = _robust_wrapper

        if config.method == "grid":
            result = self._run_grid_search(config)
        elif config.method == "random":
            result = self._run_random_search(config)
        elif config.method == "lhs":
            result = self._run_lhs_search(config)
        elif config.method == "bayesian":
            result = self._run_bayesian_search(config)
        elif config.method == "ga":
            result = self._run_ga_search(config)
        elif config.method == "sa":
            result = self._run_sa_search(config)
        elif config.method == "nsga2":
            result = self._run_nsga2_search(config)
        else:
            result = OptimizationResult(
                config=config,
                message=f"未対応の探索手法: {config.method}"
            )

        result.elapsed_sec = time.time() - start_time
        result.config = config
        result.evaluation_method = "snap" if self._is_snap else "mock"

        # SNAP評価統計を取得（SnapEvaluatorの場合）
        evaluator = self._evaluate_fn
        if hasattr(evaluator, "stats"):
            try:
                result.evaluator_stats = evaluator.stats
            except Exception:
                pass

        # ロバスト摂動統計
        total_robust = self._robustness_success + self._robustness_failed
        if total_robust > 0:
            result.robustness_stats = {
                "success": self._robustness_success,
                "failed": self._robustness_failed,
                "total": total_robust,
                "success_rate": self._robustness_success / total_robust,
            }

        self.finished_signal.emit(result)

    def _default_evaluate(self, params: Dict[str, float]) -> Dict[str, float]:
        """デフォルト評価関数（モック）。"""
        base = {}
        if self._config.base_case and self._config.base_case.result_summary:
            base = self._config.base_case.result_summary
        return _mock_evaluate(params, base, self._config.objective_key)

    def _robust_evaluate_with(
        self,
        params: Dict[str, float],
        config: OptimizationConfig,
        base_fn: Callable,
    ) -> Dict[str, float]:
        """ロバスト評価: 中心値 + 摂動サンプルの最悪ケースを返す。

        各パラメータを ±robustness_delta の範囲でランダム摂動させ、
        robustness_samples 個の摂動を評価し、全サンプルの中で
        目的関数値が最悪（最大）のケースの応答値を返す。

        これにより、パラメータの製造誤差やモデル不確実性に対して
        頑健な設計解を見つけることができる。
        """
        n_samples = config.robustness_samples
        delta = config.robustness_delta

        # 中心値の評価
        center_response = base_fn(params)
        best_worst_obj = config.compute_objective(center_response, params)
        worst_response = center_response

        # 摂動サンプルの評価
        n_success = 0
        n_failed = 0
        for _ in range(n_samples):
            perturbed = {}
            for pr in config.parameters:
                base_val = params.get(pr.key, (pr.min_val + pr.max_val) / 2)
                if base_val != 0:
                    perturbation = random.uniform(-delta, delta) * abs(base_val)
                else:
                    perturbation = random.uniform(-delta, delta) * (pr.max_val - pr.min_val)
                val = base_val + perturbation
                val = max(pr.min_val, min(pr.max_val, val))
                if pr.is_integer:
                    val = round(val)
                perturbed[pr.key] = val
            try:
                resp = base_fn(perturbed)
                obj = config.compute_objective(resp, perturbed)
                n_success += 1
                if obj > best_worst_obj:
                    best_worst_obj = obj
                    worst_response = resp
            except Exception:
                n_failed += 1
                logger.debug("ロバスト摂動評価失敗: params=%s", params)

        # 統計を累積記録
        self._robustness_success += n_success
        self._robustness_failed += n_failed

        return worst_response

    def _maybe_checkpoint(
        self,
        all_candidates: List[OptimizationCandidate],
        best: Optional[OptimizationCandidate],
        config: OptimizationConfig,
        message: str = "",
    ) -> None:
        """チェックポイント間隔に達した場合に中間結果を保存シグナルで通知する。

        Parameters
        ----------
        all_candidates : list
            これまでの全候補。
        best : OptimizationCandidate or None
            現時点の最良解。
        config : OptimizationConfig
            最適化設定。
        message : str
            中間メッセージ。
        """
        interval = config.checkpoint_interval
        if interval <= 0 or len(all_candidates) % interval != 0:
            return
        intermediate = OptimizationResult(
            best=best,
            all_candidates=list(all_candidates),
            config=config,
            message=message or f"チェックポイント: {len(all_candidates)} 点評価済み",
            evaluation_method="snap" if self._is_snap else "mock",
        )
        self.checkpoint_signal.emit(intermediate)

    def _check_constraints(
        self,
        response: Dict[str, float],
        config: OptimizationConfig,
    ) -> tuple:
        """制約条件を満たすかチェックし、各制約のマージンを返します。

        応答データが空（評価失敗）の場合や、制約キーが応答に含まれない
        場合は自動的に infeasible として扱います。これにより、SNAP解析
        失敗時に制約違反を見逃すことを防ぎます。

        Returns
        -------
        (is_feasible, margins) : tuple[bool, Dict[str, float]]
            margins は各制約のマージン（正=余裕, 負=違反量）。
            キー欠損時は -inf をマージンとして記録します。
        """
        is_feasible = True
        margins: Dict[str, float] = {}

        # 応答が空の場合（評価失敗）は即座に infeasible
        if not response and (config.constraints or config.criteria):
            for key in config.constraints:
                margins[key] = float("-inf")
            if config.criteria:
                for item in config.criteria.items:
                    if item.enabled:
                        margins[f"criteria:{item.key}"] = float("-inf")
            logger.warning("応答データが空のため制約チェック不可 → infeasible")
            return False, margins

        # 明示的な制約
        for key, limit in config.constraints.items():
            if key in response:
                margins[key] = limit - response[key]
                if response[key] > limit:
                    is_feasible = False
            else:
                # 制約キーが応答に含まれない → 安全側で infeasible
                is_feasible = False
                margins[key] = float("-inf")
                logger.warning(
                    "制約キー '%s' が応答に含まれません → infeasible として扱います",
                    key,
                )
        # 性能基準による制約
        if config.criteria:
            verdicts = config.criteria.evaluate(response)
            # 有効な基準のキーセット（無効基準はNoneでも問題なし）
            enabled_keys = {
                item.key for item in config.criteria.items
                if item.enabled and item.limit_value is not None
            }
            for k, v in verdicts.items():
                if v is False:
                    is_feasible = False
                    margins[f"criteria:{k}"] = -1.0
                elif v is True:
                    margins[f"criteria:{k}"] = 1.0
                elif v is None and k in enabled_keys:
                    # 有効な基準なのに応答値が欠損 → 安全側で infeasible
                    is_feasible = False
                    margins[f"criteria:{k}"] = float("-inf")
                    logger.warning(
                        "性能基準 '%s' の応答値が欠損 → infeasible として扱います", k,
                    )
        return is_feasible, margins

    def _penalized_objective(
        self,
        obj_val: float,
        margins: Dict[str, float],
        config: OptimizationConfig,
    ) -> float:
        """制約ペナルティ付き目的関数値を計算する。

        constraint_penalty_weight > 0 の場合、制約違反量に比例したペナルティを
        目的関数に加算する。これにより制約境界付近の探索が改善される。

        Parameters
        ----------
        obj_val : float
            元の目的関数値。
        margins : dict
            各制約のマージン（正=余裕, 負=違反量）。
        config : OptimizationConfig
            最適化設定。

        Returns
        -------
        float
            ペナルティ付き目的関数値。制約ペナルティ重みが0なら元の値を返す。
        """
        w = config.constraint_penalty_weight
        if w <= 0 or not margins:
            return obj_val
        violation = sum(max(0.0, -m) for m in margins.values())
        return obj_val + w * violation

    def _evaluate_batch(
        self,
        param_list: List[Dict[str, float]],
        config: OptimizationConfig,
        start_iter: int = 0,
    ) -> List[OptimizationCandidate]:
        """複数パラメータセットを並列評価する。

        Parameters
        ----------
        param_list : list of dict
            評価するパラメータ辞書のリスト。
        config : OptimizationConfig
            最適化設定（n_parallel, 制約チェック用）。
        start_iter : int
            候補のiteration番号の開始値。

        Returns
        -------
        list of OptimizationCandidate
            評価結果の候補リスト（入力と同じ順序）。
        """
        n_workers = max(1, config.n_parallel)

        def _eval_single(args: Tuple[int, Dict[str, float]]) -> OptimizationCandidate:
            idx, params = args
            try:
                response = self._evaluate_fn(params)
            except Exception as e:
                logger.warning("並列評価エラー (iter=%d): %s", start_iter + idx, e)
                response = {}
            obj_val = config.compute_objective(response, params)
            is_feasible, margins = self._check_constraints(response, config)
            return OptimizationCandidate(
                params=params,
                objective_value=obj_val,
                response_values=response,
                is_feasible=is_feasible,
                iteration=start_iter + idx,
                constraint_margins=margins,
            )

        indexed = list(enumerate(param_list))

        if n_workers <= 1 or len(param_list) <= 1:
            return [_eval_single(item) for item in indexed]

        results: List[Optional[OptimizationCandidate]] = [None] * len(param_list)
        with concurrent.futures.ThreadPoolExecutor(max_workers=n_workers) as executor:
            future_to_idx = {
                executor.submit(_eval_single, item): item[0]
                for item in indexed
            }
            for future in concurrent.futures.as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    logger.warning("並列Future例外 (idx=%d): %s", idx, e)
                    params = param_list[idx]
                    results[idx] = OptimizationCandidate(
                        params=params,
                        objective_value=float("inf"),
                        is_feasible=False,
                        iteration=start_iter + idx,
                    )
        return [r for r in results if r is not None]

    def _run_grid_search(self, config: OptimizationConfig) -> OptimizationResult:
        """グリッドサーチで最適化を実行します。"""
        if not config.parameters:
            return OptimizationResult(message="探索パラメータが設定されていません。")

        # 各パラメータの値リストを生成
        param_values = []
        param_keys = []
        for pr in config.parameters:
            vals = pr.discrete_values(max_points=50)
            param_values.append(vals)
            param_keys.append(pr.key)

        combinations = list(itertools.product(*param_values))
        total = len(combinations)

        if total > 10000:
            combinations = combinations[:10000]
            total = 10000

        all_candidates: List[OptimizationCandidate] = []
        best: Optional[OptimizationCandidate] = None
        n_par = max(1, config.n_parallel)
        batch_size = max(n_par, 1)

        i = 0
        while i < total:
            if self._cancelled:
                break

            # バッチ生成
            batch_end = min(i + batch_size, total)
            batch_params = [
                dict(zip(param_keys, combinations[j]))
                for j in range(i, batch_end)
            ]

            # 並列評価
            batch_results = self._evaluate_batch(batch_params, config, start_iter=i)

            for cand in batch_results:
                all_candidates.append(cand)
                self.candidate_found.emit(cand)
                if cand.is_feasible and (best is None or cand.objective_value < best.objective_value):
                    best = cand

            i = batch_end

            # 進捗報告
            msg = f"評価中: {i}/{total}"
            if n_par > 1:
                msg += f" (並列{n_par})"
            if best:
                msg += f" | 暫定最良: {best.objective_value:.6g}"
            self.progress.emit(i, total, msg)

            # チェックポイント
            self._maybe_checkpoint(all_candidates, best, config)

        result = OptimizationResult(
            best=best,
            all_candidates=all_candidates,
            converged=True,
            message=f"グリッドサーチ完了: {len(all_candidates)} 点を評価" +
                    (f" (並列{n_par})" if n_par > 1 else "") +
                    (f", 制約満足 {len([c for c in all_candidates if c.is_feasible])} 点"
                     if config.constraints or config.criteria else ""),
        )
        return result

    def _run_random_search(self, config: OptimizationConfig) -> OptimizationResult:
        """ランダムサーチ（モンテカルロ）で最適化を実行します。"""
        if not config.parameters:
            return OptimizationResult(message="探索パラメータが設定されていません。")

        total = config.max_iterations
        all_candidates: List[OptimizationCandidate] = []
        best: Optional[OptimizationCandidate] = None
        no_improve_count = 0
        n_par = max(1, config.n_parallel)
        batch_size = max(n_par, 1)

        i = 0
        while i < total:
            if self._cancelled:
                break

            # バッチ分のランダムパラメータを生成
            batch_end = min(i + batch_size, total)
            batch_params = [
                {pr.key: pr.random_value() for pr in config.parameters}
                for _ in range(i, batch_end)
            ]

            # 並列評価
            batch_results = self._evaluate_batch(batch_params, config, start_iter=i)

            improved_in_batch = False
            for cand in batch_results:
                all_candidates.append(cand)
                self.candidate_found.emit(cand)
                if cand.is_feasible and (best is None or cand.objective_value < best.objective_value):
                    best = cand
                    improved_in_batch = True

            if improved_in_batch:
                no_improve_count = 0
            else:
                no_improve_count += len(batch_results)

            i = batch_end

            # 進捗報告
            msg = f"探索中: {i}/{total}"
            if n_par > 1:
                msg += f" (並列{n_par})"
            if best:
                msg += f" | 暫定最良: {best.objective_value:.6g}"
            self.progress.emit(i, total, msg)

            # チェックポイント
            self._maybe_checkpoint(all_candidates, best, config)

            # 早期終了（一定回数改善なし）
            if no_improve_count > max(50, total // 4):
                break

        converged = no_improve_count > max(50, total // 4)
        result = OptimizationResult(
            best=best,
            all_candidates=all_candidates,
            converged=converged,
            message=f"ランダムサーチ完了: {len(all_candidates)} 点を評価" +
                    (f" (並列{n_par})" if n_par > 1 else "") +
                    (", 収束" if converged else ""),
        )
        return result

    def _run_lhs_search(self, config: OptimizationConfig) -> OptimizationResult:
        """ラテン超方格サンプリング (LHS) で最適化を実行します。

        空間充填性に優れたサンプリング手法で、ランダムサーチより少ないサンプル数で
        パラメータ空間を均等にカバーします。構造信頼性解析やモンテカルロシミュレーション
        の前段として広く用いられます。
        """
        if not config.parameters:
            return OptimizationResult(message="探索パラメータが設定されていません。")

        total = config.max_iterations
        n_params = len(config.parameters)
        n_par = max(1, config.n_parallel)
        batch_size = max(n_par, 1)

        # LHS サンプル生成 ([0,1]^d)
        lhs_samples = self._latin_hypercube_sample(total, n_params)

        # [0,1] → 実パラメータ値に変換
        all_param_sets: List[Dict[str, float]] = []
        for i in range(total):
            params: Dict[str, float] = {}
            for j, pr in enumerate(config.parameters):
                u = lhs_samples[i, j]
                val = pr.min_val + u * (pr.max_val - pr.min_val)
                # ステップサイズ適用
                if pr.step > 0:
                    val = pr.min_val + round((val - pr.min_val) / pr.step) * pr.step
                    val = max(pr.min_val, min(pr.max_val, val))
                if pr.is_integer:
                    val = round(val)
                params[pr.key] = val
            all_param_sets.append(params)

        all_candidates: List[OptimizationCandidate] = []
        best: Optional[OptimizationCandidate] = None

        i = 0
        while i < total:
            if self._cancelled:
                break

            batch_end = min(i + batch_size, total)
            batch_params = all_param_sets[i:batch_end]
            batch_results = self._evaluate_batch(batch_params, config, start_iter=i)

            for cand in batch_results:
                all_candidates.append(cand)
                self.candidate_found.emit(cand)
                if cand.is_feasible and (best is None or cand.objective_value < best.objective_value):
                    best = cand

            i = batch_end

            msg = f"LHS探索中: {i}/{total}"
            if n_par > 1:
                msg += f" (並列{n_par})"
            if best:
                msg += f" | 暫定最良: {best.objective_value:.6g}"
            self.progress.emit(i, total, msg)

            self._maybe_checkpoint(all_candidates, best, config)

        result = OptimizationResult(
            best=best,
            all_candidates=all_candidates,
            converged=True,
            message=f"LHS完了: {len(all_candidates)} 点を評価（空間充填サンプリング）" +
                    (f" (並列{n_par})" if n_par > 1 else "") +
                    (f", 制約満足 {len([c for c in all_candidates if c.is_feasible])} 点"
                     if config.constraints or config.criteria else ""),
        )
        return result

    @staticmethod
    def _latin_hypercube_sample(n: int, d: int) -> np.ndarray:
        """
        ラテン超方格サンプリング (LHS)。

        [0, 1]^d の空間から n 個の点をバランスよく生成します。

        Parameters
        ----------
        n : int
            サンプル数。
        d : int
            次元数。

        Returns
        -------
        samples : ndarray, shape (n, d)
            [0, 1] 範囲のサンプル。
        """
        samples = np.zeros((n, d))
        for j in range(d):
            perm = np.random.permutation(n)
            for i in range(n):
                samples[i, j] = (perm[i] + np.random.rand()) / n
        return samples

    def _run_bayesian_search(self, config: OptimizationConfig) -> OptimizationResult:
        """
        ベイズ最適化で最適化を実行します。

        ガウス過程回帰（GP）と Expected Improvement（EI）獲得関数を使用して
        効率的にパラメータ空間を探索します。

        戦略:
          1. 初期探索フェーズ（~10点）: ランダムサンプリング
          2. ベイズフェーズ: GP学習 → EI評価 → 最良点選択 → 評価
          3. 最後までループして候補を蓄積

        Parameters
        ----------
        config : OptimizationConfig
            最適化設定。

        Returns
        -------
        OptimizationResult
            最適化結果。
        """
        if not config.parameters:
            return OptimizationResult(message="探索パラメータが設定されていません。")

        total = config.max_iterations
        all_candidates: List[OptimizationCandidate] = []
        best: Optional[OptimizationCandidate] = None

        # パラメータ標準化用の情報を保持
        param_keys = [pr.key for pr in config.parameters]
        param_mins = np.array([pr.min_val for pr in config.parameters])
        param_ranges = np.array([pr.max_val - pr.min_val for pr in config.parameters])

        # ウォームスタート: 前回結果を初期データとして注入
        warm_count = 0
        X_warm = []
        y_warm = []
        warm_candidates: List[OptimizationCandidate] = []
        if config.warm_start_candidates:
            for wc in config.warm_start_candidates:
                if all(k in wc.params for k in param_keys):
                    raw = np.array([wc.params[k] for k in param_keys])
                    x_norm = (raw - param_mins) / np.where(param_ranges == 0, 1.0, param_ranges)
                    X_warm.append(x_norm)
                    y_warm.append(wc.objective_value)
                    warm_candidates.append(wc)
            warm_count = len(X_warm)

        # 初期探索フェーズの回数（全体の10%または最小10回）- ウォーム分を差し引き
        n_init = max(0, min(10, max(10, total // 10)) - warm_count)
        n_bayesian = total - n_init - warm_count

        # 初期サンプル用のデータ（ウォームスタートデータを先に追加）
        X_init = list(X_warm)  # 正規化されたパラメータ
        y_init = list(y_warm)  # 目的関数値
        raw_X_init = []  # 元のスケールのパラメータ (warm-start分は再評価不要)

        # ウォームスタート候補を結果に追加
        for idx, wc in enumerate(warm_candidates):
            all_candidates.append(wc)
            self.candidate_found.emit(wc)
            if wc.is_feasible and (best is None or wc.objective_value < best.objective_value):
                best = wc
        if warm_count > 0:
            self.progress.emit(warm_count, total,
                               f"ウォームスタート: {warm_count}点を引き継ぎ")

        # === Phase 1: 初期ランダム探索 ===
        for i in range(n_init):
            if self._cancelled:
                break

            # ランダムパラメータ生成
            params = {pr.key: pr.random_value() for pr in config.parameters}
            raw_params = np.array([params[k] for k in param_keys])

            # 正規化
            x_normalized = (raw_params - param_mins) / param_ranges
            X_init.append(x_normalized)
            raw_X_init.append(raw_params)

            # 評価
            response = self._evaluate_fn(params)
            obj_val = config.compute_objective(response, params)
            is_feasible, margins = self._check_constraints(response, config)
            y_penalized = self._penalized_objective(obj_val, margins, config)
            y_init.append(y_penalized)

            candidate = OptimizationCandidate(
                params=params,
                objective_value=obj_val,
                response_values=response,
                is_feasible=is_feasible,
                iteration=warm_count + i,
                constraint_margins=margins,
            )
            all_candidates.append(candidate)
            self.candidate_found.emit(candidate)

            if is_feasible and (best is None or obj_val < best.objective_value):
                best = candidate

            # 進捗報告
            if i % max(1, n_init // 10) == 0 or i == n_init - 1:
                msg = f"初期探索: {i+1}/{n_init}"
                if warm_count > 0:
                    msg += f" (+ ウォーム{warm_count}点)"
                if best:
                    msg += f" | 暫定最良: {best.objective_value:.6g}"
                self.progress.emit(warm_count + i + 1, total, msg)

        # === Phase 2: ベイズ最適化フェーズ ===
        if len(X_init) > 0 and n_bayesian > 0:
            try:
                X_history = np.array(X_init)
                y_history = np.array(y_init)

                gp = _GaussianProcessRegressor(length_scale=1.0, noise=1e-6)

                for i in range(n_bayesian):
                    if self._cancelled:
                        break

                    # GP学習
                    gp.fit(X_history, y_history)

                    # 獲得関数の評価用に候補点をサンプル
                    n_candidates = min(500, max(100, total * 2))
                    X_candidates = np.random.uniform(0, 1, (n_candidates, len(param_keys)))

                    # 予測
                    mu, sigma = gp.predict(X_candidates)

                    # 目的関数の最小値
                    y_best = float(np.min(y_history))

                    # 獲得関数の評価
                    acq_values = _compute_acquisition(
                        config.acquisition_function,
                        mu, sigma, y_best,
                        xi=0.01,
                        kappa=config.acquisition_kappa,
                    )

                    # 最高の獲得関数値を持つ点を選択
                    best_idx = int(np.argmax(acq_values))
                    x_next = X_candidates[best_idx].copy()

                    # 元のスケールに戻す
                    raw_params = x_next * param_ranges + param_mins

                    # パラメータを丸める（整数パラメータの場合）
                    params = {}
                    for j, key in enumerate(param_keys):
                        val = raw_params[j]
                        pr = config.parameters[j]
                        if pr.is_integer:
                            val = round(val)
                        elif pr.step > 0:
                            val = round(val / pr.step) * pr.step
                        params[key] = val

                    # 評価
                    response = self._evaluate_fn(params)
                    obj_val = config.compute_objective(response, params)
                    is_feasible, margins = self._check_constraints(response, config)

                    candidate = OptimizationCandidate(
                        params=params,
                        objective_value=obj_val,
                        response_values=response,
                        is_feasible=is_feasible,
                        iteration=n_init + i,
                        constraint_margins=margins,
                    )
                    all_candidates.append(candidate)
                    self.candidate_found.emit(candidate)

                    if is_feasible and (best is None or obj_val < best.objective_value):
                        best = candidate

                    # GPの履歴を更新（ペナルティ付き値でモデリング）
                    y_penalized = self._penalized_objective(obj_val, margins, config)
                    x_next_normalized = (raw_params - param_mins) / param_ranges
                    X_history = np.vstack([X_history, x_next_normalized])
                    y_history = np.hstack([y_history, y_penalized])

                    # 進捗報告
                    if (n_init + i) % max(1, total // 100) == 0 or (n_init + i) == total - 1:
                        msg = f"ベイズ探索: {n_init + i + 1}/{total}"
                        if best:
                            msg += f" | 暫定最良: {best.objective_value:.6g}"
                        self.progress.emit(n_init + i + 1, total, msg)

                    # チェックポイント
                    self._maybe_checkpoint(all_candidates, best, config)

            except Exception as e:
                # ベイズ最適化に失敗した場合、残りはランダムサーチでフォールバック
                logger.warning("Bayesian optimization failed (%s), falling back to random search", e)
                for i in range(n_bayesian):
                    if self._cancelled:
                        break

                    params = {pr.key: pr.random_value() for pr in config.parameters}
                    response = self._evaluate_fn(params)
                    obj_val = config.compute_objective(response, params)
                    is_feasible, margins = self._check_constraints(response, config)

                    candidate = OptimizationCandidate(
                        params=params,
                        objective_value=obj_val,
                        response_values=response,
                        is_feasible=is_feasible,
                        iteration=n_init + i,
                        constraint_margins=margins,
                    )
                    all_candidates.append(candidate)
                    self.candidate_found.emit(candidate)

                    if is_feasible and (best is None or obj_val < best.objective_value):
                        best = candidate

                    if (n_init + i) % max(1, total // 100) == 0:
                        msg = f"ベイズ検索（フォールバック）: {n_init + i + 1}/{total}"
                        if best:
                            msg += f" | 暫定最良: {best.objective_value:.6g}"
                        self.progress.emit(n_init + i + 1, total, msg)

                    # チェックポイント
                    self._maybe_checkpoint(all_candidates, best, config)

        result = OptimizationResult(
            best=best,
            all_candidates=all_candidates,
            converged=True,
            message=f"ベイズ最適化完了: {len(all_candidates)} 点を評価（初期:{n_init}点+ベイズ:{len(all_candidates)-n_init}点）" +
                    (f", 制約満足 {len([c for c in all_candidates if c.is_feasible])} 点"
                     if config.constraints or config.criteria else ""),
        )
        return result

    # ------------------------------------------------------------------
    # 遺伝的アルゴリズム (GA)
    # ------------------------------------------------------------------

    def _run_ga_search(self, config: OptimizationConfig) -> OptimizationResult:
        """
        遺伝的アルゴリズムで最適化を実行します。

        染色体: 各パラメータの正規化値 [0, 1] ベクトル
        選択: トーナメント選択
        交叉: BLX-α 交叉 (α=0.5)
        突然変異: ガウシアン突然変異
        エリート保存: 上位10%を次世代に直接引き継ぎ
        """
        if not config.parameters:
            return OptimizationResult(message="探索パラメータが設定されていません。")

        n_params = len(config.parameters)
        # 次元数に応じた適応的集団サイズ: 高次元ほど大きな集団が必要
        base_pop = max(20, min(100, config.max_iterations // 5))
        pop_size = max(base_pop, min(100, 10 * n_params))
        n_generations = max(1, config.max_iterations // pop_size)
        n_elite = max(1, pop_size // 10)
        crossover_rate_init = 0.8
        mutation_rate_init = 0.15 if config.ga_adaptive_mutation else 0.1
        mutation_sigma_init = 0.15 if config.ga_adaptive_mutation else 0.1
        blx_alpha = 0.5
        tournament_size = 3

        all_candidates: List[OptimizationCandidate] = []
        best: Optional[OptimizationCandidate] = None
        total = pop_size * n_generations
        stagnation_limit = max(3, n_generations // 4)  # 世代数の1/4（最低3世代）
        no_improve_gens = 0

        def _decode(chromosome: np.ndarray) -> Dict[str, float]:
            params = {}
            for j, pr in enumerate(config.parameters):
                val = pr.min_val + chromosome[j] * (pr.max_val - pr.min_val)
                if pr.is_integer:
                    val = round(val)
                elif pr.step > 0:
                    val = round(val / pr.step) * pr.step
                val = max(pr.min_val, min(pr.max_val, val))
                params[pr.key] = val
            return params

        def _evaluate_individual(chromosome: np.ndarray, iteration: int) -> OptimizationCandidate:
            params = _decode(chromosome)
            response = self._evaluate_fn(params)
            obj_val = config.compute_objective(response, params)
            is_feasible, margins = self._check_constraints(response, config)
            return OptimizationCandidate(
                params=params,
                objective_value=obj_val,
                response_values=response,
                is_feasible=is_feasible,
                iteration=iteration,
                constraint_margins=margins,
            )

        def _fitness(c: OptimizationCandidate) -> float:
            if config.constraint_penalty_weight > 0:
                return self._penalized_objective(
                    c.objective_value, c.constraint_margins, config,
                )
            if not c.is_feasible:
                return float("inf")
            return c.objective_value

        # 初期集団生成（LHS + ウォームスタート）
        population = self._latin_hypercube_sample(pop_size, n_params)
        pop_candidates = []

        # ウォームスタート: 前回結果の上位個体で初期集団の一部を置換
        warm_injected = 0
        if config.warm_start_candidates:
            warm_sorted = sorted(
                [wc for wc in config.warm_start_candidates
                 if all(k in wc.params for k in [pr.key for pr in config.parameters])],
                key=lambda c: c.objective_value if c.is_feasible else float("inf"),
            )
            for wc in warm_sorted[:pop_size // 2]:  # 最大で集団の半分まで
                chromo = np.array([
                    (wc.params[pr.key] - pr.min_val) / max(pr.max_val - pr.min_val, 1e-12)
                    for pr in config.parameters
                ])
                chromo = np.clip(chromo, 0.0, 1.0)
                population[warm_injected] = chromo
                warm_injected += 1

        for i, chromo in enumerate(population):
            if self._cancelled:
                break
            cand = _evaluate_individual(chromo, i)
            pop_candidates.append(cand)
            all_candidates.append(cand)
            self.candidate_found.emit(cand)
            if best is None or _fitness(cand) < _fitness(best):
                best = cand

        warm_msg = f" (ウォーム{warm_injected}個体)" if warm_injected > 0 else ""
        self.progress.emit(pop_size, total, f"GA: 初期集団評価完了 ({pop_size}個体{warm_msg})")

        # 世代ループ
        best_before_gen = best
        for gen in range(1, n_generations):
            if self._cancelled:
                break

            # 適応的パラメータ: 世代進行率に基づいてレートを調整
            gen_ratio = gen / max(1, n_generations - 1)  # 0.0 → 1.0
            if config.ga_adaptive_mutation:
                # 序盤: 高突然変異率(探索) → 終盤: 低突然変異率(利用)
                mutation_rate = mutation_rate_init * (1.0 - 0.7 * gen_ratio)
                mutation_sigma = mutation_sigma_init * (1.0 - 0.6 * gen_ratio)
                # 交叉率は逆方向: 序盤やや低め → 終盤高め(局所精錬)
                crossover_rate = crossover_rate_init + (1.0 - crossover_rate_init) * gen_ratio * 0.5
            else:
                mutation_rate = mutation_rate_init
                mutation_sigma = mutation_sigma_init
                crossover_rate = crossover_rate_init

            # エリート選択
            sorted_indices = sorted(range(pop_size), key=lambda i: _fitness(pop_candidates[i]))
            new_population = np.zeros((pop_size, n_params))
            new_candidates = [None] * pop_size

            for e in range(n_elite):
                idx = sorted_indices[e]
                new_population[e] = population[idx]
                new_candidates[e] = pop_candidates[idx]

            # 子孫生成
            for k in range(n_elite, pop_size):
                if self._cancelled:
                    break

                # トーナメント選択 (親1)
                t_indices = random.sample(range(pop_size), tournament_size)
                p1_idx = min(t_indices, key=lambda i: _fitness(pop_candidates[i]))
                # トーナメント選択 (親2)
                t_indices = random.sample(range(pop_size), tournament_size)
                p2_idx = min(t_indices, key=lambda i: _fitness(pop_candidates[i]))

                parent1 = population[p1_idx]
                parent2 = population[p2_idx]

                # BLX-α 交叉
                if random.random() < crossover_rate:
                    child = np.zeros(n_params)
                    for j in range(n_params):
                        lo = min(parent1[j], parent2[j])
                        hi = max(parent1[j], parent2[j])
                        d = hi - lo
                        child[j] = random.uniform(lo - blx_alpha * d, hi + blx_alpha * d)
                else:
                    child = parent1.copy()

                # ガウシアン突然変異
                for j in range(n_params):
                    if random.random() < mutation_rate:
                        child[j] += random.gauss(0, mutation_sigma)

                # [0, 1] にクリップ
                child = np.clip(child, 0.0, 1.0)

                iteration = gen * pop_size + k
                cand = _evaluate_individual(child, iteration)
                new_population[k] = child
                new_candidates[k] = cand
                all_candidates.append(cand)
                self.candidate_found.emit(cand)

                if best is None or _fitness(cand) < _fitness(best):
                    best = cand

            population = new_population
            pop_candidates = new_candidates

            # 停滞検出
            if best is not None and best_before_gen is not None and best.objective_value < best_before_gen.objective_value:
                no_improve_gens = 0
            else:
                no_improve_gens += 1
            best_before_gen = best

            msg = f"GA: 世代 {gen+1}/{n_generations}"
            if best:
                msg += f" | 最良: {best.objective_value:.6g}"
            self.progress.emit(min((gen + 1) * pop_size, total), total, msg)

            # チェックポイント
            self._maybe_checkpoint(all_candidates, best, config)

            # 早期終了（一定世代数改善なし）
            if no_improve_gens >= stagnation_limit:
                logger.info("GA: %d世代連続で改善なし — 早期終了", no_improve_gens)
                break

        actual_gens = gen + 1 if n_generations > 1 else 1
        early_stopped = no_improve_gens >= stagnation_limit
        return OptimizationResult(
            best=best,
            all_candidates=all_candidates,
            converged=early_stopped,
            message=f"遺伝的アルゴリズム完了: {actual_gens}世代×{pop_size}個体 = {len(all_candidates)}点評価" +
                    (f" (早期収束: {no_improve_gens}世代改善なし)" if early_stopped else "") +
                    (f", 制約満足 {len([c for c in all_candidates if c.is_feasible])}点"
                     if config.constraints or config.criteria else ""),
        )

    # ------------------------------------------------------------------
    # 焼きなまし法 (SA)
    # ------------------------------------------------------------------

    def _run_sa_search(self, config: OptimizationConfig) -> OptimizationResult:
        """
        焼きなまし法で最適化を実行します。

        初期温度を自動設定し、指数冷却スケジュールで温度を下げていきます。
        メトロポリス基準に基づいて悪い解も確率的に受容し、局所最適からの脱出を図ります。
        """
        if not config.parameters:
            return OptimizationResult(message="探索パラメータが設定されていません。")

        n_params = len(config.parameters)
        total = config.max_iterations
        T_init = 1.0
        T_min = 1e-6
        cooling_rate = (T_min / T_init) ** (1.0 / max(1, total - 1))
        # 適応的ステップサイズ: パラメータ数に応じて調整
        # 高次元では小さめのステップで探索効率を維持
        step_size = min(0.3, 1.0 / max(1, n_params ** 0.5))
        stagnation_limit = max(50, total // 4)  # 改善なし許容回数

        all_candidates: List[OptimizationCandidate] = []
        best: Optional[OptimizationCandidate] = None

        def _decode(x: np.ndarray) -> Dict[str, float]:
            params = {}
            for j, pr in enumerate(config.parameters):
                val = pr.min_val + x[j] * (pr.max_val - pr.min_val)
                if pr.is_integer:
                    val = round(val)
                elif pr.step > 0:
                    val = round(val / pr.step) * pr.step
                val = max(pr.min_val, min(pr.max_val, val))
                params[pr.key] = val
            return params

        def _cost(cand: OptimizationCandidate) -> float:
            if config.constraint_penalty_weight > 0:
                return self._penalized_objective(
                    cand.objective_value, cand.constraint_margins, config,
                )
            if not cand.is_feasible:
                return cand.objective_value + 1e10  # ペナルティ
            return cand.objective_value

        # 初期解（ウォームスタートまたはランダム）
        if config.warm_start_candidates:
            # 前回の最良解を初期解として使用
            warm_sorted = sorted(
                [wc for wc in config.warm_start_candidates
                 if all(k in wc.params for k in [pr.key for pr in config.parameters])],
                key=lambda c: c.objective_value if c.is_feasible else float("inf"),
            )
            if warm_sorted:
                wb = warm_sorted[0]
                current_x = np.array([
                    (wb.params[pr.key] - pr.min_val) / max(pr.max_val - pr.min_val, 1e-12)
                    for pr in config.parameters
                ])
                current_x = np.clip(current_x, 0.0, 1.0)
            else:
                current_x = np.random.rand(n_params)
        else:
            current_x = np.random.rand(n_params)
        params = _decode(current_x)
        response = self._evaluate_fn(params)
        obj_val = config.compute_objective(response, params)
        is_feasible, margins = self._check_constraints(response, config)
        current_cand = OptimizationCandidate(
            params=params, objective_value=obj_val,
            response_values=response, is_feasible=is_feasible, iteration=0,
            constraint_margins=margins,
        )
        all_candidates.append(current_cand)
        self.candidate_found.emit(current_cand)
        best = current_cand
        current_cost = _cost(current_cand)
        best_cost = current_cost

        T = T_init
        n_accept = 0
        no_improve_count = 0
        # 適応ステップサイズ用: 直近の受容率をトラッキング
        adapt_window = max(20, total // 20)
        recent_accepts = 0
        recent_trials = 0

        for i in range(1, total):
            if self._cancelled:
                break

            # 適応的ステップサイズ: 受容率に基づく調整
            # 受容率が低すぎる→ステップを縮小、高すぎる→拡大
            if recent_trials >= adapt_window:
                ratio = recent_accepts / recent_trials
                if ratio < 0.2:
                    step_size *= 0.8  # ステップ縮小
                elif ratio > 0.5:
                    step_size *= 1.2  # ステップ拡大
                step_size = max(0.01, min(0.5, step_size))
                recent_accepts = 0
                recent_trials = 0

            # 近傍生成（温度比例 + 適応ステップ）
            perturbation = np.random.randn(n_params) * step_size * (T / T_init) ** 0.5
            new_x = np.clip(current_x + perturbation, 0.0, 1.0)

            params = _decode(new_x)
            response = self._evaluate_fn(params)
            obj_val = config.compute_objective(response, params)
            is_feasible, margins = self._check_constraints(response, config)

            cand = OptimizationCandidate(
                params=params, objective_value=obj_val,
                response_values=response, is_feasible=is_feasible, iteration=i,
                constraint_margins=margins,
            )
            all_candidates.append(cand)
            self.candidate_found.emit(cand)

            new_cost = _cost(cand)
            delta = new_cost - current_cost

            # メトロポリス基準
            recent_trials += 1
            if delta < 0 or (T > 0 and random.random() < math.exp(-delta / max(T, 1e-15))):
                current_x = new_x
                current_cost = new_cost
                current_cand = cand
                n_accept += 1
                recent_accepts += 1

            if new_cost < best_cost and is_feasible:
                best = cand
                best_cost = new_cost
                no_improve_count = 0
            else:
                no_improve_count += 1

            # 冷却
            T *= cooling_rate

            # 進捗報告
            if i % max(1, total // 50) == 0 or i == total - 1:
                msg = f"SA: {i+1}/{total}, T={T:.4g}"
                if best:
                    msg += f" | 最良: {best.objective_value:.6g}"
                self.progress.emit(i + 1, total, msg)

            # チェックポイント
            self._maybe_checkpoint(all_candidates, best, config)

            # 早期終了（一定回数改善なし）
            if no_improve_count >= stagnation_limit:
                logger.info("SA: %d回連続で改善なし — 早期終了", no_improve_count)
                break

        accept_ratio = n_accept / max(1, len(all_candidates) - 1)
        early_stopped = no_improve_count >= stagnation_limit
        return OptimizationResult(
            best=best,
            all_candidates=all_candidates,
            converged=early_stopped,
            message=f"焼きなまし法完了: {len(all_candidates)}点評価, 受容率 {accept_ratio:.1%}" +
                    (f" (早期収束: {no_improve_count}回改善なし)" if early_stopped else "") +
                    (f", 制約満足 {len([c for c in all_candidates if c.is_feasible])}点"
                     if config.constraints or config.criteria else ""),
        )

    # ------------------------------------------------------------------
    # NSGA-II 多目的最適化
    # ------------------------------------------------------------------

    def _run_nsga2_search(self, config: OptimizationConfig) -> OptimizationResult:
        """
        NSGA-II (Non-dominated Sorting Genetic Algorithm II) で多目的最適化を実行。

        Deb et al. (2002) の NSGA-II アルゴリズム:
          1. 非優越ソートでパレートランクを割り当て
          2. 同ランク内はクラウディング距離で多様性を維持
          3. バイナリトーナメント選択 + BLX-α交叉 + ガウシアン突然変異

        objective_weights が設定されている場合、そのキーを個別の目的関数として扱う。
        設定されていない場合は objective_key の単一目的で NSGA-II を実行（GA相当）。

        構造設計での典型的な使い方:
          - 目的1: max_drift（層間変形角） → 最小化
          - 目的2: max_acc（最大加速度） → 最小化
          → パレートフロントから設計者がトレードオフを確認して選択
        """
        if not config.parameters:
            return OptimizationResult(message="探索パラメータが設定されていません。")

        n_params = len(config.parameters)

        # 目的関数キーの決定
        if config.objective_weights:
            obj_keys = list(config.objective_weights.keys())
        else:
            obj_keys = [config.objective_key]
        n_objectives = len(obj_keys)

        # 集団サイズ・世代数
        base_pop = max(20, min(100, config.max_iterations // 5))
        pop_size = max(base_pop, min(100, 10 * n_params))
        # NSGA-II は多目的で広く探索するので集団を大きめに
        pop_size = max(pop_size, 40)
        # 偶数に揃える（交叉ペア生成のため）
        if pop_size % 2 != 0:
            pop_size += 1
        n_generations = max(1, config.max_iterations // pop_size)

        crossover_rate = 0.9
        mutation_rate = 0.1
        mutation_sigma = 0.1
        blx_alpha = 0.5
        tournament_size = 2  # NSGA-II 標準はバイナリトーナメント

        all_candidates: List[OptimizationCandidate] = []
        total = pop_size * n_generations

        def _decode(chromosome: np.ndarray) -> Dict[str, float]:
            params = {}
            for j, pr in enumerate(config.parameters):
                val = pr.min_val + chromosome[j] * (pr.max_val - pr.min_val)
                if pr.is_integer:
                    val = round(val)
                elif pr.step > 0:
                    val = round(val / pr.step) * pr.step
                val = max(pr.min_val, min(pr.max_val, val))
                params[pr.key] = val
            return params

        def _evaluate_individual(
            chromosome: np.ndarray, iteration: int,
        ) -> OptimizationCandidate:
            params = _decode(chromosome)
            response = self._evaluate_fn(params)
            obj_val = config.compute_objective(response, params)
            is_feasible, margins = self._check_constraints(response, config)
            return OptimizationCandidate(
                params=params,
                objective_value=obj_val,
                response_values=response,
                is_feasible=is_feasible,
                iteration=iteration,
                constraint_margins=margins,
            )

        def _get_objectives(cand: OptimizationCandidate) -> List[float]:
            """候補から各目的関数値のベクトルを取得。制約違反は大きな値を付与。"""
            if not cand.is_feasible and config.constraint_penalty_weight <= 0:
                return [float("inf")] * n_objectives
            vals = []
            for key in obj_keys:
                v = cand.response_values.get(key, float("inf"))
                vals.append(v)
            if config.constraint_penalty_weight > 0 and cand.constraint_margins:
                penalty = 0.0
                for margin in cand.constraint_margins.values():
                    if margin < 0:
                        penalty += abs(margin)
                penalty *= config.constraint_penalty_weight
                vals = [v + penalty for v in vals]
            return vals

        def _dominates(obj_a: List[float], obj_b: List[float]) -> bool:
            """a が b を支配するかどうか（全目的で a<=b かつ少なくとも1つで a<b）。"""
            at_least_one_better = False
            for va, vb in zip(obj_a, obj_b):
                if va > vb:
                    return False
                if va < vb:
                    at_least_one_better = True
            return at_least_one_better

        def _fast_non_dominated_sort(
            pop_objs: List[List[float]],
        ) -> List[List[int]]:
            """高速非優越ソート。パレートランク別のインデックスリストを返す。"""
            n = len(pop_objs)
            domination_count = [0] * n
            dominated_set: List[List[int]] = [[] for _ in range(n)]
            fronts: List[List[int]] = [[]]

            for p in range(n):
                for q in range(n):
                    if p == q:
                        continue
                    if _dominates(pop_objs[p], pop_objs[q]):
                        dominated_set[p].append(q)
                    elif _dominates(pop_objs[q], pop_objs[p]):
                        domination_count[p] += 1
                if domination_count[p] == 0:
                    fronts[0].append(p)

            i = 0
            while fronts[i]:
                next_front: List[int] = []
                for p in fronts[i]:
                    for q in dominated_set[p]:
                        domination_count[q] -= 1
                        if domination_count[q] == 0:
                            next_front.append(q)
                i += 1
                fronts.append(next_front)

            # 最後の空フロントを除外
            return [f for f in fronts if f]

        def _crowding_distance(
            front: List[int], pop_objs: List[List[float]],
        ) -> Dict[int, float]:
            """クラウディング距離を計算。"""
            distances: Dict[int, float] = {idx: 0.0 for idx in front}
            if len(front) <= 2:
                for idx in front:
                    distances[idx] = float("inf")
                return distances

            for m in range(n_objectives):
                sorted_front = sorted(front, key=lambda i: pop_objs[i][m])
                # 端点は無限大
                distances[sorted_front[0]] = float("inf")
                distances[sorted_front[-1]] = float("inf")
                obj_range = (
                    pop_objs[sorted_front[-1]][m] - pop_objs[sorted_front[0]][m]
                )
                if obj_range <= 0:
                    continue
                for k in range(1, len(sorted_front) - 1):
                    distances[sorted_front[k]] += (
                        pop_objs[sorted_front[k + 1]][m]
                        - pop_objs[sorted_front[k - 1]][m]
                    ) / obj_range

            return distances

        def _tournament_select(
            ranks: List[int],
            crowding: List[float],
            pop_size: int,
        ) -> int:
            """NSGA-II バイナリトーナメント選択。ランク優先、同ランクならクラウディング距離大を選択。"""
            indices = random.sample(range(pop_size), tournament_size)
            best_idx = indices[0]
            for idx in indices[1:]:
                if ranks[idx] < ranks[best_idx]:
                    best_idx = idx
                elif ranks[idx] == ranks[best_idx] and crowding[idx] > crowding[best_idx]:
                    best_idx = idx
            return best_idx

        # --- 初期集団生成 ---
        population = self._latin_hypercube_sample(pop_size, n_params)
        pop_candidates: List[OptimizationCandidate] = []

        for i, chromo in enumerate(population):
            if self._cancelled:
                break
            cand = _evaluate_individual(chromo, i)
            pop_candidates.append(cand)
            all_candidates.append(cand)
            self.candidate_found.emit(cand)

        self.progress.emit(
            pop_size, total, f"NSGA-II: 初期集団評価完了 ({pop_size}個体, {n_objectives}目的)",
        )

        # --- 世代ループ ---
        stagnation_limit = max(5, n_generations // 4)
        no_improve_gens = 0
        prev_front_size = 0

        for gen in range(1, n_generations):
            if self._cancelled:
                break

            # 非優越ソート + クラウディング距離
            pop_objs = [_get_objectives(c) for c in pop_candidates]
            fronts = _fast_non_dominated_sort(pop_objs)

            # ランクとクラウディング距離を各個体に割り当て
            ranks = [0] * pop_size
            crowding = [0.0] * pop_size
            for rank, front in enumerate(fronts):
                cd = _crowding_distance(front, pop_objs)
                for idx in front:
                    ranks[idx] = rank
                    crowding[idx] = cd[idx]

            # 子孫生成
            offspring_chromos = np.zeros((pop_size, n_params))
            offspring_candidates: List[OptimizationCandidate] = []

            for k in range(0, pop_size, 2):
                if self._cancelled:
                    break

                p1_idx = _tournament_select(ranks, crowding, pop_size)
                p2_idx = _tournament_select(ranks, crowding, pop_size)
                parent1 = population[p1_idx]
                parent2 = population[p2_idx]

                # BLX-α 交叉
                if random.random() < crossover_rate:
                    child1 = np.zeros(n_params)
                    child2 = np.zeros(n_params)
                    for j in range(n_params):
                        lo = min(parent1[j], parent2[j])
                        hi = max(parent1[j], parent2[j])
                        d = hi - lo
                        child1[j] = random.uniform(
                            lo - blx_alpha * d, hi + blx_alpha * d,
                        )
                        child2[j] = random.uniform(
                            lo - blx_alpha * d, hi + blx_alpha * d,
                        )
                else:
                    child1 = parent1.copy()
                    child2 = parent2.copy()

                # ガウシアン突然変異
                for child in (child1, child2):
                    for j in range(n_params):
                        if random.random() < mutation_rate:
                            child[j] += random.gauss(0, mutation_sigma)

                child1 = np.clip(child1, 0.0, 1.0)
                child2 = np.clip(child2, 0.0, 1.0)

                for ci, child in enumerate((child1, child2)):
                    idx = k + ci
                    if idx >= pop_size:
                        break
                    iteration = gen * pop_size + idx
                    cand = _evaluate_individual(child, iteration)
                    offspring_chromos[idx] = child
                    offspring_candidates.append(cand)
                    all_candidates.append(cand)
                    self.candidate_found.emit(cand)

            # --- 環境選択: 親 + 子 → 次世代 ---
            combined_chromos = np.vstack([population, offspring_chromos])
            combined_candidates = pop_candidates + offspring_candidates
            combined_objs = [_get_objectives(c) for c in combined_candidates]
            combined_fronts = _fast_non_dominated_sort(combined_objs)

            # 次世代の選択（ランク順、同ランクはクラウディング距離順）
            new_population = np.zeros((pop_size, n_params))
            new_candidates: List[OptimizationCandidate] = []
            count = 0

            for front in combined_fronts:
                if count >= pop_size:
                    break
                cd = _crowding_distance(front, combined_objs)
                sorted_front = sorted(
                    front, key=lambda i: cd[i], reverse=True,
                )
                for idx in sorted_front:
                    if count >= pop_size:
                        break
                    new_population[count] = combined_chromos[idx]
                    new_candidates.append(combined_candidates[idx])
                    count += 1

            population = new_population
            pop_candidates = new_candidates

            # パレートフロント（ランク0）のサイズで停滞検出
            current_front_size = len(combined_fronts[0]) if combined_fronts else 0
            if current_front_size == prev_front_size:
                no_improve_gens += 1
            else:
                no_improve_gens = 0
            prev_front_size = current_front_size

            # 進捗報告
            pareto_count = len(combined_fronts[0]) if combined_fronts else 0
            msg = f"NSGA-II: 世代 {gen+1}/{n_generations} | パレートフロント: {pareto_count}解"
            self.progress.emit(min((gen + 1) * pop_size, total), total, msg)

            # チェックポイント
            self._maybe_checkpoint(all_candidates, None, config)

            # 早期終了
            if no_improve_gens >= stagnation_limit:
                logger.info(
                    "NSGA-II: %d世代連続でパレートフロント変化なし — 早期終了",
                    no_improve_gens,
                )
                break

        # --- 結果集計 ---
        # 最終パレートフロントの抽出
        final_objs = [_get_objectives(c) for c in pop_candidates]
        final_fronts = _fast_non_dominated_sort(final_objs)
        pareto_front = final_fronts[0] if final_fronts else []

        # パレートフロント上の候補に pareto_rank を付与
        pareto_candidates = [pop_candidates[i] for i in pareto_front]

        # best は制約を満たすパレートフロント候補から、
        # compute_objective（重み付き和）で最良のものを選択
        best: Optional[OptimizationCandidate] = None
        feasible_pareto = [c for c in pareto_candidates if c.is_feasible]
        if feasible_pareto:
            best = min(feasible_pareto, key=lambda c: c.objective_value)
        elif pareto_candidates:
            best = min(pareto_candidates, key=lambda c: c.objective_value)

        actual_gens = gen + 1 if n_generations > 1 else 1
        early_stopped = no_improve_gens >= stagnation_limit
        n_pareto = len(pareto_front)
        n_feasible = len([c for c in all_candidates if c.is_feasible])

        return OptimizationResult(
            best=best,
            all_candidates=all_candidates,
            converged=early_stopped,
            message=(
                f"NSGA-II完了: {actual_gens}世代×{pop_size}個体 = "
                f"{len(all_candidates)}点評価, "
                f"パレートフロント {n_pareto}解"
                + (f" (早期収束: {no_improve_gens}世代変化なし)" if early_stopped else "")
                + (f", 制約満足 {n_feasible}点" if config.constraints or config.criteria else "")
            ),
        )


# ---------------------------------------------------------------------------
# DamperOptimizer（公開API）
# ---------------------------------------------------------------------------

class DamperOptimizer(QObject):
    """
    ダンパー最適化の管理クラス。

    Signals
    -------
    progress(current: int, total: int, message: str)
        進捗報告。
    candidate_found(candidate: OptimizationCandidate)
        候補が評価されるたびに発火。
    optimization_finished(result: OptimizationResult)
        最適化完了時に発火。
    """

    progress = Signal(int, int, str)
    candidate_found = Signal(object)
    optimization_finished = Signal(object)
    checkpoint = Signal(object)  # OptimizationResult (intermediate)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._worker: Optional[_OptimizationWorker] = None

    def optimize(
        self,
        config: OptimizationConfig,
        evaluate_fn: Optional[Callable] = None,
    ) -> None:
        """
        最適化を非同期で開始します。

        Parameters
        ----------
        config : OptimizationConfig
            最適化設定。
        evaluate_fn : callable, optional
            評価関数。None の場合はモック評価を使用。
            シグネチャ: (params: dict) -> dict
        """
        if self._worker and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(3000)

        self._worker = _OptimizationWorker(config, evaluate_fn)
        self._worker.progress.connect(self.progress)
        self._worker.candidate_found.connect(self.candidate_found)
        self._worker.finished_signal.connect(self._on_finished)
        self._worker.checkpoint_signal.connect(self.checkpoint)
        self._worker.start()

    def cancel(self) -> None:
        """実行中の最適化をキャンセルします。"""
        if self._worker and self._worker.isRunning():
            self._worker.cancel()

    def is_running(self) -> bool:
        """最適化が実行中かどうか。"""
        return self._worker is not None and self._worker.isRunning()

    def _on_finished(self, result: OptimizationResult) -> None:
        self.optimization_finished.emit(result)


# ---------------------------------------------------------------------------
# パラメータ感度解析
# ---------------------------------------------------------------------------

@dataclass
class SensitivityEntry:
    """1パラメータの感度解析結果。"""
    key: str
    label: str
    base_value: float
    variations: List[float]  # 変動割合 (-0.2, -0.1, 0, 0.1, 0.2 etc.)
    objective_values: List[float]  # 対応する目的関数値
    sensitivity_index: float = 0.0  # |Δobj / Δparam| の正規化指標


@dataclass
class SensitivityResult:
    """感度解析の全体結果。"""
    entries: List[SensitivityEntry]
    base_objective: float
    objective_key: str
    objective_label: str = ""

    @property
    def ranked_entries(self) -> List[SensitivityEntry]:
        """感度指標が高い順にソート。"""
        return sorted(self.entries, key=lambda e: e.sensitivity_index, reverse=True)


def compute_sensitivity(
    evaluate_fn: Callable[[Dict[str, float]], Dict[str, float]],
    best_params: Dict[str, float],
    parameters: List[ParameterRange],
    objective_key: str,
    variation_pcts: Optional[List[float]] = None,
) -> SensitivityResult:
    """
    最適解周りのパラメータ感度を計算します。

    各パラメータを1つずつ変動させ（OAT: One-At-a-Time）、
    目的関数の変化量を測定します。

    Parameters
    ----------
    evaluate_fn : callable
        パラメータ辞書 → 応答値辞書 の評価関数。
    best_params : dict
        最適解のパラメータ値。
    parameters : list of ParameterRange
        探索パラメータ定義。
    objective_key : str
        目的関数のキー。
    variation_pcts : list of float, optional
        変動割合リスト（デフォルト: [-0.20, -0.10, -0.05, 0, 0.05, 0.10, 0.20]）。

    Returns
    -------
    SensitivityResult
        感度解析結果。
    """
    if variation_pcts is None:
        variation_pcts = [-0.20, -0.10, -0.05, 0.0, 0.05, 0.10, 0.20]

    # ベース評価
    base_response = evaluate_fn(best_params)
    base_obj = base_response.get(objective_key, float("inf"))

    entries: List[SensitivityEntry] = []

    for pr in parameters:
        key = pr.key
        base_val = best_params.get(key, 0.0)
        if base_val == 0.0:
            continue

        obj_values: List[float] = []
        valid_variations: List[float] = []

        for pct in variation_pcts:
            varied_val = base_val * (1.0 + pct)
            # パラメータ範囲内にクランプ
            varied_val = max(pr.min_val, min(pr.max_val, varied_val))
            if pr.is_integer:
                varied_val = round(varied_val)

            trial_params = dict(best_params)
            trial_params[key] = varied_val

            try:
                resp = evaluate_fn(trial_params)
                obj_val = resp.get(objective_key, float("inf"))
                if not (math.isnan(obj_val) or math.isinf(obj_val)):
                    obj_values.append(obj_val)
                    valid_variations.append(pct)
            except Exception:
                logger.debug("感度解析: %s=%.4g で評価失敗", key, varied_val)

        # 感度指標: 目的関数の変動幅 / ベース値（正規化）
        si = 0.0
        if obj_values and base_obj != 0:
            obj_range = max(obj_values) - min(obj_values)
            si = obj_range / abs(base_obj)

        entries.append(SensitivityEntry(
            key=key,
            label=pr.label or key,
            base_value=base_val,
            variations=valid_variations,
            objective_values=obj_values,
            sensitivity_index=si,
        ))

    return SensitivityResult(
        entries=entries,
        base_objective=base_obj,
        objective_key=objective_key,
    )


# ---------------------------------------------------------------------------
# Sobol グローバル感度解析（分散ベース）
# ---------------------------------------------------------------------------

@dataclass
class SobolEntry:
    """1パラメータの Sobol 感度指標。"""
    key: str
    label: str
    s1: float  # 一次感度指標 (first-order): 直接寄与
    st: float  # 全次感度指標 (total-order): 直接 + 交互作用


@dataclass
class SobolResult:
    """Sobol 感度解析の全体結果。"""
    entries: List[SobolEntry]
    objective_key: str
    objective_label: str = ""
    n_samples: int = 0
    n_evaluations: int = 0

    @property
    def ranked_by_total(self) -> List[SobolEntry]:
        """全次感度指標が高い順にソート。"""
        return sorted(self.entries, key=lambda e: e.st, reverse=True)

    @property
    def interaction_indices(self) -> Dict[str, float]:
        """交互作用指標 (S_Ti - S_i) を返す。正に大きいほど他パラメータとの交互作用が強い。"""
        return {e.key: max(0.0, e.st - e.s1) for e in self.entries}


def _saltelli_sample(n: int, d: int) -> Tuple[np.ndarray, np.ndarray]:
    """Saltelli (2002) のサンプリング行列 A, B を生成する。

    Parameters
    ----------
    n : int
        基本サンプル数。全評価回数は n*(2d+2) 回になる。
    d : int
        パラメータ次元数。

    Returns
    -------
    A, B : ndarray, shape (n, d)
        [0, 1]^d の準ランダムサンプル行列。
    """
    # LHS を使用して空間充填性を向上
    samples_all = np.zeros((2 * n, d))
    for j in range(d):
        perm = np.random.permutation(2 * n)
        for i in range(2 * n):
            samples_all[i, j] = (perm[i] + np.random.rand()) / (2 * n)
    A = samples_all[:n]
    B = samples_all[n:]
    return A, B


def compute_sobol_sensitivity(
    evaluate_fn: Callable[[Dict[str, float]], Dict[str, float]],
    parameters: List[ParameterRange],
    objective_key: str,
    n_samples: int = 64,
    objective_label: str = "",
) -> SobolResult:
    """
    Sobol 分散ベースグローバル感度解析を実行します。

    Saltelli (2002) のサンプリングスキームに基づき、一次感度指標 (S1) と
    全次感度指標 (ST) を推定します。OAT 法と異なりパラメータ間の交互作用を
    捉えることができます。

    評価回数: n_samples × (2 × n_params + 2) 回

    Parameters
    ----------
    evaluate_fn : callable
        パラメータ辞書 → 応答値辞書 の評価関数。
    parameters : list of ParameterRange
        探索パラメータ定義。
    objective_key : str
        目的関数のキー。
    n_samples : int
        基本サンプル数（デフォルト64）。推奨: 64〜256。
    objective_label : str
        目的関数の日本語ラベル。

    Returns
    -------
    SobolResult
        感度解析結果（一次指標 S1 + 全次指標 ST）。
    """
    d = len(parameters)
    if d == 0:
        return SobolResult(entries=[], objective_key=objective_key,
                           objective_label=objective_label)

    # Saltelli サンプリング行列を生成
    A, B = _saltelli_sample(n_samples, d)

    def _to_params(row: np.ndarray) -> Dict[str, float]:
        """[0,1]^d のサンプルを実パラメータ値に変換する。"""
        params: Dict[str, float] = {}
        for j, pr in enumerate(parameters):
            val = pr.min_val + row[j] * (pr.max_val - pr.min_val)
            if pr.step > 0:
                val = pr.min_val + round((val - pr.min_val) / pr.step) * pr.step
                val = max(pr.min_val, min(pr.max_val, val))
            if pr.is_integer:
                val = round(val)
            params[pr.key] = val
        return params

    def _eval_obj(row: np.ndarray) -> float:
        """1行を評価して目的関数値を返す。"""
        try:
            resp = evaluate_fn(_to_params(row))
            val = resp.get(objective_key, float("inf"))
            if math.isnan(val) or math.isinf(val):
                return float("nan")
            return val
        except Exception:
            return float("nan")

    # f(A), f(B) を評価
    y_a = np.array([_eval_obj(A[i]) for i in range(n_samples)])
    y_b = np.array([_eval_obj(B[i]) for i in range(n_samples)])

    # AB_i, BA_i 行列（i番目の列のみ入れ替え）を評価
    y_ab = np.zeros((d, n_samples))  # AB_i: A の i列を B の i列に置換
    y_ba = np.zeros((d, n_samples))  # BA_i: B の i列を A の i列に置換
    for j in range(d):
        AB_j = A.copy()
        AB_j[:, j] = B[:, j]
        BA_j = B.copy()
        BA_j[:, j] = A[:, j]
        y_ab[j] = np.array([_eval_obj(AB_j[i]) for i in range(n_samples)])
        y_ba[j] = np.array([_eval_obj(BA_j[i]) for i in range(n_samples)])

    n_evaluations = n_samples * (2 * d + 2)

    # NaN を除外したマスクベースの推定
    entries: List[SobolEntry] = []
    for j in range(d):
        # 有効サンプルのマスク
        mask_s1 = ~(np.isnan(y_a) | np.isnan(y_b) | np.isnan(y_ba[j]))
        mask_st = ~(np.isnan(y_a) | np.isnan(y_ab[j]))

        n_valid_s1 = mask_s1.sum()
        n_valid_st = mask_st.sum()

        if n_valid_s1 < 4 or n_valid_st < 4:
            entries.append(SobolEntry(
                key=parameters[j].key,
                label=parameters[j].label or parameters[j].key,
                s1=0.0, st=0.0,
            ))
            continue

        # 全分散の推定: Var(Y) = E[Y^2] - E[Y]^2
        y_all_valid = np.concatenate([y_a[~np.isnan(y_a)], y_b[~np.isnan(y_b)]])
        f0 = np.mean(y_all_valid)
        var_y = np.var(y_all_valid, ddof=0)

        if var_y < 1e-30:
            entries.append(SobolEntry(
                key=parameters[j].key,
                label=parameters[j].label or parameters[j].key,
                s1=0.0, st=0.0,
            ))
            continue

        # 一次感度 S1_j = V_j / V(Y)
        # V_j = (1/n) Σ f(B) * [f(A_B^j) - f(A)]  (Jansen 1999 estimator)
        s1_num = np.mean(y_b[mask_s1] * (y_ba[j][mask_s1] - y_a[mask_s1]))
        s1 = s1_num / var_y

        # 全次感度 ST_j = 1 - V_~j / V(Y)
        # Jansen (1999): ST_j = (1/2n) Σ [f(A) - f(A_B^j)]^2 / V(Y)
        diff = y_a[mask_st] - y_ab[j][mask_st]
        st = np.mean(diff ** 2) / (2.0 * var_y)

        # クランプ [0, 1] 範囲に（サンプル数が少ないと範囲外になることがある）
        s1 = max(0.0, min(1.0, s1))
        st = max(0.0, min(1.0, st))

        entries.append(SobolEntry(
            key=parameters[j].key,
            label=parameters[j].label or parameters[j].key,
            s1=s1,
            st=st,
        ))

    return SobolResult(
        entries=entries,
        objective_key=objective_key,
        objective_label=objective_label,
        n_samples=n_samples,
        n_evaluations=n_evaluations,
    )


# ---------------------------------------------------------------------------
# パラメータ相関分析
# ---------------------------------------------------------------------------

@dataclass
class CorrelationEntry:
    """2パラメータ間の相関。"""
    param_x: str
    param_y: str
    label_x: str
    label_y: str
    correlation: float  # ピアソン相関係数 [-1, 1]
    x_values: List[float] = field(default_factory=list)
    y_values: List[float] = field(default_factory=list)


@dataclass
class CorrelationResult:
    """パラメータ相関分析の全体結果。"""
    entries: List[CorrelationEntry]
    param_keys: List[str]
    param_labels: List[str]
    n_candidates: int
    objective_key: str

    @property
    def correlation_matrix(self) -> List[List[float]]:
        """相関行列を2次元リストとして返す。"""
        n = len(self.param_keys)
        mat = [[1.0] * n for _ in range(n)]
        key_to_idx = {k: i for i, k in enumerate(self.param_keys)}
        for e in self.entries:
            i = key_to_idx.get(e.param_x)
            j = key_to_idx.get(e.param_y)
            if i is not None and j is not None:
                mat[i][j] = e.correlation
                mat[j][i] = e.correlation
        return mat

    @property
    def strong_correlations(self) -> List[CorrelationEntry]:
        """|r| >= 0.5 の強い相関のみ返す。"""
        return [e for e in self.entries if abs(e.correlation) >= 0.5]


def compute_correlation_analysis(
    result: OptimizationResult,
    top_n: int = 0,
) -> Optional[CorrelationResult]:
    """
    最適化結果の上位候補からパラメータ間の相関を分析します。

    設計者がどのパラメータが互いに関連して最適解に寄与しているかを
    把握するのに役立ちます。

    Parameters
    ----------
    result : OptimizationResult
        最適化結果。
    top_n : int
        上位何候補を使うか（0=制約満足候補すべて）。

    Returns
    -------
    CorrelationResult or None
        相関分析結果。候補が2つ未満の場合は None。
    """
    candidates = result.all_ranked_candidates
    if top_n > 0:
        candidates = candidates[:top_n]

    if len(candidates) < 3:
        return None

    # パラメータキーを取得
    param_keys = list(candidates[0].params.keys())
    if len(param_keys) < 2:
        return None

    # パラメータラベルを取得
    param_labels = list(param_keys)
    if result.config:
        key_to_label = {p.key: (p.label or p.key) for p in result.config.parameters}
        param_labels = [key_to_label.get(k, k) for k in param_keys]

    # 値を抽出
    param_values: Dict[str, List[float]] = {k: [] for k in param_keys}
    for c in candidates:
        for k in param_keys:
            param_values[k].append(c.params.get(k, 0.0))

    entries: List[CorrelationEntry] = []
    for i in range(len(param_keys)):
        for j in range(i + 1, len(param_keys)):
            kx, ky = param_keys[i], param_keys[j]
            xs = param_values[kx]
            ys = param_values[ky]

            # ピアソン相関係数を計算
            r = _pearson_correlation(xs, ys)

            entries.append(CorrelationEntry(
                param_x=kx,
                param_y=ky,
                label_x=param_labels[i],
                label_y=param_labels[j],
                correlation=r,
                x_values=list(xs),
                y_values=list(ys),
            ))

    return CorrelationResult(
        entries=entries,
        param_keys=param_keys,
        param_labels=param_labels,
        n_candidates=len(candidates),
        objective_key=result.config.objective_key if result.config else "",
    )


def _pearson_correlation(xs: List[float], ys: List[float]) -> float:
    """ピアソン相関係数を計算する。分散が0の場合は0を返す。"""
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sx = sum((x - mx) ** 2 for x in xs)
    sy = sum((y - my) ** 2 for y in ys)
    if sx == 0 or sy == 0:
        return 0.0
    sxy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    return sxy / math.sqrt(sx * sy)


# ---------------------------------------------------------------------------
# 最適化ログ詳細出力
# ---------------------------------------------------------------------------

def export_optimization_log(
    result: OptimizationResult,
    path: str,
) -> None:
    """
    最適化の全評価履歴を詳細CSVログとして出力します。

    構造設計の審査・規制文書で必要な「全ての評価とその結果」の
    トレーサビリティを提供します。

    出力列:
    - 評価番号, パラメータ値..., 目的関数値, 応答値..., 制約判定,
      制約マージン..., 評価方式, 時刻

    Parameters
    ----------
    result : OptimizationResult
        最適化結果。
    path : str
        出力CSVファイルパス。
    """
    if not result.all_candidates:
        return

    # ヘッダーを構築
    first = result.all_candidates[0]
    param_keys = sorted(first.params.keys())
    response_keys = sorted(first.response_values.keys())
    margin_keys = sorted(first.constraint_margins.keys()) if first.constraint_margins else []

    headers = ["評価番号"]
    headers.extend([f"param:{k}" for k in param_keys])
    headers.append("目的関数値")
    headers.extend([f"応答:{k}" for k in response_keys])
    headers.append("制約判定")
    headers.extend([f"制約マージン:{k}" for k in margin_keys])

    # メタデータ行
    meta_lines = []
    meta_lines.append(f"# 最適化ログ")
    if result.config:
        meta_lines.append(f"# 目的関数: {result.config.objective_label}")
        meta_lines.append(f"# 探索手法: {result.config.method}")
        meta_lines.append(f"# ダンパー種類: {result.config.damper_type or '未指定'}")
    eval_label = "SNAP実解析" if result.evaluation_method == "snap" else "モック評価"
    meta_lines.append(f"# 評価方式: {eval_label}")
    meta_lines.append(f"# 計算時間: {result.elapsed_sec:.2f} sec")
    meta_lines.append(f"# 総評価数: {len(result.all_candidates)}")
    meta_lines.append(f"# 制約満足数: {len(result.feasible_candidates)}")
    if result.best:
        meta_lines.append(f"# 最良目的関数値: {result.best.objective_value:.6g}")

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        # メタデータをコメント行として書き込み
        for line in meta_lines:
            f.write(line + "\n")

        writer = csv.writer(f)
        writer.writerow(headers)

        for c in result.all_candidates:
            row = [c.iteration]
            row.extend([c.params.get(k, "") for k in param_keys])
            row.append(c.objective_value)
            row.extend([c.response_values.get(k, "") for k in response_keys])
            row.append("OK" if c.is_feasible else "NG")
            row.extend([c.constraint_margins.get(k, "") for k in margin_keys])
            writer.writerow(row)


# ---------------------------------------------------------------------------
# 収束品質診断 (Convergence Quality Diagnostics)
# ---------------------------------------------------------------------------


@dataclass
class ConvergenceDiagnostics:
    """最適化結果の収束品質を診断する結果。

    設計者が「もう一度回すべきか」「結果を信頼してよいか」を
    判断するための定量的指標と推奨テキストを提供します。

    Attributes
    ----------
    feasibility_ratio : float
        制約満足率 (0.0-1.0)。
    improvement_ratio : float
        後半の改善率。0に近いほど収束済み。
    space_coverage : float
        パラメータ空間のカバー率 (0.0-1.0)。
    best_cluster_ratio : float
        最良解近傍の候補密度。高いほど信頼性が高い。
    stagnation_detected : bool
        末尾で停滞が検出されたか。
    n_evaluations : int
        総評価数。
    n_feasible : int
        制約満足候補数。
    quality_score : float
        総合品質スコア (0-100)。
    quality_label : str
        品質ラベル（優良/良好/要注意/不十分）。
    recommendations : list of str
        設計者への推奨アクション。
    """

    feasibility_ratio: float = 0.0
    improvement_ratio: float = 0.0
    space_coverage: float = 0.0
    best_cluster_ratio: float = 0.0
    stagnation_detected: bool = False
    n_evaluations: int = 0
    n_feasible: int = 0
    quality_score: float = 0.0
    quality_label: str = ""
    recommendations: List[str] = field(default_factory=list)


def compute_convergence_diagnostics(
    result: OptimizationResult,
) -> Optional[ConvergenceDiagnostics]:
    """最適化結果の収束品質を診断します。

    Parameters
    ----------
    result : OptimizationResult
        分析対象の最適化結果。

    Returns
    -------
    ConvergenceDiagnostics or None
        診断結果。候補が2未満の場合はNone。
    """
    candidates = result.all_candidates
    if len(candidates) < 2:
        return None

    n_total = len(candidates)
    feasible = result.feasible_candidates
    n_feasible = len(feasible)

    # --- 制約満足率 ---
    feasibility_ratio = n_feasible / n_total if n_total > 0 else 0.0

    # --- 後半の改善率 ---
    # 目的関数の累積最良値を追跡し、後半での改善幅を評価
    obj_values = [c.objective_value for c in candidates]
    half = max(1, n_total // 2)
    best_first_half = float("inf")
    for v in obj_values[:half]:
        if v < best_first_half:
            best_first_half = v
    best_second_half = float("inf")
    for v in obj_values[half:]:
        if v < best_second_half:
            best_second_half = v
    best_overall = min(best_first_half, best_second_half)

    if best_first_half > 0 and best_first_half != float("inf"):
        improvement_ratio = max(0.0, (best_first_half - best_overall) / abs(best_first_half))
    else:
        improvement_ratio = 0.0

    # --- パラメータ空間カバー率 ---
    space_coverage = _compute_space_coverage(candidates, result.config)

    # --- 最良解近傍の候補密度 ---
    best_cluster_ratio = _compute_best_cluster_ratio(candidates, result.best)

    # --- 停滞検出 ---
    stagnation_detected = _check_tail_stagnation(obj_values)

    # --- 総合品質スコア ---
    score = 0.0
    recommendations: List[str] = []

    # 制約満足率の評価 (0-25点)
    if feasibility_ratio >= 0.3:
        score += 25
    elif feasibility_ratio >= 0.1:
        score += 15
    elif feasibility_ratio > 0:
        score += 5
    else:
        recommendations.append(
            "制約を満たす候補が0件です。制約条件の緩和またはパラメータ範囲の拡大を検討してください。"
        )

    # 後半改善率の評価 (0-25点) — 低いほど収束している
    if improvement_ratio < 0.005:
        score += 25  # ほぼ収束
    elif improvement_ratio < 0.02:
        score += 20
    elif improvement_ratio < 0.05:
        score += 10
        recommendations.append(
            "後半でまだ改善が見られます。反復数を増やすとより良い解が見つかる可能性があります。"
        )
    else:
        score += 5
        recommendations.append(
            "後半で大きな改善が続いています。反復数を1.5〜2倍に増やして再実行を推奨します。"
        )

    # 空間カバー率の評価 (0-25点)
    if space_coverage >= 0.6:
        score += 25
    elif space_coverage >= 0.3:
        score += 15
    elif space_coverage >= 0.1:
        score += 8
        recommendations.append(
            "パラメータ空間の探索が不十分です。ランダムサーチまたはGA手法を検討してください。"
        )
    else:
        recommendations.append(
            "探索範囲のごく一部しか評価されていません。評価数の大幅な増加を推奨します。"
        )

    # 最良解近傍の密度評価 (0-25点)
    if best_cluster_ratio >= 0.15:
        score += 25
    elif best_cluster_ratio >= 0.08:
        score += 18
    elif best_cluster_ratio >= 0.03:
        score += 10
        recommendations.append(
            "最良解の近傍にもっと候補があると信頼性が高まります。ベイズ最適化の活用を検討してください。"
        )
    else:
        score += 3
        recommendations.append(
            "最良解が孤立しています。局所最適の可能性があります。異なる初期条件で再実行を推奨します。"
        )

    # 停滞ペナルティ
    if stagnation_detected and improvement_ratio < 0.005:
        # 停滞しているが収束済みなのでOK
        pass
    elif stagnation_detected:
        score = max(0, score - 5)
        recommendations.append(
            "探索末尾で停滞が検出されました。探索手法の変更（GA→ベイズ、SA→ランダム等）を検討してください。"
        )

    # 品質ラベル
    if score >= 80:
        quality_label = "優良"
    elif score >= 60:
        quality_label = "良好"
    elif score >= 40:
        quality_label = "要注意"
    else:
        quality_label = "不十分"

    if not recommendations:
        recommendations.append("探索品質は良好です。結果を信頼して設計に使用できます。")

    return ConvergenceDiagnostics(
        feasibility_ratio=feasibility_ratio,
        improvement_ratio=improvement_ratio,
        space_coverage=space_coverage,
        best_cluster_ratio=best_cluster_ratio,
        stagnation_detected=stagnation_detected,
        n_evaluations=n_total,
        n_feasible=n_feasible,
        quality_score=score,
        quality_label=quality_label,
        recommendations=recommendations,
    )


def _compute_space_coverage(
    candidates: List[OptimizationCandidate],
    config: Optional[OptimizationConfig],
) -> float:
    """パラメータ空間のカバー率を推定します。

    各パラメータの探索範囲をグリッド分割し、
    カバーされたセルの割合を返します。
    """
    if not config or not config.parameters or not candidates:
        return 0.0

    params = config.parameters
    n_params = len(params)
    if n_params == 0:
        return 0.0

    # 各パラメータを10分割し、カバーされたビンの数を計算
    n_bins = 10
    total_bins = n_bins ** min(n_params, 3)  # 高次元は3次元に投影

    # 各候補のパラメータ値をビンインデックスに変換
    covered = set()
    use_params = params[:3]  # 高次元は上位3パラメータに限定
    for c in candidates:
        bin_idx = []
        for pr in use_params:
            val = c.params.get(pr.key, pr.min_val)
            rng = pr.max_val - pr.min_val
            if rng <= 0:
                bin_idx.append(0)
            else:
                idx = int((val - pr.min_val) / rng * n_bins)
                idx = max(0, min(n_bins - 1, idx))
                bin_idx.append(idx)
        covered.add(tuple(bin_idx))

    return len(covered) / total_bins if total_bins > 0 else 0.0


def _compute_best_cluster_ratio(
    candidates: List[OptimizationCandidate],
    best: Optional[OptimizationCandidate],
) -> float:
    """最良解の近傍にある候補の割合を計算します。

    最良解から各パラメータの範囲の10%以内にある候補の比率。
    """
    if not best or not candidates or len(candidates) < 2:
        return 0.0

    param_keys = sorted(best.params.keys())
    if not param_keys:
        return 0.0

    # パラメータ範囲を候補群から推定
    ranges = {}
    for k in param_keys:
        vals = [c.params.get(k, 0.0) for c in candidates]
        rng = max(vals) - min(vals)
        ranges[k] = rng if rng > 0 else 1.0

    threshold = 0.10  # 10%以内
    near_count = 0
    for c in candidates:
        if c is best:
            continue
        is_near = True
        for k in param_keys:
            dist = abs(c.params.get(k, 0.0) - best.params.get(k, 0.0))
            if dist > ranges[k] * threshold:
                is_near = False
                break
        if is_near:
            near_count += 1

    return near_count / (len(candidates) - 1)


def _check_tail_stagnation(obj_values: List[float], window: int = 0) -> bool:
    """目的関数値列の末尾で停滞しているか判定します。"""
    n = len(obj_values)
    if n < 6:
        return False
    if window <= 0:
        window = max(3, n // 4)
    tail = obj_values[-window:]

    # 累積最良値の改善幅を確認
    best = float("inf")
    improvements = 0
    for v in tail:
        if v < best - abs(best) * 0.0001:
            best = v
            improvements += 1
        elif best == float("inf") and v != float("inf"):
            best = v

    return improvements <= 1
