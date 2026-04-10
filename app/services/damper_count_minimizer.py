"""
app/services/damper_count_minimizer.py

ダンパー本数最小化サービス
==========================

目的
----
性能基準 (PerformanceCriteria) を満たしつつ、ダンパー本数を最小化する
配置を自動探索します。

アルゴリズム
------------
本モジュールは「評価関数注入型」の設計で、SNAP 解析の実行自体は
呼び出し側 (AnalysisService / SnapEvaluator) に委ね、本モジュールでは
**配置の選択論理のみ** を担当します。

探索戦略は次の 3 つから選択できます:

1. **greedy_remove** (推奨、既定)
   - 全候補位置に配置した「満載」配置から開始。
   - 各反復で、除去しても基準を満たせる配置の中から、
     性能マージンの減少が最小となる位置を 1 本ずつ除去。
   - 基準を破る直前で停止し、その配置を最小配置として返す。
   - 呼出回数: 最悪 O(N²) 回の評価、実用上 N*k 程度。

2. **greedy_add**
   - 空配置から開始。基準が満たされるまで 1 本ずつ追加。
   - 追加位置は「追加による基準マージン改善が最大」となる位置を選ぶ。
   - 呼出回数: 最悪 O(N²) 回の評価。

3. **exhaustive**
   - N ≤ 10 程度の小規模問題向け、全 2^N 通りを総当り。
   - 基準を満たす配置のうち最小本数を返す。

評価関数 (``evaluate_fn``) のシグネチャ:
    ``evaluate_fn(placement: List[bool]) -> Tuple[Dict[str, float], bool, float]``
  - placement: 各候補位置に配置するか否かの bool リスト
  - 戻り値:
      * result_summary : 基準評価に使う数値辞書
        (例: {"max_drift_angle": 0.005, "max_acc": 200.0, ...})
      * is_feasible    : 基準を満たすか (True/False)
      * margin         : 性能マージン (大きいほど余裕。負なら違反)
        通常は「(許容値 - 実値) / 許容値」の最小値を用いる。
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

EvaluateFn = Callable[[List[bool]], Tuple[Dict[str, float], bool, float]]


# ---------------------------------------------------------------------------
# 結果データクラス
# ---------------------------------------------------------------------------


@dataclass
class MinimizationStep:
    iteration: int
    action: str                        # "remove" / "add" / "init" / "final"
    position_index: Optional[int]      # 操作対象の候補位置
    placement: List[bool]              # その時点の配置
    count: int                         # ダンパー本数
    is_feasible: bool
    margin: float
    note: str = ""


@dataclass
class MinimizationResult:
    strategy: str
    initial_placement: List[bool]
    final_placement: List[bool]
    final_count: int
    is_feasible: bool
    final_margin: float
    history: List[MinimizationStep] = field(default_factory=list)
    evaluations: int = 0
    note: str = ""

    def summary_text(self) -> str:
        lines = [
            f"=== ダンパー本数最小化結果 ===",
            f"戦略         : {self.strategy}",
            f"初期本数     : {sum(self.initial_placement)}",
            f"最終本数     : {self.final_count}",
            f"基準充足     : {'OK' if self.is_feasible else 'NG'}",
            f"最終マージン : {self.final_margin:+.4f}",
            f"評価回数     : {self.evaluations}",
            "",
            "最終配置 (1=設置 / 0=撤去):",
            " ".join("1" if x else "0" for x in self.final_placement),
        ]
        if self.note:
            lines.append(f"備考: {self.note}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Greedy Remove
# ---------------------------------------------------------------------------


def minimize_greedy_remove(
    num_positions: int,
    evaluate_fn: EvaluateFn,
    initial_placement: Optional[List[bool]] = None,
    required_positions: Optional[List[int]] = None,
    progress_cb: Optional[Callable[[MinimizationStep], None]] = None,
) -> MinimizationResult:
    """
    満載配置から 1 本ずつ撤去していく貪欲法。

    Parameters
    ----------
    num_positions : int
        ダンパー配置候補位置の数
    evaluate_fn : EvaluateFn
        placement -> (summary, is_feasible, margin)
    initial_placement : Optional[List[bool]]
        初期配置。None なら全 True (満載)
    required_positions : Optional[List[int]]
        必ず残す位置（撤去不可）
    progress_cb : Optional[Callable]
        各ステップごとに呼ばれるコールバック
    """
    if initial_placement is None:
        placement = [True] * num_positions
    else:
        if len(initial_placement) != num_positions:
            raise ValueError("initial_placement length mismatch")
        placement = list(initial_placement)

    required = set(required_positions or [])

    evaluations = 0
    history: List[MinimizationStep] = []

    # --- 初期評価 ---
    summary, feasible, margin = evaluate_fn(placement)
    evaluations += 1
    step = MinimizationStep(
        iteration=0,
        action="init",
        position_index=None,
        placement=list(placement),
        count=sum(placement),
        is_feasible=feasible,
        margin=margin,
        note="初期満載配置",
    )
    history.append(step)
    if progress_cb:
        progress_cb(step)

    if not feasible:
        return MinimizationResult(
            strategy="greedy_remove",
            initial_placement=list(placement),
            final_placement=list(placement),
            final_count=sum(placement),
            is_feasible=False,
            final_margin=margin,
            history=history,
            evaluations=evaluations,
            note="満載配置でも基準を満たせない（基準が厳しすぎるか、ダンパー種が不足）。",
        )

    iteration = 0
    while True:
        iteration += 1
        # 現在 True の位置のうち、required 以外を 1 本ずつ試しに撤去
        candidates = [i for i, on in enumerate(placement) if on and i not in required]
        if not candidates:
            break

        best_i: Optional[int] = None
        best_margin = -math.inf
        best_summary: Optional[Dict[str, float]] = None

        for i in candidates:
            trial = list(placement)
            trial[i] = False
            s, feas, m = evaluate_fn(trial)
            evaluations += 1
            if feas and m > best_margin:
                best_margin = m
                best_i = i
                best_summary = s

        if best_i is None:
            # 撤去候補が全て基準違反となる -> 停止
            break

        placement[best_i] = False
        margin = best_margin
        step = MinimizationStep(
            iteration=iteration,
            action="remove",
            position_index=best_i,
            placement=list(placement),
            count=sum(placement),
            is_feasible=True,
            margin=margin,
            note=f"位置 {best_i} を撤去",
        )
        history.append(step)
        if progress_cb:
            progress_cb(step)

    # --- 最終確認 ---
    summary, feasible, margin = evaluate_fn(placement)
    evaluations += 1
    final_step = MinimizationStep(
        iteration=iteration + 1,
        action="final",
        position_index=None,
        placement=list(placement),
        count=sum(placement),
        is_feasible=feasible,
        margin=margin,
        note="最終配置",
    )
    history.append(final_step)
    if progress_cb:
        progress_cb(final_step)

    return MinimizationResult(
        strategy="greedy_remove",
        initial_placement=[True] * num_positions if initial_placement is None else list(initial_placement),
        final_placement=list(placement),
        final_count=sum(placement),
        is_feasible=feasible,
        final_margin=margin,
        history=history,
        evaluations=evaluations,
    )


# ---------------------------------------------------------------------------
# Greedy Add
# ---------------------------------------------------------------------------


def minimize_greedy_add(
    num_positions: int,
    evaluate_fn: EvaluateFn,
    required_positions: Optional[List[int]] = None,
    progress_cb: Optional[Callable[[MinimizationStep], None]] = None,
) -> MinimizationResult:
    """
    空配置から 1 本ずつ追加していく貪欲法。基準を満たした瞬間に停止する。
    """
    required = set(required_positions or [])
    placement = [i in required for i in range(num_positions)]

    history: List[MinimizationStep] = []
    evaluations = 0

    summary, feasible, margin = evaluate_fn(placement)
    evaluations += 1
    step = MinimizationStep(
        iteration=0,
        action="init",
        position_index=None,
        placement=list(placement),
        count=sum(placement),
        is_feasible=feasible,
        margin=margin,
        note="必須位置のみの初期配置" if required else "空配置",
    )
    history.append(step)
    if progress_cb:
        progress_cb(step)

    if feasible:
        return MinimizationResult(
            strategy="greedy_add",
            initial_placement=list(placement),
            final_placement=list(placement),
            final_count=sum(placement),
            is_feasible=True,
            final_margin=margin,
            history=history,
            evaluations=evaluations,
            note="初期配置で既に基準を満たす。",
        )

    iteration = 0
    while True:
        iteration += 1
        off_positions = [i for i, on in enumerate(placement) if not on]
        if not off_positions:
            break

        best_i: Optional[int] = None
        best_margin = -math.inf

        for i in off_positions:
            trial = list(placement)
            trial[i] = True
            s, feas, m = evaluate_fn(trial)
            evaluations += 1
            if m > best_margin:
                best_margin = m
                best_i = i
                if feas:
                    # 基準達成ならその時点で即採用
                    break

        if best_i is None:
            break

        placement[best_i] = True
        margin = best_margin
        step = MinimizationStep(
            iteration=iteration,
            action="add",
            position_index=best_i,
            placement=list(placement),
            count=sum(placement),
            is_feasible=best_margin >= 0.0,
            margin=margin,
            note=f"位置 {best_i} を追加",
        )
        history.append(step)
        if progress_cb:
            progress_cb(step)
        if margin >= 0.0:
            break

    summary, feasible, margin = evaluate_fn(placement)
    evaluations += 1
    final_step = MinimizationStep(
        iteration=iteration + 1,
        action="final",
        position_index=None,
        placement=list(placement),
        count=sum(placement),
        is_feasible=feasible,
        margin=margin,
        note="最終配置",
    )
    history.append(final_step)
    if progress_cb:
        progress_cb(final_step)

    return MinimizationResult(
        strategy="greedy_add",
        initial_placement=[i in required for i in range(num_positions)],
        final_placement=list(placement),
        final_count=sum(placement),
        is_feasible=feasible,
        final_margin=margin,
        history=history,
        evaluations=evaluations,
    )


# ---------------------------------------------------------------------------
# Exhaustive (小規模問題)
# ---------------------------------------------------------------------------


def minimize_exhaustive(
    num_positions: int,
    evaluate_fn: EvaluateFn,
    required_positions: Optional[List[int]] = None,
    max_positions: int = 12,
) -> MinimizationResult:
    """
    全探索。候補数 ≤ max_positions の場合のみ動作。
    """
    if num_positions > max_positions:
        raise ValueError(
            f"exhaustive search limited to {max_positions} positions, got {num_positions}"
        )
    required = set(required_positions or [])
    best_placement: Optional[List[bool]] = None
    best_count = num_positions + 1
    best_margin = -math.inf
    evaluations = 0

    for mask in range(1 << num_positions):
        placement = [bool((mask >> i) & 1) for i in range(num_positions)]
        if any((i in required) and not placement[i] for i in range(num_positions)):
            continue
        count = sum(placement)
        # 同じ本数でもマージンがより大きい解に更新し得るため、厳密な > で剪定する
        if count > best_count:
            continue
        _, feas, m = evaluate_fn(placement)
        evaluations += 1
        if feas and (count < best_count or (count == best_count and m > best_margin)):
            best_placement = placement
            best_count = count
            best_margin = m

    if best_placement is None:
        return MinimizationResult(
            strategy="exhaustive",
            initial_placement=[True] * num_positions,
            final_placement=[True] * num_positions,
            final_count=num_positions,
            is_feasible=False,
            final_margin=-math.inf,
            evaluations=evaluations,
            note="どの組合せでも基準を満たす配置が存在しない。",
        )

    return MinimizationResult(
        strategy="exhaustive",
        initial_placement=[True] * num_positions,
        final_placement=best_placement,
        final_count=best_count,
        is_feasible=True,
        final_margin=best_margin,
        evaluations=evaluations,
    )


# ---------------------------------------------------------------------------
# 統一エントリーポイント
# ---------------------------------------------------------------------------


def minimize_damper_count(
    num_positions: int,
    evaluate_fn: EvaluateFn,
    strategy: str = "greedy_remove",
    **kwargs,
) -> MinimizationResult:
    """
    戦略を指定してダンパー本数最小化を実行するエントリーポイント。

    strategy: "greedy_remove" | "greedy_add" | "exhaustive"
    """
    strategy = strategy.lower()
    if strategy == "greedy_remove":
        return minimize_greedy_remove(num_positions, evaluate_fn, **kwargs)
    elif strategy == "greedy_add":
        return minimize_greedy_add(num_positions, evaluate_fn, **kwargs)
    elif strategy == "exhaustive":
        return minimize_exhaustive(num_positions, evaluate_fn, **kwargs)
    else:
        raise ValueError(f"unknown strategy: {strategy}")


__all__ = [
    "EvaluateFn",
    "MinimizationStep",
    "MinimizationResult",
    "minimize_greedy_remove",
    "minimize_greedy_add",
    "minimize_exhaustive",
    "minimize_damper_count",
]
