"""
app/services/optimizer_analytics.py
最適化結果の分析・診断ユーティリティ。

optimizer.py から分離した分析モジュール:
  - パラメータ感度解析 (OAT法)
  - Sobol グローバル感度解析 (分散ベース)
  - パラメータ相関分析
  - 最適化ログ詳細出力
  - 収束品質診断
"""

from __future__ import annotations

import csv
import logging
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

import numpy as np

if TYPE_CHECKING:
    from .optimizer import (
        OptimizationCandidate,
        OptimizationConfig,
        OptimizationResult,
        ParameterRange,
    )

logger = logging.getLogger(__name__)


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
    parameters: List["ParameterRange"],
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
    parameters: List["ParameterRange"],
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
            logger.debug("Sobol評価失敗: row=%s", row)
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
    result: "OptimizationResult",
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
    result: "OptimizationResult",
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
    result: "OptimizationResult",
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
    feasibility_ratio = n_feasible / n_total if n_total > 0 else 0.0

    obj_values = [c.objective_value for c in candidates]
    improvement_ratio = _compute_improvement_ratio(obj_values, n_total)
    space_coverage = _compute_space_coverage(candidates, result.config)
    best_cluster_ratio = _compute_best_cluster_ratio(candidates, result.best)
    stagnation_detected = _check_tail_stagnation(obj_values)

    score, recommendations = _score_diagnostics(
        feasibility_ratio,
        improvement_ratio,
        space_coverage,
        best_cluster_ratio,
        stagnation_detected,
    )
    quality_label = _quality_label_from_score(score)
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


def _compute_improvement_ratio(obj_values: List[float], n_total: int) -> float:
    half = max(1, n_total // 2)
    best_first_half = min(obj_values[:half], default=float("inf"))
    best_second_half = min(obj_values[half:], default=float("inf"))
    best_overall = min(best_first_half, best_second_half)
    if best_first_half > 0 and best_first_half != float("inf"):
        return max(0.0, (best_first_half - best_overall) / abs(best_first_half))
    return 0.0


def _score_diagnostics(
    feasibility_ratio: float,
    improvement_ratio: float,
    space_coverage: float,
    best_cluster_ratio: float,
    stagnation_detected: bool,
) -> Tuple[float, List[str]]:
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
        score += 25
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

    # 停滞ペナルティ（収束済みなら無視）
    if stagnation_detected and improvement_ratio >= 0.005:
        score = max(0.0, score - 5)
        recommendations.append(
            "探索末尾で停滞が検出されました。探索手法の変更（GA→ベイズ、SA→ランダム等）を検討してください。"
        )

    return score, recommendations


def _quality_label_from_score(score: float) -> str:
    if score >= 80:
        return "優良"
    if score >= 60:
        return "良好"
    if score >= 40:
        return "要注意"
    return "不十分"


def _compute_space_coverage(
    candidates: List["OptimizationCandidate"],
    config: Optional["OptimizationConfig"],
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
    candidates: List["OptimizationCandidate"],
    best: Optional["OptimizationCandidate"],
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
