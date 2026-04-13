"""
app/services/optimizer_search.py
最適化探索アルゴリズム群。

optimizer.py から分離された _OptimizationWorker の探索手法メソッド:
- _run_grid_search: グリッドサーチ（全組合せ探索）
- _run_random_search: ランダムサーチ（モンテカルロ）
- _run_lhs_search: ラテン超方格サンプリング
- _run_bayesian_search: ベイズ最適化（GP + 獲得関数）
- _run_ga_search: 遺伝的アルゴリズム
- _run_sa_search: 焼きなまし法
- _run_de_search: 差分進化
- _run_nsga2_search: NSGA-II（多目的最適化）
- _latin_hypercube_sample: LHSサンプリングユーティリティ
"""

from __future__ import annotations

import itertools
import logging
import math
import random
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

import numpy as np

if TYPE_CHECKING:
    from .optimizer import OptimizationCandidate, OptimizationConfig, OptimizationResult

logger = logging.getLogger(__name__)


def _ensure_imports():
    """遅延インポート: 循環参照を回避するためランタイムでインポートする。

    初回呼び出し時にモジュールグローバルに必要なクラス・関数を設定する。
    """
    global OptimizationResult, OptimizationCandidate, OptimizationConfig
    global _GaussianProcessRegressor, _compute_acquisition
    if "OptimizationResult" not in globals():
        from .optimizer import (
            OptimizationCandidate as _OC,
            OptimizationConfig as _OCfg,
            OptimizationResult as _OR,
            _GaussianProcessRegressor as _GPR,
            _compute_acquisition as _CA,
        )
        OptimizationResult = _OR
        OptimizationCandidate = _OC
        OptimizationConfig = _OCfg
        _GaussianProcessRegressor = _GPR
        _compute_acquisition = _CA


