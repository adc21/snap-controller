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
"""

from __future__ import annotations

import logging
import math
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
# 1. 層別追加法
# ---------------------------------------------------------------------------


def minimize_floor_add(
    floor_keys: List[str],
    max_quantities: Dict[str, int],
    evaluate_fn: EvaluateFn,
    progress_cb: Optional[ProgressCb] = None,
    max_iterations: int = 500,
) -> MinimizationResult:
    """基準超過の階にダンパーを追加していく。"""
    quantities = {k: 0 for k in floor_keys}
    history: List[MinimizationStep] = []
    evaluations = 0

    for iteration in range(max_iterations):
        result = evaluate_fn(quantities)
        evaluations += 1

        action = "init" if iteration == 0 else "eval"
        step = _make_step(iteration, quantities, result, action=action)
        history.append(step)
        if progress_cb:
            progress_cb(step)

        if result.is_feasible:
            break

        # 基準超過の階を特定し、最もマージンが悪い階に+1
        worst_floor = None
        worst_margin_val = float("inf")
        for fr in result.floor_responses:
            # margin_* キーから最悪マージンを取得
            floor_margins = [v for k, v in fr.values.items() if k.startswith("margin_")]
            if floor_margins:
                floor_worst = min(floor_margins)
                if floor_worst < worst_margin_val and quantities.get(fr.floor_key, 0) < max_quantities.get(fr.floor_key, 999):
                    worst_margin_val = floor_worst
                    worst_floor = fr.floor_key

        if worst_floor is None:
            # マージン情報がない場合、ダンパー本数が少ない階から追加（本数昇順）
            candidates = [(k, quantities.get(k, 0)) for k in floor_keys
                          if quantities.get(k, 0) < max_quantities.get(k, 999)]
            if candidates:
                candidates.sort(key=lambda x: x[1])
                worst_floor = candidates[0][0]

        if worst_floor is None:
            break  # 全階が上限

        quantities[worst_floor] = quantities.get(worst_floor, 0) + 1

    # 最終評価
    result = evaluate_fn(quantities)
    evaluations += 1
    final_step = _make_step(evaluations, quantities, result, action="final")
    history.append(final_step)
    if progress_cb:
        progress_cb(final_step)

    return MinimizationResult(
        strategy="floor_add",
        initial_quantities={k: 0 for k in floor_keys},
        final_quantities=dict(quantities),
        final_count=sum(quantities.values()),
        is_feasible=result.is_feasible,
        final_margin=result.worst_margin,
        history=history,
        evaluations=evaluations,
    )


# ---------------------------------------------------------------------------
# 2. 層別削減法
# ---------------------------------------------------------------------------


def minimize_floor_remove(
    floor_keys: List[str],
    initial_quantities: Dict[str, int],
    evaluate_fn: EvaluateFn,
    progress_cb: Optional[ProgressCb] = None,
    max_iterations: int = 500,
) -> MinimizationResult:
    """余裕のある階から1本ずつ削減。"""
    quantities = dict(initial_quantities)
    history: List[MinimizationStep] = []
    evaluations = 0

    # 初期評価
    result = evaluate_fn(quantities)
    evaluations += 1
    step = _make_step(0, quantities, result, action="init")
    history.append(step)
    if progress_cb:
        progress_cb(step)

    if not result.is_feasible:
        return MinimizationResult(
            strategy="floor_remove",
            initial_quantities=dict(initial_quantities),
            final_quantities=dict(quantities),
            final_count=sum(quantities.values()),
            is_feasible=False,
            final_margin=result.worst_margin,
            history=history,
            evaluations=evaluations,
            note="初期配置で基準を満たしていません。",
        )

    for iteration in range(1, max_iterations + 1):
        # 余裕が最大の階を探す（本数>0の階から）
        best_floor = None
        best_margin = -math.inf
        best_trial_result = None

        removable = [k for k in floor_keys if quantities.get(k, 0) > 0]
        if not removable:
            break

        for k in removable:
            trial = dict(quantities)
            trial[k] -= 1
            trial_result = evaluate_fn(trial)
            evaluations += 1
            if trial_result.is_feasible and trial_result.worst_margin > best_margin:
                best_margin = trial_result.worst_margin
                best_floor = k
                best_trial_result = trial_result

        if best_floor is None:
            break  # どの階を減らしてもNG

        quantities[best_floor] -= 1
        result = best_trial_result  # 試行時に既に評価済み
        step = _make_step(iteration, quantities, result, action="remove",
                          changed_floor=best_floor, note=f"{best_floor} -1")
        history.append(step)
        if progress_cb:
            progress_cb(step)

    final_step = _make_step(len(history), quantities, result, action="final")
    history.append(final_step)
    if progress_cb:
        progress_cb(final_step)

    return MinimizationResult(
        strategy="floor_remove",
        initial_quantities=dict(initial_quantities),
        final_quantities=dict(quantities),
        final_count=sum(quantities.values()),
        is_feasible=result.is_feasible,
        final_margin=result.worst_margin,
        history=history,
        evaluations=evaluations,
    )


# ---------------------------------------------------------------------------
# 3. 一律二分探索
# ---------------------------------------------------------------------------


def minimize_binary_search(
    floor_keys: List[str],
    max_quantity: int,
    evaluate_fn: EvaluateFn,
    progress_cb: Optional[ProgressCb] = None,
) -> MinimizationResult:
    """全階一律にN本で二分探索。"""
    history: List[MinimizationStep] = []
    evaluations = 0

    lo, hi = 0, max_quantity
    best_feasible_n: Optional[int] = None
    best_result: Optional[EvaluationResult] = None

    while lo <= hi:
        mid = (lo + hi) // 2
        quantities = {k: mid for k in floor_keys}
        result = evaluate_fn(quantities)
        evaluations += 1

        step = _make_step(evaluations, quantities, result, action="eval",
                          note=f"N={mid} [{lo},{hi}]")
        history.append(step)
        if progress_cb:
            progress_cb(step)

        if result.is_feasible:
            best_feasible_n = mid
            best_result = result
            hi = mid - 1
        else:
            lo = mid + 1

    if best_feasible_n is None:
        quantities = {k: max_quantity for k in floor_keys}
        result = evaluate_fn(quantities)
        evaluations += 1
        return MinimizationResult(
            strategy="binary_search",
            initial_quantities={k: max_quantity for k in floor_keys},
            final_quantities=quantities,
            final_count=sum(quantities.values()),
            is_feasible=result.is_feasible,
            final_margin=result.worst_margin,
            history=history,
            evaluations=evaluations,
            note="最大本数でも基準を満たせません。",
        )

    final_q = {k: best_feasible_n for k in floor_keys}
    final_step = _make_step(evaluations + 1, final_q, best_result, action="final")
    history.append(final_step)
    if progress_cb:
        progress_cb(final_step)

    return MinimizationResult(
        strategy="binary_search",
        initial_quantities={k: max_quantity for k in floor_keys},
        final_quantities=final_q,
        final_count=sum(final_q.values()),
        is_feasible=True,
        final_margin=best_result.worst_margin,
        history=history,
        evaluations=evaluations,
    )


# ---------------------------------------------------------------------------
# 4. 一律線形探索
# ---------------------------------------------------------------------------


def minimize_linear_search(
    floor_keys: List[str],
    max_quantity: int,
    evaluate_fn: EvaluateFn,
    progress_cb: Optional[ProgressCb] = None,
) -> MinimizationResult:
    """全階一律にN本、上から下へ線形探索。"""
    history: List[MinimizationStep] = []
    evaluations = 0
    last_feasible_n = None
    last_feasible_result = None

    for n in range(max_quantity, -1, -1):
        quantities = {k: n for k in floor_keys}
        result = evaluate_fn(quantities)
        evaluations += 1

        step = _make_step(evaluations, quantities, result, action="eval",
                          note=f"N={n}")
        history.append(step)
        if progress_cb:
            progress_cb(step)

        if result.is_feasible:
            last_feasible_n = n
            last_feasible_result = result
        else:
            if last_feasible_n is not None:
                break  # 直前がfeasibleの最小

    if last_feasible_n is None:
        quantities = {k: max_quantity for k in floor_keys}
        return MinimizationResult(
            strategy="linear_search",
            initial_quantities={k: max_quantity for k in floor_keys},
            final_quantities=quantities,
            final_count=sum(quantities.values()),
            is_feasible=False,
            final_margin=-1.0,
            history=history,
            evaluations=evaluations,
            note="どの本数でも基準を満たせません。",
        )

    final_q = {k: last_feasible_n for k in floor_keys}
    final_step = _make_step(evaluations + 1, final_q, last_feasible_result,
                            action="final")
    history.append(final_step)
    if progress_cb:
        progress_cb(final_step)

    return MinimizationResult(
        strategy="linear_search",
        initial_quantities={k: max_quantity for k in floor_keys},
        final_quantities=final_q,
        final_count=sum(final_q.values()),
        is_feasible=True,
        final_margin=last_feasible_result.worst_margin,
        history=history,
        evaluations=evaluations,
    )


# ---------------------------------------------------------------------------
# 5. 遺伝的アルゴリズム (GA)
# ---------------------------------------------------------------------------


def _auto_penalty_weight(max_quantities: Dict[str, int]) -> float:
    """問題スケールに応じたペナルティ重みを算出。

    ペナルティ = weight * |margin| なので、infeasible 解の目的関数値が
    feasible 解の最大合計本数より十分大きくなるように設定する。
    """
    max_total = sum(max_quantities.values()) if max_quantities else 10
    return max(100.0, max_total * 10.0)


def minimize_ga(
    floor_keys: List[str],
    max_quantities: Dict[str, int],
    evaluate_fn: EvaluateFn,
    progress_cb: Optional[ProgressCb] = None,
    population_size: int = 30,
    generations: int = 50,
    penalty_weight: Optional[float] = None,
) -> MinimizationResult:
    """整数GAで合計本数を最小化。"""
    if penalty_weight is None:
        penalty_weight = _auto_penalty_weight(max_quantities)
    n = len(floor_keys)
    maxes = [max_quantities.get(k, 10) for k in floor_keys]
    history: List[MinimizationStep] = []
    evaluations = 0
    best_solution: Optional[Dict[str, int]] = None
    best_obj = float("inf")
    best_result: Optional[EvaluationResult] = None

    def to_dict(arr):
        return {floor_keys[i]: int(arr[i]) for i in range(n)}

    def objective(arr):
        nonlocal evaluations, best_solution, best_obj, best_result
        q = to_dict(arr)
        result = evaluate_fn(q)
        evaluations += 1
        total = sum(q.values())
        penalty = 0.0 if result.is_feasible else penalty_weight * abs(result.worst_margin)
        obj = total + penalty
        if obj < best_obj:
            best_obj = obj
            best_solution = q
            best_result = result
        return obj, result

    # 初期集団
    pop = []
    for _ in range(population_size):
        ind = [random.randint(0, m) for m in maxes]
        pop.append(ind)

    for gen in range(generations):
        # 評価
        fitnesses = []
        for ind in pop:
            obj, result = objective(ind)
            fitnesses.append(obj)

        step = _make_step(gen, best_solution or to_dict(pop[0]),
                          best_result or EvaluationResult(),
                          action="eval", note=f"世代{gen} best={best_obj:.1f}")
        history.append(step)
        if progress_cb:
            progress_cb(step)

        # エリート
        sorted_idx = sorted(range(len(pop)), key=lambda i: fitnesses[i])
        elite_count = max(2, population_size // 10)
        new_pop = [list(pop[i]) for i in sorted_idx[:elite_count]]

        # 交叉 + 突然変異（トーナメント選択）
        def tournament(k=3):
            candidates = random.sample(range(population_size), min(k, population_size))
            return min(candidates, key=lambda i: fitnesses[i])

        while len(new_pop) < population_size:
            p1 = pop[tournament()]
            p2 = pop[tournament()]
            child = []
            for i in range(n):
                # BLX-α交叉 → 整数丸め
                lo_val = min(p1[i], p2[i])
                hi_val = max(p1[i], p2[i])
                alpha = 0.5
                rng = hi_val - lo_val
                c = random.uniform(lo_val - alpha * rng, hi_val + alpha * rng)
                c = max(0, min(maxes[i], round(c)))
                child.append(c)
            # 突然変異
            mut_rate = max(0.05, 0.3 * (1 - gen / max(1, generations)))
            for i in range(n):
                if random.random() < mut_rate:
                    child[i] = random.randint(0, maxes[i])
            new_pop.append(child)

        pop = new_pop

    if best_solution is None:
        best_solution = {k: max_quantities.get(k, 0) for k in floor_keys}
        best_result = evaluate_fn(best_solution)
        evaluations += 1

    final_step = _make_step(generations, best_solution, best_result, action="final")
    history.append(final_step)
    if progress_cb:
        progress_cb(final_step)

    return MinimizationResult(
        strategy="ga",
        initial_quantities={k: max_quantities.get(k, 0) for k in floor_keys},
        final_quantities=best_solution,
        final_count=sum(best_solution.values()),
        is_feasible=best_result.is_feasible,
        final_margin=best_result.worst_margin,
        history=history,
        evaluations=evaluations,
    )


# ---------------------------------------------------------------------------
# 6. 焼きなまし法 (SA)
# ---------------------------------------------------------------------------


def minimize_sa(
    floor_keys: List[str],
    max_quantities: Dict[str, int],
    evaluate_fn: EvaluateFn,
    progress_cb: Optional[ProgressCb] = None,
    max_iterations: int = 200,
    initial_temp: float = 100.0,
    penalty_weight: Optional[float] = None,
) -> MinimizationResult:
    """焼きなまし法。"""
    if penalty_weight is None:
        penalty_weight = _auto_penalty_weight(max_quantities)
    n = len(floor_keys)
    maxes = [max_quantities.get(k, 10) for k in floor_keys]
    history: List[MinimizationStep] = []
    evaluations = 0

    # 初期解: 最大本数から開始
    current = [max_quantities.get(k, 0) for k in floor_keys]
    current_result = evaluate_fn({floor_keys[i]: current[i] for i in range(n)})
    evaluations += 1

    def obj(arr, result):
        total = sum(arr)
        penalty = 0.0 if result.is_feasible else penalty_weight * abs(result.worst_margin)
        return total + penalty

    current_obj = obj(current, current_result)
    best = list(current)
    best_obj = current_obj
    best_result = current_result

    step = _make_step(0, {floor_keys[i]: current[i] for i in range(n)},
                      current_result, action="init")
    history.append(step)
    if progress_cb:
        progress_cb(step)

    t_min = 1e-3
    cooling_rate = (t_min / initial_temp) ** (1.0 / max(1, max_iterations - 1))

    for it in range(1, max_iterations + 1):
        temp = initial_temp * cooling_rate ** it

        # 近傍: ランダムな1階の本数を±1〜2
        neighbor = list(current)
        idx = random.randint(0, n - 1)
        delta = random.choice([-2, -1, 1, 2])
        neighbor[idx] = max(0, min(maxes[idx], neighbor[idx] + delta))

        q = {floor_keys[i]: neighbor[i] for i in range(n)}
        result = evaluate_fn(q)
        evaluations += 1
        neighbor_obj = obj(neighbor, result)

        # メトロポリス基準
        if neighbor_obj < current_obj or random.random() < math.exp(
            -(neighbor_obj - current_obj) / max(temp, 1e-10)
        ):
            current = neighbor
            current_obj = neighbor_obj
            current_result = result

        if current_obj < best_obj:
            best = list(current)
            best_obj = current_obj
            best_result = current_result

        if it % max(1, max_iterations // 20) == 0:
            step = _make_step(it, {floor_keys[i]: current[i] for i in range(n)},
                              current_result, action="eval",
                              note=f"T={temp:.1f} obj={current_obj:.1f}")
            history.append(step)
            if progress_cb:
                progress_cb(step)

    final_q = {floor_keys[i]: best[i] for i in range(n)}
    final_step = _make_step(max_iterations, final_q, best_result, action="final")
    history.append(final_step)
    if progress_cb:
        progress_cb(final_step)

    return MinimizationResult(
        strategy="sa",
        initial_quantities={k: max_quantities.get(k, 0) for k in floor_keys},
        final_quantities=final_q,
        final_count=sum(final_q.values()),
        is_feasible=best_result.is_feasible,
        final_margin=best_result.worst_margin,
        history=history,
        evaluations=evaluations,
    )


# ---------------------------------------------------------------------------
# 7. 粒子群最適化 (PSO)
# ---------------------------------------------------------------------------


def minimize_pso(
    floor_keys: List[str],
    max_quantities: Dict[str, int],
    evaluate_fn: EvaluateFn,
    progress_cb: Optional[ProgressCb] = None,
    n_particles: int = 20,
    max_iterations: int = 50,
    penalty_weight: Optional[float] = None,
) -> MinimizationResult:
    """粒子群最適化 (PSO)。"""
    if penalty_weight is None:
        penalty_weight = _auto_penalty_weight(max_quantities)
    n = len(floor_keys)
    maxes = np.array([max_quantities.get(k, 10) for k in floor_keys], dtype=float)
    history: List[MinimizationStep] = []
    evaluations = 0

    def obj_fn(arr):
        nonlocal evaluations
        q = {floor_keys[i]: int(arr[i]) for i in range(n)}
        result = evaluate_fn(q)
        evaluations += 1
        total = sum(q.values())
        penalty = 0.0 if result.is_feasible else penalty_weight * abs(result.worst_margin)
        return total + penalty, result

    # 初期化
    positions = np.array([[random.randint(0, int(m)) for m in maxes]
                          for _ in range(n_particles)], dtype=float)
    velocities = np.zeros((n_particles, n))
    p_best = positions.copy()
    p_best_obj = np.full(n_particles, float("inf"))
    g_best = positions[0].copy()
    g_best_obj = float("inf")
    g_best_result = EvaluationResult()

    w_start, w_end = 0.9, 0.4  # 慣性（線形減衰）
    c1, c2 = 1.5, 1.5  # 認知・社会係数
    v_max = maxes * 0.3  # 速度上限（探索範囲の30%）

    for it in range(max_iterations):
        for i in range(n_particles):
            clamped = np.clip(np.round(positions[i]), 0, maxes).astype(int)
            obj_val, result = obj_fn(clamped)

            if obj_val < p_best_obj[i]:
                p_best_obj[i] = obj_val
                p_best[i] = clamped.astype(float)

            if obj_val < g_best_obj:
                g_best_obj = obj_val
                g_best = clamped.astype(float)
                g_best_result = result

        step = _make_step(it, {floor_keys[i]: int(g_best[i]) for i in range(n)},
                          g_best_result, action="eval",
                          note=f"PSO iter={it} best={g_best_obj:.1f}")
        history.append(step)
        if progress_cb:
            progress_cb(step)

        # 慣性の線形減衰
        w = w_start - (w_start - w_end) * it / max(1, max_iterations - 1)

        # 速度・位置更新
        r1 = np.random.random((n_particles, n))
        r2 = np.random.random((n_particles, n))
        velocities = (w * velocities
                      + c1 * r1 * (p_best - positions)
                      + c2 * r2 * (g_best - positions))
        # 速度クランプ
        velocities = np.clip(velocities, -v_max, v_max)
        positions = positions + velocities

    final_q = {floor_keys[i]: int(g_best[i]) for i in range(n)}
    final_step = _make_step(max_iterations, final_q, g_best_result, action="final")
    history.append(final_step)
    if progress_cb:
        progress_cb(final_step)

    return MinimizationResult(
        strategy="pso",
        initial_quantities={k: max_quantities.get(k, 0) for k in floor_keys},
        final_quantities=final_q,
        final_count=sum(final_q.values()),
        is_feasible=g_best_result.is_feasible,
        final_margin=g_best_result.worst_margin,
        history=history,
        evaluations=evaluations,
    )


# ---------------------------------------------------------------------------
# 8. 差分進化 (DE)
# ---------------------------------------------------------------------------


def minimize_de(
    floor_keys: List[str],
    max_quantities: Dict[str, int],
    evaluate_fn: EvaluateFn,
    progress_cb: Optional[ProgressCb] = None,
    population_size: int = 30,
    max_iterations: int = 50,
    penalty_weight: Optional[float] = None,
    F: float = 0.8,
    CR: float = 0.9,
    adaptive: bool = True,
) -> MinimizationResult:
    """差分進化 (DE/rand/1/bin)。adaptive=True で jDE 自己適応 F/CR。"""
    if penalty_weight is None:
        penalty_weight = _auto_penalty_weight(max_quantities)
    n = len(floor_keys)
    maxes = [max_quantities.get(k, 10) for k in floor_keys]
    history: List[MinimizationStep] = []
    evaluations = 0
    best_solution: Optional[Dict[str, int]] = None
    best_obj = float("inf")
    best_result = EvaluationResult()

    def obj_fn(arr):
        nonlocal evaluations, best_solution, best_obj, best_result
        q = {floor_keys[i]: max(0, min(maxes[i], int(round(arr[i])))) for i in range(n)}
        result = evaluate_fn(q)
        evaluations += 1
        total = sum(q.values())
        penalty = 0.0 if result.is_feasible else penalty_weight * abs(result.worst_margin)
        obj = total + penalty
        if obj < best_obj:
            best_obj = obj
            best_solution = q
            best_result = result
        return obj

    # 初期集団
    pop = np.array([[random.randint(0, m) for m in maxes]
                     for _ in range(population_size)], dtype=float)
    pop_obj = np.array([obj_fn(ind) for ind in pop])

    # jDE: 個体ごとの F, CR (Brest et al., 2006)
    tau1, tau2 = 0.1, 0.1  # 自己適応確率
    F_arr = np.full(population_size, F)
    CR_arr = np.full(population_size, CR)

    for gen in range(max_iterations):
        for i in range(population_size):
            # jDE: F, CR の自己適応
            if adaptive:
                Fi = 0.1 + 0.9 * random.random() if random.random() < tau1 else F_arr[i]
                CRi = random.random() if random.random() < tau2 else CR_arr[i]
            else:
                Fi, CRi = F, CR

            # 突然変異: DE/rand/1
            candidates = [j for j in range(population_size) if j != i]
            a, b, c = random.sample(candidates, 3)
            mutant = pop[a] + Fi * (pop[b] - pop[c])

            # 交叉
            trial = np.copy(pop[i])
            j_rand = random.randint(0, n - 1)
            for j in range(n):
                if random.random() < CRi or j == j_rand:
                    trial[j] = mutant[j]
            # クランプ
            trial = np.clip(np.round(trial), 0, [float(m) for m in maxes])

            trial_obj = obj_fn(trial)
            if trial_obj <= pop_obj[i]:
                pop[i] = trial
                pop_obj[i] = trial_obj
                # 成功した F, CR を保持
                if adaptive:
                    F_arr[i] = Fi
                    CR_arr[i] = CRi

        step = _make_step(gen, best_solution or {},
                          best_result, action="eval",
                          note=f"DE gen={gen} best={best_obj:.1f}")
        history.append(step)
        if progress_cb:
            progress_cb(step)

    final_q = best_solution or {k: max_quantities.get(k, 0) for k in floor_keys}
    final_step = _make_step(max_iterations, final_q, best_result, action="final")
    history.append(final_step)
    if progress_cb:
        progress_cb(final_step)

    return MinimizationResult(
        strategy="de",
        initial_quantities={k: max_quantities.get(k, 0) for k in floor_keys},
        final_quantities=final_q,
        final_count=sum(final_q.values()),
        is_feasible=best_result.is_feasible,
        final_margin=best_result.worst_margin,
        history=history,
        evaluations=evaluations,
    )


# ---------------------------------------------------------------------------
# 9. SQP法
# ---------------------------------------------------------------------------


def minimize_sqp(
    floor_keys: List[str],
    max_quantities: Dict[str, int],
    evaluate_fn: EvaluateFn,
    progress_cb: Optional[ProgressCb] = None,
    penalty_weight: Optional[float] = None,
) -> MinimizationResult:
    """SQP法 (SLSQP) — 連続緩和+整数丸め。"""
    from scipy.optimize import minimize as sp_minimize

    if penalty_weight is None:
        penalty_weight = _auto_penalty_weight(max_quantities)
    n = len(floor_keys)
    maxes = [max_quantities.get(k, 10) for k in floor_keys]
    history: List[MinimizationStep] = []
    evaluations = 0
    best_solution: Optional[Dict[str, int]] = None
    best_obj = float("inf")
    best_result = EvaluationResult()

    def obj_fn(x):
        nonlocal evaluations, best_solution, best_obj, best_result
        q = {floor_keys[i]: max(0, min(maxes[i], int(round(x[i])))) for i in range(n)}
        result = evaluate_fn(q)
        evaluations += 1
        total = sum(q.values())
        penalty = 0.0 if result.is_feasible else penalty_weight * abs(result.worst_margin)
        obj = total + penalty

        step = _make_step(evaluations, q, result, action="eval")
        history.append(step)
        if progress_cb:
            progress_cb(step)

        if obj < best_obj:
            best_obj = obj
            best_solution = q
            best_result = result
        return obj

    bounds = [(0, m) for m in maxes]
    x0 = [m / 2.0 for m in maxes]

    try:
        sp_minimize(obj_fn, x0, method="SLSQP", bounds=bounds,
                    options={"maxiter": 100, "ftol": 0.1})
    except Exception as e:
        logger.warning("SQP最適化でエラー: %s", e)

    if best_solution is None:
        best_solution = {k: max_quantities.get(k, 0) for k in floor_keys}
        best_result = evaluate_fn(best_solution)
        evaluations += 1

    # 整数丸め後に最終検証
    final_result = evaluate_fn(best_solution)
    evaluations += 1
    final_step = _make_step(evaluations, best_solution, final_result, action="final")
    history.append(final_step)
    if progress_cb:
        progress_cb(final_step)

    return MinimizationResult(
        strategy="sqp",
        initial_quantities={k: max_quantities.get(k, 0) for k in floor_keys},
        final_quantities=best_solution,
        final_count=sum(best_solution.values()),
        is_feasible=final_result.is_feasible,
        final_margin=final_result.worst_margin,
        history=history,
        evaluations=evaluations,
    )


# ---------------------------------------------------------------------------
# 10. Nelder-Mead法
# ---------------------------------------------------------------------------


def minimize_nelder_mead(
    floor_keys: List[str],
    max_quantities: Dict[str, int],
    evaluate_fn: EvaluateFn,
    progress_cb: Optional[ProgressCb] = None,
    penalty_weight: Optional[float] = None,
) -> MinimizationResult:
    """Nelder-Mead法 — 微分不要シンプレックス、連続緩和+整数丸め。"""
    from scipy.optimize import minimize as sp_minimize

    if penalty_weight is None:
        penalty_weight = _auto_penalty_weight(max_quantities)
    n = len(floor_keys)
    maxes = [max_quantities.get(k, 10) for k in floor_keys]
    history: List[MinimizationStep] = []
    evaluations = 0
    best_solution: Optional[Dict[str, int]] = None
    best_obj = float("inf")
    best_result = EvaluationResult()

    def obj_fn(x):
        nonlocal evaluations, best_solution, best_obj, best_result
        q = {floor_keys[i]: max(0, min(maxes[i], int(round(x[i])))) for i in range(n)}
        result = evaluate_fn(q)
        evaluations += 1
        total = sum(q.values())
        penalty = 0.0 if result.is_feasible else penalty_weight * abs(result.worst_margin)
        obj = total + penalty

        if evaluations % 5 == 0:
            step = _make_step(evaluations, q, result, action="eval")
            history.append(step)
            if progress_cb:
                progress_cb(step)

        if obj < best_obj:
            best_obj = obj
            best_solution = q
            best_result = result
        return obj

    x0 = [m / 2.0 for m in maxes]

    try:
        sp_minimize(obj_fn, x0, method="Nelder-Mead",
                    options={"maxiter": 200, "xatol": 0.5, "fatol": 0.5})
    except Exception as e:
        logger.warning("Nelder-Mead最適化でエラー: %s", e)

    if best_solution is None:
        best_solution = {k: max_quantities.get(k, 0) for k in floor_keys}
        best_result = evaluate_fn(best_solution)
        evaluations += 1

    final_result = evaluate_fn(best_solution)
    evaluations += 1
    final_step = _make_step(evaluations, best_solution, final_result, action="final")
    history.append(final_step)
    if progress_cb:
        progress_cb(final_step)

    return MinimizationResult(
        strategy="nelder_mead",
        initial_quantities={k: max_quantities.get(k, 0) for k in floor_keys},
        final_quantities=best_solution,
        final_count=sum(best_solution.values()),
        is_feasible=final_result.is_feasible,
        final_margin=final_result.worst_margin,
        history=history,
        evaluations=evaluations,
    )


# ---------------------------------------------------------------------------
# 11. ベイズ最適化
# ---------------------------------------------------------------------------


def minimize_bayesian(
    floor_keys: List[str],
    max_quantities: Dict[str, int],
    evaluate_fn: EvaluateFn,
    progress_cb: Optional[ProgressCb] = None,
    max_iterations: int = 50,
    n_initial: int = 10,
    penalty_weight: Optional[float] = None,
) -> MinimizationResult:
    """ガウス過程回帰 + EI獲得関数。"""
    from scipy.stats import norm

    if penalty_weight is None:
        penalty_weight = _auto_penalty_weight(max_quantities)
    n = len(floor_keys)
    maxes = [max_quantities.get(k, 10) for k in floor_keys]
    history: List[MinimizationStep] = []
    evaluations = 0
    best_solution: Optional[Dict[str, int]] = None
    best_obj = float("inf")
    best_result = EvaluationResult()

    X_observed: List[List[int]] = []
    y_observed: List[float] = []

    def eval_point(arr):
        nonlocal evaluations, best_solution, best_obj, best_result
        q = {floor_keys[i]: max(0, min(maxes[i], int(arr[i]))) for i in range(n)}
        result = evaluate_fn(q)
        evaluations += 1
        total = sum(q.values())
        penalty = 0.0 if result.is_feasible else penalty_weight * abs(result.worst_margin)
        obj = total + penalty
        X_observed.append([int(arr[i]) for i in range(n)])
        y_observed.append(obj)
        if obj < best_obj:
            best_obj = obj
            best_solution = q
            best_result = result

        step = _make_step(evaluations, q, result, action="eval",
                          note=f"obj={obj:.1f}")
        history.append(step)
        if progress_cb:
            progress_cb(step)
        return obj

    # ランダム初期サンプル
    for _ in range(n_initial):
        x = [random.randint(0, m) for m in maxes]
        eval_point(x)

    # ベイズ反復
    for it in range(max_iterations - n_initial):
        X = np.array(X_observed, dtype=float)
        y = np.array(y_observed)

        # 簡易GP: RBFカーネル + 予測
        try:
            from sklearn.gaussian_process import GaussianProcessRegressor
            from sklearn.gaussian_process.kernels import Matern
            gp = GaussianProcessRegressor(kernel=Matern(nu=2.5), n_restarts_optimizer=2)
            gp.fit(X, y)

            # EI獲得関数をランダム候補で評価
            n_candidates = min(500, max(100, 10 ** n))
            candidates = np.array([[random.randint(0, m) for m in maxes]
                                   for _ in range(n_candidates)], dtype=float)
            mu, sigma = gp.predict(candidates, return_std=True)
            sigma = np.maximum(sigma, 1e-10)
            best_y = min(y_observed)
            z_val = (best_y - mu) / sigma
            ei = (best_y - mu) * norm.cdf(z_val) + sigma * norm.pdf(z_val)
            best_idx = np.argmax(ei)
            next_x = candidates[best_idx].astype(int).tolist()
        except ImportError:
            logger.warning("sklearn がインストールされていません。ランダムサンプリングにフォールバックします。")
            next_x = [random.randint(0, m) for m in maxes]
        except Exception as e:
            logger.debug("GP予測エラー（ランダムにフォールバック）: %s", e)
            next_x = [random.randint(0, m) for m in maxes]

        eval_point(next_x)

    if best_solution is None:
        best_solution = {k: max_quantities.get(k, 0) for k in floor_keys}
        best_result = evaluate_fn(best_solution)
        evaluations += 1

    final_step = _make_step(evaluations, best_solution, best_result, action="final")
    history.append(final_step)
    if progress_cb:
        progress_cb(final_step)

    return MinimizationResult(
        strategy="bayesian",
        initial_quantities={k: max_quantities.get(k, 0) for k in floor_keys},
        final_quantities=best_solution,
        final_count=sum(best_solution.values()),
        is_feasible=best_result.is_feasible,
        final_margin=best_result.worst_margin,
        history=history,
        evaluations=evaluations,
    )


# ---------------------------------------------------------------------------
# 12. ランダムサーチ
# ---------------------------------------------------------------------------


def minimize_random(
    floor_keys: List[str],
    max_quantities: Dict[str, int],
    evaluate_fn: EvaluateFn,
    progress_cb: Optional[ProgressCb] = None,
    max_iterations: int = 100,
    penalty_weight: Optional[float] = None,
) -> MinimizationResult:
    """ランダムサーチ（ベースライン）。"""
    if penalty_weight is None:
        penalty_weight = _auto_penalty_weight(max_quantities)
    n = len(floor_keys)
    maxes = [max_quantities.get(k, 10) for k in floor_keys]
    history: List[MinimizationStep] = []
    evaluations = 0
    best_solution: Optional[Dict[str, int]] = None
    best_obj = float("inf")
    best_result = EvaluationResult()

    for it in range(max_iterations):
        arr = [random.randint(0, m) for m in maxes]
        q = {floor_keys[i]: arr[i] for i in range(n)}
        result = evaluate_fn(q)
        evaluations += 1
        total = sum(q.values())
        penalty = 0.0 if result.is_feasible else penalty_weight * abs(result.worst_margin)
        obj = total + penalty

        if obj < best_obj:
            best_obj = obj
            best_solution = q
            best_result = result

        step = _make_step(it, q, result, action="eval",
                          note=f"obj={obj:.1f}")
        history.append(step)
        if progress_cb:
            progress_cb(step)

    if best_solution is None:
        best_solution = {k: 0 for k in floor_keys}
        best_result = EvaluationResult()

    final_step = _make_step(max_iterations, best_solution, best_result, action="final")
    history.append(final_step)
    if progress_cb:
        progress_cb(final_step)

    return MinimizationResult(
        strategy="random",
        initial_quantities={k: max_quantities.get(k, 0) for k in floor_keys},
        final_quantities=best_solution,
        final_count=sum(best_solution.values()),
        is_feasible=best_result.is_feasible,
        final_margin=best_result.worst_margin,
        history=history,
        evaluations=evaluations,
    )


# ---------------------------------------------------------------------------
# 統一エントリーポイント
# ---------------------------------------------------------------------------


# アルゴリズム定義
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


def minimize_damper_count(
    floor_keys: List[str],
    max_quantities: Dict[str, int],
    evaluate_fn: EvaluateFn,
    strategy: str = "floor_add",
    initial_quantities: Optional[Dict[str, int]] = None,
    progress_cb: Optional[ProgressCb] = None,
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
    """
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
