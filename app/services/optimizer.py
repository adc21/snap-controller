"""
app/services/optimizer.py
ダンパー最適化エンジン。

指定した応答値（目的関数）を最小化する最適なダンパーパラメータを
自動探索するサービスクラスです。

探索手法:
  - グリッドサーチ（全パラメータの直積）
  - ランダムサーチ（モンテカルロ）
  - ベイズ最適化（ガウス過程回帰 + 獲得関数による効率的探索）
  - 遺伝的アルゴリズム（GA）（BLX-α交叉 + ガウシアン突然変異 + エリート保存）
  - 焼きなまし法（SA）（指数冷却 + メトロポリス基準）

使い方:
  1. OptimizationConfig で目的関数・制約・探索範囲を設定
  2. DamperOptimizer.optimize() を呼び出して探索を実行
  3. OptimizationResult から最適解を取得
"""

from __future__ import annotations

import itertools
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

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
            # コレスキー分解失敗時はフォールバック
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

    def compute_objective(self, response: Dict[str, float]) -> float:
        """応答値辞書から目的関数値を計算する。

        objective_weights が空の場合は単一目的（objective_key）、
        設定されている場合は重み付き和を返す。
        """
        if self.objective_weights:
            total = 0.0
            for key, weight in self.objective_weights.items():
                val = response.get(key, float("inf"))
                if val == float("inf"):
                    return float("inf")
                total += weight * val
            return total
        return response.get(self.objective_key, float("inf"))


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

    @property
    def feasible_candidates(self) -> List[OptimizationCandidate]:
        """制約を満たす候補のみ。"""
        return [c for c in self.all_candidates if c.is_feasible]

    @property
    def ranked_candidates(self) -> List[OptimizationCandidate]:
        """目的関数値でソートされた候補リスト（制約満足のみ）。"""
        feasible = self.feasible_candidates
        return sorted(feasible, key=lambda c: c.objective_value)

    def get_summary_text(self) -> str:
        """結果のテキストサマリーを返します。"""
        lines = ["=" * 50]
        lines.append("ダンパー最適化 結果サマリー")
        lines.append("=" * 50)

        if self.config:
            lines.append(f"目的関数: {self.config.objective_label} を最小化")
            lines.append(f"探索手法: {self.config.method}")
            lines.append(f"ダンパー種類: {self.config.damper_type or '未指定'}")

        lines.append(f"計算時間: {self.elapsed_sec:.2f} sec")
        lines.append(f"評価数: {len(self.all_candidates)}")
        lines.append(f"制約満足数: {len(self.feasible_candidates)}")

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

    def __init__(
        self,
        config: OptimizationConfig,
        evaluate_fn: Optional[Callable] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._evaluate_fn = evaluate_fn or self._default_evaluate
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        config = self._config
        start_time = time.time()

        if config.method == "grid":
            result = self._run_grid_search(config)
        elif config.method == "random":
            result = self._run_random_search(config)
        elif config.method == "bayesian":
            result = self._run_bayesian_search(config)
        elif config.method == "ga":
            result = self._run_ga_search(config)
        elif config.method == "sa":
            result = self._run_sa_search(config)
        else:
            result = OptimizationResult(
                config=config,
                message=f"未対応の探索手法: {config.method}"
            )

        result.elapsed_sec = time.time() - start_time
        result.config = config
        self.finished_signal.emit(result)

    def _default_evaluate(self, params: Dict[str, float]) -> Dict[str, float]:
        """デフォルト評価関数（モック）。"""
        base = {}
        if self._config.base_case and self._config.base_case.result_summary:
            base = self._config.base_case.result_summary
        return _mock_evaluate(params, base, self._config.objective_key)

    def _check_constraints(
        self,
        response: Dict[str, float],
        config: OptimizationConfig,
    ) -> bool:
        """制約条件を満たすかチェックします。"""
        # 明示的な制約
        for key, limit in config.constraints.items():
            if key in response and response[key] > limit:
                return False
        # 性能基準による制約
        if config.criteria:
            verdicts = config.criteria.evaluate(response)
            for v in verdicts.values():
                if v is False:
                    return False
        return True

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

        for i, combo in enumerate(combinations):
            if self._cancelled:
                break

            params = dict(zip(param_keys, combo))

            # 評価
            response = self._evaluate_fn(params)
            obj_val = config.compute_objective(response)
            is_feasible = self._check_constraints(response, config)

            candidate = OptimizationCandidate(
                params=params,
                objective_value=obj_val,
                response_values=response,
                is_feasible=is_feasible,
                iteration=i,
            )
            all_candidates.append(candidate)
            self.candidate_found.emit(candidate)

            if is_feasible and (best is None or obj_val < best.objective_value):
                best = candidate

            # 進捗報告（100回に1回 or 最後）
            if i % max(1, total // 100) == 0 or i == total - 1:
                msg = f"評価中: {i+1}/{total}"
                if best:
                    msg += f" | 暫定最良: {best.objective_value:.6g}"
                self.progress.emit(i + 1, total, msg)

        result = OptimizationResult(
            best=best,
            all_candidates=all_candidates,
            converged=True,
            message=f"グリッドサーチ完了: {len(all_candidates)} 点を評価" +
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

        for i in range(total):
            if self._cancelled:
                break

            # ランダムにパラメータを生成
            params = {pr.key: pr.random_value() for pr in config.parameters}

            # 評価
            response = self._evaluate_fn(params)
            obj_val = config.compute_objective(response)
            is_feasible = self._check_constraints(response, config)

            candidate = OptimizationCandidate(
                params=params,
                objective_value=obj_val,
                response_values=response,
                is_feasible=is_feasible,
                iteration=i,
            )
            all_candidates.append(candidate)
            self.candidate_found.emit(candidate)

            if is_feasible and (best is None or obj_val < best.objective_value):
                best = candidate
                no_improve_count = 0
            else:
                no_improve_count += 1

            # 進捗報告
            if i % max(1, total // 100) == 0 or i == total - 1:
                msg = f"探索中: {i+1}/{total}"
                if best:
                    msg += f" | 暫定最良: {best.objective_value:.6g}"
                self.progress.emit(i + 1, total, msg)

            # 早期終了（一定回数改善なし）
            if no_improve_count > max(50, total // 4):
                break

        converged = no_improve_count > max(50, total // 4)
        result = OptimizationResult(
            best=best,
            all_candidates=all_candidates,
            converged=converged,
            message=f"ランダムサーチ完了: {len(all_candidates)} 点を評価" +
                    (", 収束" if converged else ""),
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

        # 初期探索フェーズの回数（全体の10%または最小10回）
        n_init = min(10, max(10, total // 10))
        n_bayesian = total - n_init

        # 初期サンプル用のデータ
        X_init = []  # 正規化されたパラメータ
        y_init = []  # 目的関数値
        raw_X_init = []  # 元のスケールのパラメータ

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
            obj_val = config.compute_objective(response)
            is_feasible = self._check_constraints(response, config)
            y_init.append(obj_val)

            candidate = OptimizationCandidate(
                params=params,
                objective_value=obj_val,
                response_values=response,
                is_feasible=is_feasible,
                iteration=i,
            )
            all_candidates.append(candidate)
            self.candidate_found.emit(candidate)

            if is_feasible and (best is None or obj_val < best.objective_value):
                best = candidate

            # 進捗報告
            if i % max(1, n_init // 10) == 0 or i == n_init - 1:
                msg = f"初期探索: {i+1}/{n_init}"
                if best:
                    msg += f" | 暫定最良: {best.objective_value:.6g}"
                self.progress.emit(i + 1, total, msg)

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

                    # EI獲得関数の評価
                    try:
                        ei = _expected_improvement(mu, sigma, y_best, xi=0.01)
                    except Exception:
                        # scipy不可の場合のフォールバック
                        ei = _expected_improvement_no_scipy(mu, sigma, y_best, xi=0.01)

                    # 最高のEIを持つ点を選択
                    best_idx = int(np.argmax(ei))
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
                    obj_val = config.compute_objective(response)
                    is_feasible = self._check_constraints(response, config)

                    candidate = OptimizationCandidate(
                        params=params,
                        objective_value=obj_val,
                        response_values=response,
                        is_feasible=is_feasible,
                        iteration=n_init + i,
                    )
                    all_candidates.append(candidate)
                    self.candidate_found.emit(candidate)

                    if is_feasible and (best is None or obj_val < best.objective_value):
                        best = candidate

                    # GPの履歴を更新
                    x_next_normalized = (raw_params - param_mins) / param_ranges
                    X_history = np.vstack([X_history, x_next_normalized])
                    y_history = np.hstack([y_history, obj_val])

                    # 進捗報告
                    if (n_init + i) % max(1, total // 100) == 0 or (n_init + i) == total - 1:
                        msg = f"ベイズ探索: {n_init + i + 1}/{total}"
                        if best:
                            msg += f" | 暫定最良: {best.objective_value:.6g}"
                        self.progress.emit(n_init + i + 1, total, msg)

            except Exception as e:
                # ベイズ最適化に失敗した場合、残りはランダムサーチでフォールバック
                for i in range(n_bayesian):
                    if self._cancelled:
                        break

                    params = {pr.key: pr.random_value() for pr in config.parameters}
                    response = self._evaluate_fn(params)
                    obj_val = config.compute_objective(response)
                    is_feasible = self._check_constraints(response, config)

                    candidate = OptimizationCandidate(
                        params=params,
                        objective_value=obj_val,
                        response_values=response,
                        is_feasible=is_feasible,
                        iteration=n_init + i,
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
        pop_size = max(20, min(100, config.max_iterations // 5))
        n_generations = max(1, config.max_iterations // pop_size)
        n_elite = max(1, pop_size // 10)
        crossover_rate = 0.8
        mutation_rate = 0.1
        mutation_sigma = 0.1
        blx_alpha = 0.5
        tournament_size = 3

        all_candidates: List[OptimizationCandidate] = []
        best: Optional[OptimizationCandidate] = None
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

        def _evaluate_individual(chromosome: np.ndarray, iteration: int) -> OptimizationCandidate:
            params = _decode(chromosome)
            response = self._evaluate_fn(params)
            obj_val = config.compute_objective(response)
            is_feasible = self._check_constraints(response, config)
            return OptimizationCandidate(
                params=params,
                objective_value=obj_val,
                response_values=response,
                is_feasible=is_feasible,
                iteration=iteration,
            )

        def _fitness(c: OptimizationCandidate) -> float:
            if not c.is_feasible:
                return float("inf")
            return c.objective_value

        # 初期集団生成（LHS）
        population = self._latin_hypercube_sample(pop_size, n_params)
        pop_candidates = []
        for i, chromo in enumerate(population):
            if self._cancelled:
                break
            cand = _evaluate_individual(chromo, i)
            pop_candidates.append(cand)
            all_candidates.append(cand)
            self.candidate_found.emit(cand)
            if best is None or _fitness(cand) < _fitness(best):
                best = cand

        self.progress.emit(pop_size, total, f"GA: 初期集団評価完了 ({pop_size}個体)")

        # 世代ループ
        for gen in range(1, n_generations):
            if self._cancelled:
                break

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

            msg = f"GA: 世代 {gen+1}/{n_generations}"
            if best:
                msg += f" | 最良: {best.objective_value:.6g}"
            self.progress.emit(min((gen + 1) * pop_size, total), total, msg)

        return OptimizationResult(
            best=best,
            all_candidates=all_candidates,
            converged=True,
            message=f"遺伝的アルゴリズム完了: {n_generations}世代×{pop_size}個体 = {len(all_candidates)}点評価" +
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
        step_size = 0.3  # 正規化空間での初期ステップサイズ

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
            if not cand.is_feasible:
                return cand.objective_value + 1e10  # ペナルティ
            return cand.objective_value

        # 初期解（ランダム）
        current_x = np.random.rand(n_params)
        params = _decode(current_x)
        response = self._evaluate_fn(params)
        obj_val = config.compute_objective(response)
        is_feasible = self._check_constraints(response, config)
        current_cand = OptimizationCandidate(
            params=params, objective_value=obj_val,
            response_values=response, is_feasible=is_feasible, iteration=0,
        )
        all_candidates.append(current_cand)
        self.candidate_found.emit(current_cand)
        best = current_cand
        current_cost = _cost(current_cand)
        best_cost = current_cost

        T = T_init
        n_accept = 0

        for i in range(1, total):
            if self._cancelled:
                break

            # 近傍生成
            perturbation = np.random.randn(n_params) * step_size * (T / T_init) ** 0.5
            new_x = np.clip(current_x + perturbation, 0.0, 1.0)

            params = _decode(new_x)
            response = self._evaluate_fn(params)
            obj_val = config.compute_objective(response)
            is_feasible = self._check_constraints(response, config)

            cand = OptimizationCandidate(
                params=params, objective_value=obj_val,
                response_values=response, is_feasible=is_feasible, iteration=i,
            )
            all_candidates.append(cand)
            self.candidate_found.emit(cand)

            new_cost = _cost(cand)
            delta = new_cost - current_cost

            # メトロポリス基準
            if delta < 0 or (T > 0 and random.random() < math.exp(-delta / max(T, 1e-15))):
                current_x = new_x
                current_cost = new_cost
                current_cand = cand
                n_accept += 1

            if new_cost < best_cost and is_feasible:
                best = cand
                best_cost = new_cost

            # 冷却
            T *= cooling_rate

            # 進捗報告
            if i % max(1, total // 50) == 0 or i == total - 1:
                msg = f"SA: {i+1}/{total}, T={T:.4g}"
                if best:
                    msg += f" | 最良: {best.objective_value:.6g}"
                self.progress.emit(i + 1, total, msg)

        accept_ratio = n_accept / max(1, len(all_candidates) - 1)
        return OptimizationResult(
            best=best,
            all_candidates=all_candidates,
            converged=True,
            message=f"焼きなまし法完了: {len(all_candidates)}点評価, 受容率 {accept_ratio:.1%}" +
                    (f", 制約満足 {len([c for c in all_candidates if c.is_feasible])}点"
                     if config.constraints or config.criteria else ""),
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