class _SearchAlgorithmsMixin:
    """探索アルゴリズムを _OptimizationWorker に提供するミックスインクラス。

    _OptimizationWorker が QThread とともにこのミックスインを継承します。
    各メソッドは self._cancelled, self._evaluate_fn, self.progress,
    self.candidate_found, self._check_constraints, self._penalized_objective,
    self._evaluate_batch, self._maybe_checkpoint を使用します。

    使用前に _ensure_imports() を呼び出すこと（run()メソッド冒頭で実行）。
    """

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

        ガウス過程回帰（GP）と獲得関数（EI/PI/UCB）を使用して
        効率的にパラメータ空間を探索します。

        戦略:
          1. ウォームスタート: 既存候補を初期データとして注入
          2. 初期探索フェーズ（~10点）: ランダムサンプリング
          3. ベイズフェーズ: GP学習 → 獲得関数評価 → 最良点選択 → 評価
        """
        if not config.parameters:
            return OptimizationResult(message="探索パラメータが設定されていません。")

        total = config.max_iterations
        all_candidates: List[OptimizationCandidate] = []
        best: Optional[OptimizationCandidate] = None

        param_keys = [pr.key for pr in config.parameters]
        param_mins = np.array([pr.min_val for pr in config.parameters])
        param_ranges = np.array([pr.max_val - pr.min_val for pr in config.parameters])

        # Phase 0: ウォームスタート
        X_init, y_init, warm_count, best = self._bayesian_warmstart(
            config, param_keys, param_mins, param_ranges, all_candidates, total,
        )

        # Phase 1: 初期ランダム探索
        n_init = max(0, min(10, max(10, total // 10)) - warm_count)
        best = self._bayesian_init_phase(
            config, param_keys, param_mins, param_ranges,
            X_init, y_init, all_candidates, best,
            n_init, warm_count, total,
        )

        # Phase 2: ベイズ最適化
        n_bayesian = total - n_init - warm_count
        best = self._bayesian_gp_phase(
            config, param_keys, param_mins, param_ranges,
            X_init, y_init, all_candidates, best,
            n_init, n_bayesian, total,
        )

        result = OptimizationResult(
            best=best,
            all_candidates=all_candidates,
            converged=True,
            message=f"ベイズ最適化完了: {len(all_candidates)} 点を評価（初期:{n_init}点+ベイズ:{len(all_candidates)-n_init}点）" +
                    (f", 制約満足 {len([c for c in all_candidates if c.is_feasible])} 点"
                     if config.constraints or config.criteria else ""),
        )
        return result

    def _bayesian_warmstart(
        self,
        config: OptimizationConfig,
        param_keys: List[str],
        param_mins: np.ndarray,
        param_ranges: np.ndarray,
        all_candidates: List[OptimizationCandidate],
        total: int,
    ) -> Tuple[List[np.ndarray], List[float], int, Optional[OptimizationCandidate]]:
        """ウォームスタート候補を正規化して初期データに注入する。"""
        X_init: List[np.ndarray] = []
        y_init: List[float] = []
        best: Optional[OptimizationCandidate] = None

        if not config.warm_start_candidates:
            return X_init, y_init, 0, best

        for wc in config.warm_start_candidates:
            if all(k in wc.params for k in param_keys):
                raw = np.array([wc.params[k] for k in param_keys])
                x_norm = (raw - param_mins) / np.where(param_ranges == 0, 1.0, param_ranges)
                X_init.append(x_norm)
                y_init.append(wc.objective_value)
                all_candidates.append(wc)
                self.candidate_found.emit(wc)
                if wc.is_feasible and (best is None or wc.objective_value < best.objective_value):
                    best = wc

        warm_count = len(X_init)
        if warm_count > 0:
            self.progress.emit(warm_count, total,
                               f"ウォームスタート: {warm_count}点を引き継ぎ")
        return X_init, y_init, warm_count, best

    def _bayesian_init_phase(
        self,
        config: OptimizationConfig,
        param_keys: List[str],
        param_mins: np.ndarray,
        param_ranges: np.ndarray,
        X_init: List[np.ndarray],
        y_init: List[float],
        all_candidates: List[OptimizationCandidate],
        best: Optional[OptimizationCandidate],
        n_init: int,
        warm_count: int,
        total: int,
    ) -> Optional[OptimizationCandidate]:
        """初期ランダム探索フェーズを実行する。"""
        for i in range(n_init):
            if self._cancelled:
                break

            params = {pr.key: pr.random_value() for pr in config.parameters}
            raw_params = np.array([params[k] for k in param_keys])
            x_normalized = (raw_params - param_mins) / param_ranges
            X_init.append(x_normalized)

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

            if i % max(1, n_init // 10) == 0 or i == n_init - 1:
                msg = f"初期探索: {i+1}/{n_init}"
                if warm_count > 0:
                    msg += f" (+ ウォーム{warm_count}点)"
                if best:
                    msg += f" | 暫定最良: {best.objective_value:.6g}"
                self.progress.emit(warm_count + i + 1, total, msg)

        return best

    def _bayesian_gp_phase(
        self,
        config: OptimizationConfig,
        param_keys: List[str],
        param_mins: np.ndarray,
        param_ranges: np.ndarray,
        X_init: List[np.ndarray],
        y_init: List[float],
        all_candidates: List[OptimizationCandidate],
        best: Optional[OptimizationCandidate],
        n_init: int,
        n_bayesian: int,
        total: int,
    ) -> Optional[OptimizationCandidate]:
        """GP + 獲得関数によるベイズ最適化フェーズを実行する。"""
        if len(X_init) == 0 or n_bayesian <= 0:
            return best

        try:
            X_history = np.array(X_init)
            y_history = np.array(y_init)
            gp = _GaussianProcessRegressor(length_scale=1.0, noise=1e-6)

            for i in range(n_bayesian):
                if self._cancelled:
                    break

                gp.fit(X_history, y_history)

                n_candidates = min(500, max(100, total * 2))
                X_candidates = np.random.uniform(0, 1, (n_candidates, len(param_keys)))
                mu, sigma = gp.predict(X_candidates)
                y_best = float(np.min(y_history))

                acq_values = _compute_acquisition(
                    config.acquisition_function,
                    mu, sigma, y_best,
                    xi=0.01,
                    kappa=config.acquisition_kappa,
                )

                best_idx = int(np.argmax(acq_values))
                x_next = X_candidates[best_idx].copy()
                raw_params = x_next * param_ranges + param_mins

                params = {}
                for j, key in enumerate(param_keys):
                    val = raw_params[j]
                    pr = config.parameters[j]
                    if pr.is_integer:
                        val = round(val)
                    elif pr.step > 0:
                        val = round(val / pr.step) * pr.step
                    params[key] = val

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

                y_penalized = self._penalized_objective(obj_val, margins, config)
                x_next_normalized = (raw_params - param_mins) / param_ranges
                X_history = np.vstack([X_history, x_next_normalized])
                y_history = np.hstack([y_history, y_penalized])

                if (n_init + i) % max(1, total // 100) == 0 or (n_init + i) == total - 1:
                    msg = f"ベイズ探索: {n_init + i + 1}/{total}"
                    if best:
                        msg += f" | 暫定最良: {best.objective_value:.6g}"
                    self.progress.emit(n_init + i + 1, total, msg)

                self._maybe_checkpoint(all_candidates, best, config)

        except Exception as e:
            logger.warning("Bayesian optimization failed (%s), falling back to random search", e)
            best = self._bayesian_fallback_random(
                config, all_candidates, best, n_init, n_bayesian, total,
            )

        return best

    def _bayesian_fallback_random(
        self,
        config: OptimizationConfig,
        all_candidates: List[OptimizationCandidate],
        best: Optional[OptimizationCandidate],
        n_init: int,
        n_bayesian: int,
        total: int,
    ) -> Optional[OptimizationCandidate]:
        """ベイズ最適化失敗時のランダムサーチフォールバック。"""
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

            self._maybe_checkpoint(all_candidates, best, config)

        return best

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
    # 差分進化 (Differential Evolution)
    # ------------------------------------------------------------------

    def _run_de_search(self, config: OptimizationConfig) -> OptimizationResult:
        """
        差分進化 (DE/rand/1/bin) で最適化を実行します。

        Storn & Price (1997) のアルゴリズムに基づく。
        連続パラメータ空間で高い探索能力を持ち、GAより少ないチューニングで
        安定した性能を発揮する。

        突然変異: DE/rand/1 (ランダム3個体から差分ベクトルを生成)
        交叉: 二項交叉 (binomial crossover)
        自己適応: jDE (Brest et al., 2006) — 個体ごとにF, CRを適応
        """
        if not config.parameters:
            return OptimizationResult(message="探索パラメータが設定されていません。")

        n_params = len(config.parameters)
        # 集団サイズ: DEは5*D〜10*D が標準的な指針
        pop_size = max(20, min(100, config.max_iterations // 5))
        pop_size = max(pop_size, min(100, 7 * n_params))
        n_generations = max(1, config.max_iterations // pop_size)

        # jDE自己適応の初期値
        F_init = 0.5   # スケーリング因子
        CR_init = 0.9  # 交叉率
        tau1, tau2 = 0.1, 0.1  # 自己適応確率

        all_candidates: List[OptimizationCandidate] = []
        best: Optional[OptimizationCandidate] = None
        total = pop_size * n_generations
        stagnation_limit = max(5, n_generations // 4)
        no_improve_gens = 0
        n_restarts = 0
        restart_limit = 2  # 最大リスタート回数
        diversity_threshold = 0.01  # 集団多様性の最低閾値

        def _decode(vec: np.ndarray) -> Dict[str, float]:
            params = {}
            for j, pr in enumerate(config.parameters):
                val = pr.min_val + vec[j] * (pr.max_val - pr.min_val)
                if pr.is_integer:
                    val = round(val)
                elif pr.step > 0:
                    val = round(val / pr.step) * pr.step
                val = max(pr.min_val, min(pr.max_val, val))
                params[pr.key] = val
            return params

        def _evaluate_vec(vec: np.ndarray, iteration: int) -> OptimizationCandidate:
            params = _decode(vec)
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

        # 初期集団生成（LHS）
        population = self._latin_hypercube_sample(pop_size, n_params)
        pop_candidates: List[Optional[OptimizationCandidate]] = [None] * pop_size

        # 個体別 F, CR (jDE)
        F_arr = np.full(pop_size, F_init)
        CR_arr = np.full(pop_size, CR_init)

        # ウォームスタート
        warm_injected = 0
        if config.warm_start_candidates:
            warm_sorted = sorted(
                [wc for wc in config.warm_start_candidates
                 if all(k in wc.params for k in [pr.key for pr in config.parameters])],
                key=lambda c: c.objective_value if c.is_feasible else float("inf"),
            )
            for wc in warm_sorted[:pop_size // 2]:
                chromo = np.array([
                    (wc.params[pr.key] - pr.min_val) / max(pr.max_val - pr.min_val, 1e-12)
                    for pr in config.parameters
                ])
                chromo = np.clip(chromo, 0.0, 1.0)
                population[warm_injected] = chromo
                warm_injected += 1

        # 初期集団の評価
        for i in range(pop_size):
            if self._cancelled:
                break
            cand = _evaluate_vec(population[i], i)
            pop_candidates[i] = cand
            all_candidates.append(cand)
            self.candidate_found.emit(cand)
            if best is None or _fitness(cand) < _fitness(best):
                best = cand

        warm_msg = f" (ウォーム{warm_injected}個体)" if warm_injected > 0 else ""
        self.progress.emit(pop_size, total, f"DE: 初期集団評価完了 ({pop_size}個体{warm_msg})")

        # 世代ループ
        best_before_gen = best
        for gen in range(1, n_generations):
            if self._cancelled:
                break

            for i in range(pop_size):
                if self._cancelled:
                    break

                # jDE: F, CR の自己適応
                if random.random() < tau1:
                    F_i = random.uniform(0.1, 1.0)
                else:
                    F_i = F_arr[i]
                if random.random() < tau2:
                    CR_i = random.random()
                else:
                    CR_i = CR_arr[i]

                # DE/rand/1 突然変異: v = x_r1 + F * (x_r2 - x_r3)
                idxs = list(range(pop_size))
                idxs.remove(i)
                r1, r2, r3 = random.sample(idxs, 3)
                mutant = population[r1] + F_i * (population[r2] - population[r3])
                mutant = np.clip(mutant, 0.0, 1.0)

                # 二項交叉 (binomial crossover)
                j_rand = random.randint(0, n_params - 1)
                trial = np.copy(population[i])
                for j in range(n_params):
                    if random.random() < CR_i or j == j_rand:
                        trial[j] = mutant[j]

                # 選択 (greedy selection)
                iteration = gen * pop_size + i
                trial_cand = _evaluate_vec(trial, iteration)
                all_candidates.append(trial_cand)
                self.candidate_found.emit(trial_cand)

                if _fitness(trial_cand) <= _fitness(pop_candidates[i]):
                    population[i] = trial
                    pop_candidates[i] = trial_cand
                    F_arr[i] = F_i
                    CR_arr[i] = CR_i

                if best is None or _fitness(trial_cand) < _fitness(best):
                    best = trial_cand

            # 集団多様性: 各次元の標準偏差の平均（[0,1]正規化空間）
            diversity = float(np.mean(np.std(population, axis=0)))

            # 停滞検出
            if (best is not None and best_before_gen is not None
                    and best.objective_value < best_before_gen.objective_value):
                no_improve_gens = 0
            else:
                no_improve_gens += 1
            best_before_gen = best

            msg = f"DE: 世代 {gen+1}/{n_generations}"
            if best:
                msg += f" | 最良: {best.objective_value:.6g}"
            msg += f" | 多様性: {diversity:.4f}"
            self.progress.emit(min((gen + 1) * pop_size, total), total, msg)

            self._maybe_checkpoint(all_candidates, best, config)

            # 多様性喪失時のリスタート: 集団の下位半分を再初期化
            if (diversity < diversity_threshold
                    and n_restarts < restart_limit
                    and gen < n_generations - 2):
                n_restarts += 1
                logger.info(
                    "DE: 多様性低下 (%.4f < %.4f) — リスタート %d/%d",
                    diversity, diversity_threshold, n_restarts, restart_limit,
                )
                # 適応度でソートし、上位半分を保持
                ranked = sorted(range(pop_size),
                                key=lambda k: _fitness(pop_candidates[k])
                                if pop_candidates[k] else float("inf"))
                n_keep = pop_size // 2
                new_pop = self._latin_hypercube_sample(
                    pop_size - n_keep, n_params,
                )
                for idx_new, idx_old in enumerate(ranked[n_keep:]):
                    population[idx_old] = new_pop[idx_new]
                    pop_candidates[idx_old] = None  # 次世代で再評価
                    F_arr[idx_old] = F_init
                    CR_arr[idx_old] = CR_init
                # リスタート後の再評価
                for idx_old in ranked[n_keep:]:
                    if self._cancelled:
                        break
                    cand = _evaluate_vec(population[idx_old],
                                        gen * pop_size + idx_old)
                    pop_candidates[idx_old] = cand
                    all_candidates.append(cand)
                    self.candidate_found.emit(cand)
                    if best is None or _fitness(cand) < _fitness(best):
                        best = cand
                no_improve_gens = 0  # リスタート後はカウントリセット
                continue

            if no_improve_gens >= stagnation_limit:
                logger.info("DE: %d世代連続で改善なし — 早期終了", no_improve_gens)
                break

        actual_gens = gen + 1 if n_generations > 1 else 1
        early_stopped = no_improve_gens >= stagnation_limit
        restart_msg = f", リスタート{n_restarts}回" if n_restarts > 0 else ""
        return OptimizationResult(
            best=best,
            all_candidates=all_candidates,
            converged=early_stopped,
            message=f"差分進化完了: {actual_gens}世代×{pop_size}個体 = {len(all_candidates)}点評価" +
                    restart_msg +
                    (f" (早期収束: {no_improve_gens}世代改善なし)" if early_stopped else "") +
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


