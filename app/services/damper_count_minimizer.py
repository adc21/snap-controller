"""
app/services/damper_count_minimizer.py

ダンパー本数最小化サービス
==========================

目的
----
性能基準を満たしつつ、ダンパー合計本数を最小化する配置を自動探索します。

設計思想
--------
- 各階のダンパー本数 (quantity) をパラメータとして変更
- SNAP解析をループ実行し、層ごとの応答結果に基づいて判断
- 12種のアルゴリズムから選択可能

評価関数
--------
``evaluate_fn(quantities: Dict[str, int]) -> EvaluationResult``
  - quantities: 各階のダンパー本数 (例: {"F1": 3, "F2": 5})
  - 戻り値: EvaluationResult (層ごとの応答 + 制約判定)

アルゴリズム実装は minimizer_strategies.py に分離。
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------


@dataclass
class FloorResponse:
    """1階分の応答結果。"""
    floor_key: str        # "F1", "F2", ...
    values: Dict[str, float] = field(default_factory=dict)  # {"max_drift": 0.005, ...}
    damper_count: int = 0


@dataclass
class EvaluationResult:
    """1回の評価結果。"""
    floor_responses: List[FloorResponse] = field(default_factory=list)
    total_count: int = 0
    is_feasible: bool = False
    worst_margin: float = -1.0
    summary: Dict[str, float] = field(default_factory=dict)


@dataclass
class MinimizationStep:
    """最適化ステップの記録。"""
    iteration: int
    quantities: Dict[str, int]
    total_count: int
    is_feasible: bool
    worst_margin: float
    changed_floor: Optional[str] = None
    action: str = ""          # "add"/"remove"/"init"/"eval"/"final"
    note: str = ""
    summary: Dict[str, float] = field(default_factory=dict)


@dataclass
class MinimizationResult:
    """最小化の最終結果。"""
    strategy: str
    initial_quantities: Dict[str, int]
    final_quantities: Dict[str, int]
    final_count: int
    is_feasible: bool
    final_margin: float
    history: List[MinimizationStep] = field(default_factory=list)
    evaluations: int = 0
    note: str = ""
    final_floor_responses: List[FloorResponse] = field(default_factory=list)

    def summary_text(self) -> str:
        lines = [
            "=== ダンパー本数最小化結果 ===",
            f"戦略         : {self.strategy}",
            f"初期合計本数 : {sum(self.initial_quantities.values())}",
            f"最終合計本数 : {self.final_count}",
            f"基準充足     : {'OK' if self.is_feasible else 'NG'}",
            f"最終マージン : {self.final_margin:+.4f}",
            f"評価回数     : {self.evaluations}",
            "",
            "最終配置:",
        ]
        for k, v in sorted(self.final_quantities.items()):
            lines.append(f"  {k}: {v}本")
        if self.note:
            lines.append(f"備考: {self.note}")
        return "\n".join(lines)


# 評価関数型
EvaluateFn = Callable[[Dict[str, int]], EvaluationResult]
ProgressCb = Callable[[MinimizationStep], None]


# ---------------------------------------------------------------------------
# ヘルパー
# ---------------------------------------------------------------------------


def _make_step(
    iteration: int,
    quantities: Dict[str, int],
    result: EvaluationResult,
    action: str = "eval",
    changed_floor: Optional[str] = None,
    note: str = "",
) -> MinimizationStep:
    return MinimizationStep(
        iteration=iteration,
        quantities=dict(quantities),
        total_count=sum(quantities.values()),
        is_feasible=result.is_feasible,
        worst_margin=result.worst_margin,
        changed_floor=changed_floor,
        action=action,
        note=note,
        summary=dict(result.summary),
    )


def _clamp_quantities(
    quantities: Dict[str, int],
    max_quantities: Dict[str, int],
) -> Dict[str, int]:
    """本数を [0, max] にクランプ。"""
    return {
        k: max(0, min(v, max_quantities.get(k, v)))
        for k, v in quantities.items()
    }


# ---------------------------------------------------------------------------
# アルゴリズム定義
# ---------------------------------------------------------------------------

STRATEGIES = {
    "floor_add": "層別追加法",
    "floor_remove": "層別削減法",
    "binary_search": "一律二分探索",
    "linear_search": "一律線形探索",
    "ga": "遺伝的アルゴリズム (GA)",
    "sa": "焼きなまし法 (SA)",
    "pso": "粒子群最適化 (PSO)",
    "de": "差分進化 (DE)",
    "sqp": "SQP法",
    "nelder_mead": "Nelder-Mead法",
    "bayesian": "ベイズ最適化",
    "random": "ランダムサーチ",
}

STRATEGY_CATEGORIES = {
    "ドメイン特化": ["floor_add", "floor_remove", "binary_search", "linear_search"],
    "メタヒューリスティクス": ["ga", "sa", "pso", "de"],
    "数理最適化": ["sqp", "nelder_mead"],
    "サンプリング": ["bayesian", "random"],
}


# ---------------------------------------------------------------------------
# 統一エントリーポイント
# ---------------------------------------------------------------------------


def minimize_damper_count(
    floor_keys: List[str],
    max_quantities: Dict[str, int],
    evaluate_fn: EvaluateFn,
    strategy: str = "floor_add",
    initial_quantities: Optional[Dict[str, int]] = None,
    progress_cb: Optional[ProgressCb] = None,
    random_seed: Optional[int] = None,
    **kwargs,
) -> MinimizationResult:
    """
    戦略を指定してダンパー本数最小化を実行するエントリーポイント。

    Parameters
    ----------
    floor_keys : List[str]
        階のキーリスト (例: ["F1", "F2", "F3"])
    max_quantities : Dict[str, int]
        各階の最大ダンパー本数
    evaluate_fn : EvaluateFn
        評価関数: Dict[str, int] → EvaluationResult
    strategy : str
        アルゴリズム名 (STRATEGIES のキー)
    initial_quantities : Optional[Dict[str, int]]
        初期本数（floor_remove で使用）
    progress_cb : Optional[ProgressCb]
        進捗コールバック
    random_seed : Optional[int]
        乱数シード。整数を指定すると再現性のある結果を得られる。
    """
    # 遅延インポートで循環参照を回避
    from app.services.minimizer_strategies import (
        minimize_bayesian,
        minimize_binary_search,
        minimize_de,
        minimize_floor_add,
        minimize_floor_remove,
        minimize_ga,
        minimize_linear_search,
        minimize_nelder_mead,
        minimize_pso,
        minimize_random,
        minimize_sa,
        minimize_sqp,
    )

    # 乱数シード設定（再現性の確保）
    if random_seed is not None:
        np.random.seed(random_seed)
        random.seed(random_seed)
        logger.info("Minimizer乱数シード設定: %d", random_seed)

    strategy = strategy.lower()
    max_q = max(max_quantities.values()) if max_quantities else 10

    if strategy == "floor_add":
        return minimize_floor_add(floor_keys, max_quantities, evaluate_fn,
                                  progress_cb, **kwargs)
    elif strategy == "floor_remove":
        init_q = initial_quantities or max_quantities
        return minimize_floor_remove(floor_keys, init_q, evaluate_fn,
                                     progress_cb, **kwargs)
    elif strategy == "binary_search":
        return minimize_binary_search(floor_keys, max_q, evaluate_fn,
                                      progress_cb)
    elif strategy == "linear_search":
        return minimize_linear_search(floor_keys, max_q, evaluate_fn,
                                      progress_cb)
    elif strategy == "ga":
        return minimize_ga(floor_keys, max_quantities, evaluate_fn,
                           progress_cb, **kwargs)
    elif strategy == "sa":
        return minimize_sa(floor_keys, max_quantities, evaluate_fn,
                           progress_cb, **kwargs)
    elif strategy == "pso":
        return minimize_pso(floor_keys, max_quantities, evaluate_fn,
                            progress_cb, **kwargs)
    elif strategy == "de":
        return minimize_de(floor_keys, max_quantities, evaluate_fn,
                           progress_cb, **kwargs)
    elif strategy == "sqp":
        return minimize_sqp(floor_keys, max_quantities, evaluate_fn,
                            progress_cb, **kwargs)
    elif strategy == "nelder_mead":
        return minimize_nelder_mead(floor_keys, max_quantities, evaluate_fn,
                                    progress_cb, **kwargs)
    elif strategy == "bayesian":
        return minimize_bayesian(floor_keys, max_quantities, evaluate_fn,
                                 progress_cb, **kwargs)
    elif strategy == "random":
        return minimize_random(floor_keys, max_quantities, evaluate_fn,
                               progress_cb, **kwargs)
    else:
        raise ValueError(f"未知の戦略: {strategy}。有効な戦略: {list(STRATEGIES.keys())}")


# ---------------------------------------------------------------------------
# 後方互換: 個別アルゴリズム関数の再エクスポート
# ---------------------------------------------------------------------------
# 既存コードが from app.services.damper_count_minimizer import minimize_ga
# のようにインポートしているケースに対応

def __getattr__(name):
    """遅延再エクスポート: 個別アルゴリズム関数へのアクセスを minimizer_strategies に委譲。"""
    _delegated = {
        "minimize_floor_add", "minimize_floor_remove",
        "minimize_binary_search", "minimize_linear_search",
        "minimize_ga", "minimize_sa", "minimize_pso", "minimize_de",
        "minimize_sqp", "minimize_nelder_mead", "minimize_bayesian",
        "minimize_random", "_auto_penalty_weight",
    }
    if name in _delegated:
        from app.services import minimizer_strategies
        return getattr(minimizer_strategies, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "FloorResponse",
    "EvaluationResult",
    "MinimizationStep",
    "MinimizationResult",
    "EvaluateFn",
    "ProgressCb",
    "STRATEGIES",
    "STRATEGY_CATEGORIES",
    "minimize_damper_count",
    # 後方互換の再エクスポート
    "minimize_floor_add",
    "minimize_floor_remove",
    "minimize_binary_search",
    "minimize_linear_search",
    "minimize_ga",
    "minimize_sa",
    "minimize_pso",
    "minimize_de",
    "minimize_sqp",
    "minimize_nelder_mead",
    "minimize_bayesian",
    "minimize_random",
]
