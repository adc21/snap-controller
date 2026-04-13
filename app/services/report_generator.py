"""
app/services/report_generator.py
解析結果 HTML レポート生成サービス。

プロジェクトの全ケース（または選択ケース）の解析結果をまとめた
HTML レポートを自動生成します。

生成内容:
  - プロジェクト概要（モデル情報、基準、ケース数）
  - ケース毎の結果サマリーテーブル
  - 各応答値の層別チャート（SVG / base64 PNG）
  - ケース比較チャート
  - 性能基準判定結果
  - 最適化結果（実行した場合）

出力形式: 自己完結型 HTML（外部依存なし・印刷対応 CSS 付き）
"""

from __future__ import annotations

import base64
import io
import html as _html
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from app.models.analysis_case import AnalysisCase, AnalysisCaseStatus
from app.models.performance_criteria import PerformanceCriteria
from app.models.project import Project

# matplotlib は遅延インポート (headless 環境対応)
_MPL_AVAILABLE = False
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    import numpy as np
    _MPL_AVAILABLE = True
except ImportError:
    pass

# Period.xbn リーダー (固有値解析)
_PERIOD_READER_AVAILABLE = False
try:
    from controller.binary.period_xbn_reader import PeriodXbnReader, ModeInfo
    _PERIOD_READER_AVAILABLE = True
except ImportError:
    PeriodXbnReader = None  # type: ignore
    ModeInfo = None  # type: ignore


# ---------------------------------------------------------------------------
# 応答値のメタ情報
# ---------------------------------------------------------------------------

RESPONSE_ITEMS = [
    ("max_disp",        "最大相対変位",        "m"),
    ("max_vel",         "最大相対速度",        "m/s"),
    ("max_acc",         "最大絶対加速度",      "m/s²"),
    ("max_story_disp",  "最大層間変形",        "m"),
    ("max_story_drift", "最大層間変形角",      "rad"),
    ("shear_coeff",     "せん断力係数",        "—"),
    ("max_otm",         "最大転倒モーメント",  "kN·m"),
]


# ---------------------------------------------------------------------------
# 公開 API
# ---------------------------------------------------------------------------

def generate_report(
    project: Project,
    cases: Optional[List[AnalysisCase]] = None,
    output_path: Optional[str] = None,
    include_charts: bool = True,
    title: Optional[str] = None,
) -> str:
    """
    解析レポートを HTML として生成します。

    Parameters
    ----------
    project : Project
        レポート対象のプロジェクト。
    cases : list of AnalysisCase, optional
        対象ケース。None の場合は全完了ケース。
    output_path : str, optional
        出力先ファイルパス。None の場合は HTML 文字列のみ返却。
    include_charts : bool
        チャート画像を含めるかどうか (matplotlib 必須)。
    title : str, optional
        レポートタイトル。None の場合はプロジェクト名。

    Returns
    -------
    str
        生成された HTML 文字列。
    """
    if cases is None:
        cases = project.get_completed_cases()

    report_title = title or f"{project.name} — 解析レポート"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    sections: List[str] = []
    sections.append(_build_header(report_title, now))
    sections.append(_build_project_summary(project, cases))
    sections.append(_build_result_table(cases, project.criteria))

    if _PERIOD_READER_AVAILABLE and cases:
        modal_section = _build_modal_analysis_section(
            cases, include_charts and _MPL_AVAILABLE
        )
        if modal_section:
            sections.append(modal_section)

    if include_charts and _MPL_AVAILABLE and cases:
        sections.append(_build_chart_section(cases, project.criteria))

    sections.append(_build_criteria_verdict(cases, project.criteria))
    sections.append(_build_footer(now))

    html_content = _wrap_html(report_title, "\n".join(sections))

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(html_content, encoding="utf-8")

    return html_content


def generate_optimization_report(
    result: "OptimizationResult",
    output_path: Optional[str] = None,
    include_charts: bool = True,
    title: Optional[str] = None,
) -> str:
    """
    最適化結果を HTML レポートとして生成します。

    Parameters
    ----------
    result : OptimizationResult
        最適化結果オブジェクト。
    output_path : str, optional
        出力先ファイルパス。None の場合は HTML 文字列のみ返却。
    include_charts : bool
        チャート画像を含めるかどうか (matplotlib 必須)。
    title : str, optional
        レポートタイトル。None の場合はデフォルトタイトル。

    Returns
    -------
    str
        生成された HTML 文字列。
    """
    from app.services.optimizer import OptimizationResult, OptimizationCandidate  # noqa: F401

    report_title = title or "ダンパー最適化レポート"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    sections: List[str] = []
    sections.append(_build_header(report_title, now))
    sections.append(_build_opt_config_summary(result))
    sections.append(_build_opt_best_solution(result))
    sections.append(_build_opt_ranking_table(result))

    if include_charts and _MPL_AVAILABLE:
        conv_chart = _build_opt_convergence_chart(result)
        if conv_chart:
            sections.append(conv_chart)
        space_chart = _build_opt_parameter_space_chart(result)
        if space_chart:
            sections.append(space_chart)

    sections.append(_build_footer(now))

    html_content = _wrap_html(report_title, "\n".join(sections))

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(html_content, encoding="utf-8")

    return html_content


# ---------------------------------------------------------------------------
# 最適化レポート セクションビルダー
# ---------------------------------------------------------------------------

def _build_opt_config_summary(result: Any) -> str:
    """設定概要セクション。"""
    cfg = result.config
    elapsed = getattr(result, "elapsed_sec", None)
    converged = getattr(result, "converged", None)
    message = getattr(result, "message", None)
    eval_method = getattr(result, "evaluation_method", None)
    evaluator_stats = getattr(result, "evaluator_stats", None)

    all_candidates = getattr(result, "all_candidates", []) or []
    num_evaluations = len(all_candidates)
    num_feasible = sum(1 for c in all_candidates if getattr(c, "is_feasible", True))

    penalty = getattr(cfg, "constraint_penalty_weight", 0) or 0

    method_map = {
        "genetic_algorithm": "遺伝的アルゴリズム (GA)",
        "simulated_annealing": "焼きなまし法 (SA)",
        "grid_search": "グリッドサーチ",
        "random_search": "ランダムサーチ",
        "bayesian": "ベイズ最適化",
    }
    method_str = _esc(method_map.get(str(getattr(cfg, "method", "") or ""), str(getattr(cfg, "method", "") or "—")))

    obj_label = _esc(str(getattr(cfg, "objective_label", None) or getattr(cfg, "objective_key", "") or "—"))
    damper_type = _esc(str(getattr(cfg, "damper_type", "") or "—"))
    eval_method_str = _esc(str(eval_method or "—"))

    rows = [
        ("<th>目的関数</th>", f"<td>{obj_label}</td>"),
        ("<th>最適化手法</th>", f"<td>{method_str}</td>"),
        ("<th>ダンパー種別</th>", f"<td>{damper_type}</td>"),
        ("<th>評価方法</th>", f"<td>{eval_method_str}</td>"),
    ]

    if elapsed is not None:
        rows.append(("<th>計算時間</th>", f"<td>{elapsed:.1f} 秒</td>"))

    rows.append(("<th>評価回数</th>", f"<td>{num_evaluations}</td>"))
    rows.append(("<th>実行可能解数</th>", f"<td>{num_feasible} / {num_evaluations}</td>"))

    if converged is not None:
        conv_str = "収束" if converged else "未収束"
        rows.append(("<th>収束状態</th>", f"<td>{_esc(conv_str)}</td>"))

    if message:
        rows.append(("<th>メッセージ</th>", f"<td>{_esc(str(message))}</td>"))

    if penalty > 0:
        rows.append(("<th>制約ペナルティ重み</th>", f"<td>{penalty:.4g}</td>"))

    # 複合目的関数の重み
    obj_weights = getattr(cfg, "objective_weights", None)
    if obj_weights and isinstance(obj_weights, dict):
        weight_parts = [f"{_esc(str(k))}: {v:.4g}" for k, v in obj_weights.items()]
        rows.append(("<th>目的関数重み</th>", f"<td>{', '.join(weight_parts)}</td>"))

    # SNAP 評価統計
    if evaluator_stats and isinstance(evaluator_stats, dict):
        stats_parts = [f"{_esc(str(k))}: {_esc(str(v))}" for k, v in evaluator_stats.items()]
        rows.append(("<th>評価統計 (SNAP)</th>", f"<td>{'<br>'.join(stats_parts)}</td>"))

    table_rows_html = "\n".join(f"<tr>{th}{td}</tr>" for th, td in rows)

    return f"""
    <section class="summary-section">
        <h2>設定概要</h2>
        <table class="info-table">
            {table_rows_html}
        </table>
    </section>
    """


def _build_opt_best_solution(result: Any) -> str:
    """最良解カードセクション。"""
    best = getattr(result, "best", None)
    if best is None:
        return '<section><h2>最良解</h2><p>最良解が見つかりませんでした。</p></section>'

    cfg = result.config
    params = getattr(best, "params", {}) or {}
    obj_val = getattr(best, "objective_value", None)
    response_values = getattr(best, "response_values", {}) or {}
    constraint_margins = getattr(best, "constraint_margins", {}) or {}
    is_feasible = getattr(best, "is_feasible", True)

    # パラメータ名ラベルマップ
    param_label_map: Dict[str, str] = {}
    for pr in (getattr(cfg, "parameters", None) or []):
        param_label_map[getattr(pr, "key", "")] = getattr(pr, "label", "") or getattr(pr, "key", "")

    feasible_badge = (
        "<span class='badge badge-pass'>実行可能</span>"
        if is_feasible
        else "<span class='badge badge-fail'>制約違反</span>"
    )

    obj_label = _esc(str(getattr(cfg, "objective_label", None) or getattr(cfg, "objective_key", "") or "目的関数"))

    # パラメータ行
    param_rows = []
    for k, v in params.items():
        label = _esc(param_label_map.get(k, k))
        val_str = f"{v:.4g}" if isinstance(v, float) else _esc(str(v))
        param_rows.append(f"<tr><th>{label}</th><td>{val_str}</td></tr>")

    obj_str = f"{obj_val:.6g}" if obj_val is not None else "N/A"

    # 応答値行
    resp_rows = []
    for k, v in response_values.items():
        label = _esc(str(k))
        val_str = _format_value(v, k)
        resp_rows.append(f"<tr><th>{label}</th><td>{val_str}</td></tr>")

    # 制約マージン行
    margin_rows = []
    for k, v in constraint_margins.items():
        label = _esc(str(k))
        if isinstance(v, float):
            margin_str = f"{v:.4g}"
            cls = "pass" if v >= 0 else "fail"
        else:
            margin_str = _esc(str(v))
            cls = ""
        margin_rows.append(f"<tr><th>{label}</th><td class='{cls}'>{margin_str}</td></tr>")

    params_table = f"""
    <table class="info-table">
        {''.join(param_rows)}
        <tr><th>{obj_label}</th><td><strong>{_esc(obj_str)}</strong></td></tr>
    </table>
    """ if param_rows else ""

    resp_table = f"""
    <h3>応答値</h3>
    <table class="info-table">{''.join(resp_rows)}</table>
    """ if resp_rows else ""

    margin_table = f"""
    <h3>制約マージン</h3>
    <table class="info-table">{''.join(margin_rows)}</table>
    """ if margin_rows else ""

    return f"""
    <section class="opt-best-section">
        <h2>最良解</h2>
        <div class="opt-best-card">
            <div class="opt-best-header">
                {feasible_badge}
                <span class="opt-best-obj">{obj_label}: <strong>{_esc(obj_str)}</strong></span>
            </div>
            <div class="opt-best-body">
                <div class="opt-best-col">
                    <h3>パラメータ</h3>
                    {params_table}
                </div>
                <div class="opt-best-col">
                    {resp_table}
                    {margin_table}
                </div>
            </div>
        </div>
    </section>
    """


def _build_opt_ranking_table(result: Any) -> str:
    """候補ランキングテーブルセクション (上位20件)。"""
    all_candidates = getattr(result, "all_candidates", []) or []
    if not all_candidates:
        return '<section><h2>候補ランキング</h2><p>候補がありません。</p></section>'

    cfg = result.config

    # パラメータ名ラベルマップ
    param_label_map: Dict[str, str] = {}
    param_keys: List[str] = []
    for pr in (getattr(cfg, "parameters", None) or []):
        k = getattr(pr, "key", "")
        param_keys.append(k)
        param_label_map[k] = getattr(pr, "label", "") or k

    # 全パラメータキーを収集 (config にない場合のフォールバック)
    if not param_keys and all_candidates:
        first_params = getattr(all_candidates[0], "params", {}) or {}
        param_keys = list(first_params.keys())

    # 応答値キー収集
    resp_keys: List[str] = []
    for c in all_candidates[:10]:
        rv = getattr(c, "response_values", {}) or {}
        for k in rv:
            if k not in resp_keys:
                resp_keys.append(k)

    obj_label = _esc(str(getattr(cfg, "objective_label", None) or getattr(cfg, "objective_key", "") or "目的関数"))

    # ソート: 実行可能解を優先、次に目的関数値
    def _sort_key(c: Any):
        feasible = getattr(c, "is_feasible", True)
        obj = getattr(c, "objective_value", None)
        obj_v = float(obj) if obj is not None else float("inf")
        return (0 if feasible else 1, obj_v)

    sorted_candidates = sorted(all_candidates, key=_sort_key)
    top20 = sorted_candidates[:20]

    # ヘッダ
    param_headers = "".join(
        f"<th>{_esc(param_label_map.get(k, k))}</th>" for k in param_keys
    )
    resp_headers = "".join(f"<th>{_esc(k)}</th>" for k in resp_keys)

    header_row = f"<th>順位</th>{param_headers}<th>{obj_label}</th><th>判定</th>{resp_headers}"

    # データ行
    rows_html = []
    for rank, c in enumerate(top20, 1):
        params = getattr(c, "params", {}) or {}
        obj_val = getattr(c, "objective_value", None)
        rv = getattr(c, "response_values", {}) or {}
        is_feasible = getattr(c, "is_feasible", True)

        obj_str = f"{obj_val:.6g}" if obj_val is not None else "N/A"
        verdict = (
            "<span class='badge badge-pass'>○</span>"
            if is_feasible
            else "<span class='badge badge-fail'>✗</span>"
        )

        param_cells = "".join(
            f"<td>{f'{params.get(k):.4g}' if isinstance(params.get(k), float) else _esc(str(params.get(k, '—')))}</td>"
            for k in param_keys
        )
        resp_cells = "".join(f"<td>{_format_value(rv.get(k), k)}</td>" for k in resp_keys)

        row_cls = "" if is_feasible else " class='infeasible-row'"
        rows_html.append(
            f"<tr{row_cls}>"
            f"<td>{rank}</td>{param_cells}<td><strong>{_esc(obj_str)}</strong></td>"
            f"<td>{verdict}</td>{resp_cells}"
            f"</tr>"
        )

    return f"""
    <section class="opt-ranking-section">
        <h2>候補ランキング (上位20件)</h2>
        <div class="table-wrapper">
        <table class="result-table">
            <thead><tr>{header_row}</tr></thead>
            <tbody>{''.join(rows_html)}</tbody>
        </table>
        </div>
    </section>
    """


def _build_opt_convergence_chart(result: Any) -> Optional[str]:
    """収束グラフセクション。"""
    if not _MPL_AVAILABLE:
        return None

    all_candidates = getattr(result, "all_candidates", []) or []
    if not all_candidates:
        return None

    iterations: List[float] = []
    obj_vals: List[float] = []
    feasibles: List[bool] = []

    for c in all_candidates:
        it = getattr(c, "iteration", None)
        obj = getattr(c, "objective_value", None)
        feas = getattr(c, "is_feasible", True)
        if it is not None and obj is not None:
            try:
                iterations.append(float(it))
                obj_vals.append(float(obj))
                feasibles.append(bool(feas))
            except (ValueError, TypeError):
                continue

    if not iterations:
        return None

    fig, ax = plt.subplots(figsize=(9, 4), dpi=120)

    # 実行不可能解 (赤 x)
    ix_inf = [it for it, f in zip(iterations, feasibles) if not f]
    ov_inf = [ov for ov, f in zip(obj_vals, feasibles) if not f]
    if ix_inf:
        ax.scatter(ix_inf, ov_inf, c="#e74c3c", marker="x", s=20, alpha=0.6,
                   linewidths=0.8, label="制約違反", zorder=2)

    # 実行可能解 (青 o)
    ix_feas = [it for it, f in zip(iterations, feasibles) if f]
    ov_feas = [ov for ov, f in zip(obj_vals, feasibles) if f]
    if ix_feas:
        ax.scatter(ix_feas, ov_feas, c="#3498db", marker="o", s=18, alpha=0.7,
                   label="実行可能", zorder=3)

    # 累積ベスト (橙線): 実行可能解のみで追跡
    if ix_feas:
        sorted_feas = sorted(zip(ix_feas, ov_feas), key=lambda x: x[0])
        best_it, best_ov = [], []
        current_best = float("inf")
        for it, ov in sorted_feas:
            if ov < current_best:
                current_best = ov
            best_it.append(it)
            best_ov.append(current_best)
        ax.plot(best_it, best_ov, c="#e67e22", linewidth=1.8, label="累積ベスト", zorder=4)

    obj_label = str(getattr(result.config, "objective_label", None) or
                    getattr(result.config, "objective_key", "") or "目的関数値")
    ax.set_xlabel("イテレーション", fontsize=9)
    ax.set_ylabel(_esc(obj_label), fontsize=9)
    ax.set_title("収束グラフ", fontsize=11, fontweight="bold")
    ax.tick_params(labelsize=8)
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    img_data = base64.b64encode(buf.read()).decode("ascii")

    return f"""
    <section class="opt-chart-section">
        <h2>収束グラフ</h2>
        <div class="chart-container" style="max-width:800px;">
            <img src="data:image/png;base64,{img_data}" alt="収束グラフ">
        </div>
    </section>
    """


def _build_opt_parameter_space_chart(result: Any) -> Optional[str]:
    """パラメータ空間探索グラフセクション。"""
    if not _MPL_AVAILABLE:
        return None

    all_candidates = getattr(result, "all_candidates", []) or []
    if not all_candidates:
        return None

    cfg = result.config
    param_ranges = getattr(cfg, "parameters", None) or []
    param_keys = [getattr(pr, "key", "") for pr in param_ranges]
    param_labels = [getattr(pr, "label", None) or getattr(pr, "key", "") for pr in param_ranges]

    # フォールバック: config にパラメータ定義がない場合
    if not param_keys and all_candidates:
        first_params = getattr(all_candidates[0], "params", {}) or {}
        param_keys = list(first_params.keys())
        param_labels = param_keys[:]

    if not param_keys:
        return None

    # データ収集
    rows_data: List[Dict] = []
    for c in all_candidates:
        params = getattr(c, "params", {}) or {}
        obj = getattr(c, "objective_value", None)
        feas = getattr(c, "is_feasible", True)
        if obj is not None:
            try:
                row = {k: float(params[k]) for k in param_keys if k in params}
                row["_obj"] = float(obj)
                row["_feas"] = bool(feas)
                rows_data.append(row)
            except (ValueError, TypeError):
                continue

    if not rows_data:
        return None

    obj_label = str(getattr(cfg, "objective_label", None) or
                    getattr(cfg, "objective_key", "") or "目的関数値")

    obj_vals_all = [r["_obj"] for r in rows_data]
    obj_min = min(obj_vals_all)
    obj_max = max(obj_vals_all)

    img_data: Optional[str] = None

    if len(param_keys) == 1:
        # 1パラメータ: X=パラメータ値, Y=目的関数値
        k0 = param_keys[0]
        x_vals = [r[k0] for r in rows_data if k0 in r]
        y_vals = [r["_obj"] for r in rows_data if k0 in r]
        feas_flags = [r["_feas"] for r in rows_data if k0 in r]

        fig, ax = plt.subplots(figsize=(7, 4), dpi=120)
        inf_x = [x for x, f in zip(x_vals, feas_flags) if not f]
        inf_y = [y for y, f in zip(y_vals, feas_flags) if not f]
        feas_x = [x for x, f in zip(x_vals, feas_flags) if f]
        feas_y = [y for y, f in zip(y_vals, feas_flags) if f]

        if inf_x:
            ax.scatter(inf_x, inf_y, c="#e74c3c", marker="x", s=20, alpha=0.5,
                       linewidths=0.8, label="制約違反")
        if feas_x:
            ax.scatter(feas_x, feas_y, c="#3498db", marker="o", s=20, alpha=0.7,
                       label="実行可能")

        ax.set_xlabel(_esc(param_labels[0]), fontsize=9)
        ax.set_ylabel(_esc(obj_label), fontsize=9)
        ax.set_title("パラメータ空間探索", fontsize=11, fontweight="bold")
        ax.tick_params(labelsize=8)
        ax.grid(alpha=0.3)
        ax.legend(fontsize=8)

        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        img_data = base64.b64encode(buf.read()).decode("ascii")

    else:
        # 2+パラメータ: 最初の2つでスキャッタ、色=目的関数値
        k0, k1 = param_keys[0], param_keys[1]
        valid_rows = [r for r in rows_data if k0 in r and k1 in r]
        if not valid_rows:
            return None

        x_vals = [r[k0] for r in valid_rows]
        y_vals = [r[k1] for r in valid_rows]
        c_vals = [r["_obj"] for r in valid_rows]
        feas_flags = [r["_feas"] for r in valid_rows]

        fig, ax = plt.subplots(figsize=(7, 5), dpi=120)

        import numpy as np
        c_arr = np.array(c_vals, dtype=float)
        scatter = ax.scatter(
            x_vals, y_vals,
            c=c_arr,
            cmap="RdYlGn_r",
            s=20, alpha=0.75,
            edgecolors="none",
        )
        cb = fig.colorbar(scatter, ax=ax, shrink=0.8)
        cb.set_label(_esc(obj_label), fontsize=8)
        cb.ax.tick_params(labelsize=7)

        # 最良解をスター印で強調
        best = getattr(result, "best", None)
        if best:
            best_params = getattr(best, "params", {}) or {}
            bx = best_params.get(k0)
            by = best_params.get(k1)
            if bx is not None and by is not None:
                ax.scatter([float(bx)], [float(by)], c="#e74c3c", marker="*",
                           s=120, zorder=5, label="最良解")
                ax.legend(fontsize=8)

        ax.set_xlabel(_esc(param_labels[0]), fontsize=9)
        ax.set_ylabel(_esc(param_labels[1]), fontsize=9)
        ax.set_title("パラメータ空間探索 (色=目的関数値)", fontsize=11, fontweight="bold")
        ax.tick_params(labelsize=8)
        ax.grid(alpha=0.2)

        # 追加パラメータ軸がある場合の注記
        if len(param_keys) > 2:
            extra = ", ".join(_esc(param_labels[i]) for i in range(2, len(param_keys)))
            ax.set_title(
                f"パラメータ空間探索 ({_esc(param_labels[0])} vs {_esc(param_labels[1])}, 色=目的関数値)\n"
                f"その他軸: {extra}",
                fontsize=10, fontweight="bold"
            )

        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        img_data = base64.b64encode(buf.read()).decode("ascii")

    if img_data is None:
        return None

    return f"""
    <section class="opt-chart-section">
        <h2>パラメータ空間探索</h2>
        <div class="chart-container" style="max-width:700px;">
            <img src="data:image/png;base64,{img_data}" alt="パラメータ空間探索">
        </div>
    </section>
    """


# ---------------------------------------------------------------------------
# HTML セクションビルダー
# ---------------------------------------------------------------------------

def _build_header(title: str, date_str: str) -> str:
    return f"""
    <header class="report-header">
        <h1>{_esc(title)}</h1>
        <p class="report-date">作成日時: {date_str}</p>
        <p class="report-app">snap-controller 自動レポート</p>
    </header>
    """


def _build_project_summary(project: Project, cases: List[AnalysisCase]) -> str:
    total = len(project.cases)
    completed = len(cases)
    model_name = Path(project.s8i_path).name if project.s8i_path else "未設定"
    model_info = ""
    if project.s8i_model:
        m = project.s8i_model
        model_info = (
            f"{m.num_floors}層 / {m.num_nodes}節点 / "
            f"ダンパー定義 {len(m.damper_defs)}種 / 装置 {m.num_dampers}箇所"
        )

    return f"""
    <section class="summary-section">
        <h2>プロジェクト概要</h2>
        <table class="info-table">
            <tr><th>プロジェクト名</th><td>{_esc(project.name)}</td></tr>
            <tr><th>入力モデル</th><td>{_esc(model_name)}</td></tr>
            <tr><th>モデル情報</th><td>{_esc(model_info) if model_info else "—"}</td></tr>
            <tr><th>全ケース数</th><td>{total}</td></tr>
            <tr><th>完了ケース数</th><td>{completed}</td></tr>
        </table>
    </section>
    """


def _build_result_table(
    cases: List[AnalysisCase],
    criteria: PerformanceCriteria,
) -> str:
    if not cases:
        return '<section><h2>結果テーブル</h2><p>完了済みケースがありません。</p></section>'

    # ヘッダ行
    header_cells = "<th>ケース名</th>"
    for key, label, unit in RESPONSE_ITEMS:
        header_cells += f"<th>{_esc(label)}<br><small>({_esc(unit)})</small></th>"
    header_cells += "<th>判定</th>"

    # データ行
    rows = []
    for case in cases:
        rs = case.result_summary or {}
        verdicts = criteria.evaluate(rs) if criteria else {}

        cells = f"<td class='case-name'>{_esc(case.name)}</td>"
        for key, label, unit in RESPONSE_ITEMS:
            val = rs.get(key)
            v = verdicts.get(key)
            cell_class = ""
            if v is True:
                cell_class = "pass"
            elif v is False:
                cell_class = "fail"
            val_str = _format_value(val, key)
            cells += f"<td class='{cell_class}'>{val_str}</td>"

        # 総合判定
        all_pass = criteria.is_all_pass(rs) if criteria else None
        if all_pass is True:
            verdict_html = "<span class='badge badge-pass'>合格</span>"
        elif all_pass is False:
            verdict_html = "<span class='badge badge-fail'>不合格</span>"
        else:
            verdict_html = "<span class='badge badge-na'>—</span>"
        cells += f"<td>{verdict_html}</td>"

        rows.append(f"<tr>{cells}</tr>")

    return f"""
    <section class="result-section">
        <h2>結果テーブル</h2>
        <div class="table-wrapper">
        <table class="result-table">
            <thead><tr>{header_cells}</tr></thead>
            <tbody>{''.join(rows)}</tbody>
        </table>
        </div>
    </section>
    """


def _build_chart_section(
    cases: List[AnalysisCase],
    criteria: PerformanceCriteria,
) -> str:
    """応答値比較チャートセクション。"""
    charts_html = []

    # 各応答値について、ケース比較の棒グラフを生成
    for key, label, unit in RESPONSE_ITEMS:
        values = []
        names = []
        for case in cases:
            rs = case.result_summary or {}
            # スカラー値の場合
            val = rs.get(key)
            if isinstance(val, dict):
                # 層別辞書 → 最大値を採用
                val = max(val.values()) if val else None
            if val is not None:
                values.append(float(val))
                names.append(case.name)

        if not values:
            continue

        # 基準線
        limit = None
        if criteria:
            item = next(
                (it for it in criteria.items if it.key == key and it.enabled),
                None,
            )
            if item and item.limit_value is not None:
                limit = item.limit_value

        img_data = _render_bar_chart(names, values, label, unit, limit)
        if img_data:
            charts_html.append(f"""
            <div class="chart-container">
                <h3>{_esc(label)} ({_esc(unit)})</h3>
                <img src="data:image/png;base64,{img_data}" alt="{_esc(label)}">
            </div>
            """)

    if not charts_html:
        return ""

    return f"""
    <section class="chart-section">
        <h2>応答値比較チャート</h2>
        <div class="charts-grid">
            {''.join(charts_html)}
        </div>
    </section>
    """


def _build_criteria_verdict(
    cases: List[AnalysisCase],
    criteria: PerformanceCriteria,
) -> str:
    """性能基準判定結果セクション。"""
    if not cases or not criteria:
        return ""

    enabled_items = [it for it in criteria.items if it.enabled]
    if not enabled_items:
        return ""

    # ヘッダ
    header = "<th>ケース名</th>"
    for item in enabled_items:
        header += f"<th>{_esc(item.label)}<br><small>基準: {_format_value(item.limit_value, item.key)}</small></th>"
    header += "<th>総合</th>"

    rows = []
    for case in cases:
        rs = case.result_summary or {}
        verdicts = criteria.evaluate(rs)
        cells = f"<td class='case-name'>{_esc(case.name)}</td>"

        for item in enabled_items:
            val = rs.get(item.key)
            v = verdicts.get(item.key)
            val_str = _format_value(val, item.key)

            if v is True:
                mark = "✓"
                cls = "pass"
            elif v is False:
                mark = "✗"
                cls = "fail"
            else:
                mark = "—"
                cls = "na"
            cells += f"<td class='{cls}'>{mark} {val_str}</td>"

        all_pass = criteria.is_all_pass(rs)
        if all_pass is True:
            cells += "<td><span class='badge badge-pass'>合格</span></td>"
        elif all_pass is False:
            cells += "<td><span class='badge badge-fail'>不合格</span></td>"
        else:
            cells += "<td><span class='badge badge-na'>—</span></td>"

        rows.append(f"<tr>{cells}</tr>")

    return f"""
    <section class="verdict-section">
        <h2>性能基準判定</h2>
        <p>基準名: {_esc(criteria.name)}</p>
        <div class="table-wrapper">
        <table class="verdict-table">
            <thead><tr>{header}</tr></thead>
            <tbody>{''.join(rows)}</tbody>
        </table>
        </div>
    </section>
    """


def _find_period_xbn(case: AnalysisCase) -> Optional[Path]:
    """AnalysisCase から Period.xbn ファイルを探します。"""
    search_dirs: List[Path] = []

    for attr in ("binary_result_dir", "result_path", "output_dir"):
        v = getattr(case, attr, None)
        if v:
            search_dirs.append(Path(v))

    for dr in getattr(case, "dyc_results", []) or []:
        rd = dr.get("result_dir")
        if rd:
            search_dirs.append(Path(rd))

    model_path = getattr(case, "model_path", None)
    if model_path:
        base = Path(model_path).parent
        search_dirs.append(base)
        if base.exists():
            for sub in base.iterdir():
                if sub.is_dir() and sub.name.startswith("D"):
                    search_dirs.append(sub)

    for d in search_dirs:
        p = d / "Period.xbn"
        if p.exists():
            return p
    return None


def _build_modal_analysis_section(
    cases: List[AnalysisCase],
    include_charts: bool,
) -> Optional[str]:
    """固有値解析（モード情報）セクションを構築します。"""
    if not _PERIOD_READER_AVAILABLE:
        return None

    case_modes: List[Tuple[str, List[Any]]] = []
    for case in cases:
        period_path = _find_period_xbn(case)
        if period_path is None:
            continue
        try:
            reader = PeriodXbnReader(str(period_path))
            if reader.modes:
                case_modes.append((case.name, reader.modes))
        except Exception:
            logger.debug("固有値解析結果の読込失敗: %s", period_path)
            continue

    if not case_modes:
        return None

    parts: List[str] = ['<section class="modal-section"><h2>固有値解析</h2>']

    for case_name, modes in case_modes:
        # テーブルヘッダ
        parts.append(f"<h3>{_esc(case_name)}</h3>")
        parts.append('<div class="table-wrapper"><table class="result-table">')
        parts.append(
            "<thead><tr>"
            "<th>モード</th>"
            "<th>固有周期<br><small>(s)</small></th>"
            "<th>振動数<br><small>(Hz)</small></th>"
            "<th>角振動数<br><small>(rad/s)</small></th>"
            "<th>支配方向</th>"
            "<th>β_X</th><th>β_Y</th>"
            "<th>PM_X<br><small>(%)</small></th>"
            "<th>PM_Y<br><small>(%)</small></th>"
            "</tr></thead><tbody>"
        )
        for m in modes:
            dom = m.dominant_direction
            parts.append(
                f"<tr>"
                f"<td>{m.mode_no}</td>"
                f"<td>{m.period:.4f}</td>"
                f"<td>{m.frequency:.3f}</td>"
                f"<td>{m.omega:.3f}</td>"
                f"<td>{_esc(dom)}</td>"
                f"<td>{m.beta.get('X', 0):.4f}</td>"
                f"<td>{m.beta.get('Y', 0):.4f}</td>"
                f"<td>{m.pm.get('X', 0):.2f}</td>"
                f"<td>{m.pm.get('Y', 0):.2f}</td>"
                f"</tr>"
            )
        parts.append("</tbody></table></div>")

    # 刺激係数の棒グラフ
    if include_charts and _MPL_AVAILABLE and case_modes:
        chart_html = _render_beta_chart(case_modes)
        if chart_html:
            parts.append(chart_html)

    parts.append("</section>")
    return "\n".join(parts)


def _render_beta_chart(
    case_modes: List[Tuple[str, List[Any]]],
) -> Optional[str]:
    """刺激係数 β の棒グラフを base64 PNG として返します。"""
    if not _MPL_AVAILABLE:
        return None

    fig, axes = plt.subplots(1, 2, figsize=(10, 4), dpi=120)

    colors = ["#3498db", "#e74c3c", "#2ecc71", "#9b59b6", "#f39c12"]

    for ax, direction, label in [
        (axes[0], "X", "β_X (X方向刺激係数)"),
        (axes[1], "Y", "β_Y (Y方向刺激係数)"),
    ]:
        for ci, (case_name, modes) in enumerate(case_modes):
            mode_nos = [m.mode_no for m in modes]
            betas = [abs(m.beta.get(direction, 0)) for m in modes]
            color = colors[ci % len(colors)]
            width = 0.8 / len(case_modes)
            offset = (ci - len(case_modes) / 2 + 0.5) * width
            x = [mn + offset for mn in mode_nos]
            ax.bar(x, betas, width=width, color=color, alpha=0.8,
                   label=case_name, edgecolor="white", linewidth=0.3)

        ax.set_xlabel("モード番号", fontsize=8)
        ax.set_ylabel("|β|", fontsize=8)
        ax.set_title(label, fontsize=9)
        ax.tick_params(labelsize=7)
        ax.grid(axis="y", alpha=0.3)
        if len(case_modes) > 1:
            ax.legend(fontsize=7)

    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    img_data = base64.b64encode(buf.read()).decode("ascii")

    return f"""
    <div class="chart-container" style="grid-column: 1 / -1;">
        <h3>刺激係数 |β| (ケース比較)</h3>
        <img src="data:image/png;base64,{img_data}" alt="刺激係数比較">
    </div>
    """


def _build_footer(date_str: str) -> str:
    return f"""
    <footer class="report-footer">
        <hr>
        <p>snap-controller 自動生成レポート | {date_str}</p>
        <p><small>本レポートは snap-controller (BAUES) により自動生成されました。</small></p>
    </footer>
    """


# ---------------------------------------------------------------------------
# チャート描画
# ---------------------------------------------------------------------------

def _render_bar_chart(
    names: List[str],
    values: List[float],
    title: str,
    unit: str,
    limit: Optional[float] = None,
) -> Optional[str]:
    """
    棒グラフを base64 PNG 画像として返します。
    """
    if not _MPL_AVAILABLE:
        return None

    fig, ax = plt.subplots(figsize=(max(4, len(names) * 0.8), 3.5), dpi=120)

    colors = []
    for v in values:
        if limit is not None and v > limit:
            colors.append("#e74c3c")  # 赤 (不合格)
        else:
            colors.append("#3498db")  # 青

    x = range(len(names))
    bars = ax.bar(x, values, color=colors, edgecolor="white", linewidth=0.5)

    if limit is not None:
        ax.axhline(y=limit, color="#e74c3c", linestyle="--", linewidth=1.2,
                    label=f"基準: {limit:.4g}")
        ax.legend(fontsize=8)

    ax.set_xticks(x)
    ax.set_xticklabels(names, fontsize=7, rotation=30, ha="right")
    ax.set_ylabel(f"{unit}", fontsize=8)
    ax.set_title(title, fontsize=10, fontweight="bold")
    ax.tick_params(axis="y", labelsize=7)
    ax.grid(axis="y", alpha=0.3)

    # 値ラベル
    for bar, val in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            bar.get_height(),
            f"{val:.4g}",
            ha="center", va="bottom", fontsize=6,
        )

    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def _esc(text: Any) -> str:
    """HTML エスケープ。"""
    return _html.escape(str(text)) if text else ""


def _format_value(val: Any, key: str = "") -> str:
    """応答値を表示用にフォーマットします。"""
    if val is None:
        return "N/A"
    if isinstance(val, dict):
        val = max(val.values()) if val else None
        if val is None:
            return "N/A"
    try:
        f = float(val)
    except (ValueError, TypeError):
        return str(val)
    # キーに応じた桁数
    if key in ("max_story_drift", "max_drift"):
        return f"{f:.6f}"
    elif key in ("max_disp", "max_story_disp"):
        return f"{f:.5f}"
    elif key in ("shear_coeff",):
        return f"{f:.4f}"
    elif key in ("max_otm",):
        return f"{f:.1f}"
    else:
        return f"{f:.4f}"


def _wrap_html(title: str, body: str) -> str:
    """完全な HTML ドキュメントに包みます。"""
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{_esc(title)}</title>
    <style>
        {_CSS}
    </style>
</head>
<body>
    <div class="report-container">
        {body}
    </div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# ダンパー本数最小化レポート
# ---------------------------------------------------------------------------

def generate_minimizer_report(
    result: "MinimizationResult",
    output_path: Optional[str] = None,
    include_charts: bool = True,
    title: Optional[str] = None,
    is_snap: bool = False,
) -> str:
    """
    ダンパー本数最小化結果を HTML レポートとして生成します。

    Parameters
    ----------
    result : MinimizationResult
        最小化結果オブジェクト。
    output_path : str, optional
        出力先ファイルパス。None の場合は HTML 文字列のみ返却。
    include_charts : bool
        チャート画像を含めるかどうか (matplotlib 必須)。
    title : str, optional
        レポートタイトル。
    is_snap : bool
        SNAP 実解析で得た結果かどうか。

    Returns
    -------
    str
        生成された HTML 文字列。
    """
    from app.services.damper_count_minimizer import (
        MinimizationResult, MinimizationStep, FloorResponse, STRATEGIES,
    )

    report_title = title or "ダンパー本数最小化レポート"
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    sections: List[str] = []
    sections.append(_build_header(report_title, now))
    sections.append(_build_minimizer_summary(result, is_snap))
    sections.append(_build_minimizer_floor_table(result))
    if result.history:
        sections.append(_build_minimizer_history_table(result))
    if include_charts and _MPL_AVAILABLE and result.history:
        chart = _build_minimizer_chart(result)
        if chart:
            sections.append(chart)
    sections.append(_build_footer(now))

    html_content = _wrap_html(report_title, "\n".join(sections))

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(html_content, encoding="utf-8")

    return html_content


def _build_minimizer_summary(result: Any, is_snap: bool) -> str:
    """最小化結果の概要セクション。"""
    from app.services.damper_count_minimizer import STRATEGIES

    strategy_name = STRATEGIES.get(result.strategy, result.strategy)
    eval_tag = "SNAP 実解析" if is_snap else "モック評価"
    feasible_text = "OK" if result.is_feasible else "NG"
    feasible_color = "#2e7d32" if result.is_feasible else "#c62828"
    initial_total = sum(result.initial_quantities.values())
    reduction = initial_total - result.final_count

    return f"""
    <section class="opt-best-card" style="margin-top: 16px;">
        <div class="opt-best-header" style="background: {'#e8f5e9' if result.is_feasible else '#ffebee'};">
            <h2>{'✅' if result.is_feasible else '⚠️'} 最小化結果</h2>
        </div>
        <div class="opt-best-body">
            <div class="opt-best-item">
                <div class="opt-best-label">戦略</div>
                <div class="opt-best-value">{_esc(strategy_name)}</div>
            </div>
            <div class="opt-best-item">
                <div class="opt-best-label">評価方式</div>
                <div class="opt-best-value">{_esc(eval_tag)}</div>
            </div>
            <div class="opt-best-item">
                <div class="opt-best-label">初期合計本数</div>
                <div class="opt-best-value">{initial_total}</div>
            </div>
            <div class="opt-best-item">
                <div class="opt-best-label">最終合計本数</div>
                <div class="opt-best-value" style="font-size: 1.5em; font-weight: bold;">{result.final_count}</div>
            </div>
            <div class="opt-best-item">
                <div class="opt-best-label">削減数</div>
                <div class="opt-best-value">{reduction:+d}</div>
            </div>
            <div class="opt-best-item">
                <div class="opt-best-label">基準充足</div>
                <div class="opt-best-value" style="color: {feasible_color}; font-weight: bold;">{feasible_text}</div>
            </div>
            <div class="opt-best-item">
                <div class="opt-best-label">最終マージン</div>
                <div class="opt-best-value">{result.final_margin:+.4f}</div>
            </div>
            <div class="opt-best-item">
                <div class="opt-best-label">評価回数</div>
                <div class="opt-best-value">{result.evaluations}</div>
            </div>
        </div>
    </section>
    """


def _build_minimizer_floor_table(result: Any) -> str:
    """階別ダンパー配置テーブル。"""
    floor_resp_map = {
        fr.floor_key: fr for fr in getattr(result, "final_floor_responses", [])
    }

    rows: List[str] = []
    for fk in sorted(
        result.final_quantities.keys(),
        key=lambda k: int("".join(c for c in k if c.isdigit()) or "0"),
    ):
        final = result.final_quantities.get(fk, 0)
        initial = result.initial_quantities.get(fk, 0)
        diff = final - initial
        diff_text = f"{diff:+d}" if diff != 0 else "±0"
        diff_color = "#c62828" if diff < 0 else ("#2e7d32" if diff > 0 else "#666")

        margin_text = "—"
        margin_color = "#666"
        fr = floor_resp_map.get(fk)
        if fr:
            margins = [v for k, v in fr.values.items() if k.startswith("margin_")]
            if margins:
                m = min(margins)
                margin_text = f"{m:+.4f}"
                if m >= 0.05:
                    margin_color = "#2e7d32"
                elif m >= 0.0:
                    margin_color = "#e65100"
                else:
                    margin_color = "#c62828"

        rows.append(
            f"<tr>"
            f"<td>{_esc(fk)}</td>"
            f"<td style='text-align:right;'>{final}</td>"
            f"<td style='text-align:right;'>{initial}</td>"
            f"<td style='text-align:right; color:{diff_color};'>{diff_text}</td>"
            f"<td style='text-align:right; color:{margin_color};'>{margin_text}</td>"
            f"</tr>"
        )

    total_final = result.final_count
    total_initial = sum(result.initial_quantities.values())
    total_diff = total_final - total_initial
    total_diff_text = f"{total_diff:+d}" if total_diff != 0 else "±0"

    return f"""
    <section class="opt-ranking-section">
        <h2>階別ダンパー配置</h2>
        <table>
            <thead>
                <tr>
                    <th>階</th>
                    <th style='text-align:right;'>最終本数</th>
                    <th style='text-align:right;'>初期本数</th>
                    <th style='text-align:right;'>変化</th>
                    <th style='text-align:right;'>マージン</th>
                </tr>
            </thead>
            <tbody>
                {"".join(rows)}
                <tr style='font-weight:bold; border-top:2px solid #333;'>
                    <td>合計</td>
                    <td style='text-align:right;'>{total_final}</td>
                    <td style='text-align:right;'>{total_initial}</td>
                    <td style='text-align:right;'>{total_diff_text}</td>
                    <td style='text-align:right;'>{result.final_margin:+.4f}</td>
                </tr>
            </tbody>
        </table>
    </section>
    """


def _build_minimizer_history_table(result: Any) -> str:
    """探索履歴テーブル（上位30ステップ）。"""
    steps = result.history[:30]
    rows: List[str] = []
    for step in steps:
        feasible_color = "#2e7d32" if step.is_feasible else "#c62828"
        verdict = "OK" if step.is_feasible else "NG"
        rows.append(
            f"<tr>"
            f"<td style='text-align:center;'>{step.iteration}</td>"
            f"<td>{_esc(step.action)}</td>"
            f"<td style='text-align:right;'>{step.total_count}</td>"
            f"<td style='text-align:center; color:{feasible_color};'>{verdict}</td>"
            f"<td style='text-align:right;'>{step.worst_margin:+.4f}</td>"
            f"<td>{_esc(step.note)}</td>"
            f"</tr>"
        )

    more = ""
    if len(result.history) > 30:
        more = f"<p style='color:#666; font-size:12px;'>... 他 {len(result.history) - 30} ステップ省略</p>"

    return f"""
    <section class="opt-ranking-section">
        <h2>探索履歴</h2>
        <table>
            <thead>
                <tr>
                    <th style='text-align:center;'>ステップ</th>
                    <th>操作</th>
                    <th style='text-align:right;'>合計本数</th>
                    <th style='text-align:center;'>判定</th>
                    <th style='text-align:right;'>マージン</th>
                    <th>備考</th>
                </tr>
            </thead>
            <tbody>
                {"".join(rows)}
            </tbody>
        </table>
        {more}
    </section>
    """


def _build_minimizer_chart(result: Any) -> Optional[str]:
    """本数推移・マージン推移チャート (base64 PNG)。"""
    if not _MPL_AVAILABLE or not result.history:
        return None

    try:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 5), dpi=100)

        iters = [s.iteration for s in result.history]
        counts = [s.total_count for s in result.history]
        margins = [s.worst_margin for s in result.history]
        colors = ["#2e7d32" if s.is_feasible else "#c62828" for s in result.history]

        ax1.scatter(iters, counts, c=colors, s=15, zorder=3)
        ax1.plot(iters, counts, color="#1565c0", alpha=0.5, linewidth=1)
        ax1.set_ylabel("合計本数")
        ax1.set_title("本数推移", fontsize=10)
        ax1.grid(linestyle="--", alpha=0.3)

        ax2.scatter(iters, margins, c=colors, s=15, zorder=3)
        ax2.plot(iters, margins, color="#1565c0", alpha=0.5, linewidth=1)
        ax2.axhline(y=0, color="#e65100", linestyle="--", linewidth=1, alpha=0.7)
        ax2.set_ylabel("最小マージン")
        ax2.set_xlabel("ステップ")
        ax2.set_title("マージン推移", fontsize=10)
        ax2.grid(linestyle="--", alpha=0.3)

        try:
            fig.tight_layout()
        except Exception:
            logger.debug("tight_layout失敗（MemoryError等）")

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        img_data = base64.b64encode(buf.read()).decode("ascii")

        return f"""
        <section class="opt-chart-section">
            <h2>探索推移チャート</h2>
            <div class="chart-container">
                <img src="data:image/png;base64,{img_data}" alt="探索推移チャート"
                     style="max-width:100%;">
            </div>
            <p style="font-size:11px; color:#666;">
                緑=基準充足 / 赤=基準違反。オレンジ破線=マージンゼロ。
            </p>
        </section>
        """
    except Exception:
        logger.debug("探索推移チャート生成失敗", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """
/* ===== Reset & Base ===== */
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: "Segoe UI", "Meiryo", "Hiragino Sans", sans-serif;
    font-size: 14px;
    line-height: 1.6;
    color: #333;
    background: #f8f9fa;
}
.report-container {
    max-width: 1100px;
    margin: 0 auto;
    padding: 30px 40px;
    background: #fff;
    box-shadow: 0 2px 8px rgba(0,0,0,0.08);
}

/* ===== Header ===== */
.report-header {
    text-align: center;
    margin-bottom: 32px;
    padding-bottom: 16px;
    border-bottom: 3px solid #2c3e50;
}
.report-header h1 {
    font-size: 22px;
    color: #2c3e50;
    margin-bottom: 8px;
}
.report-date { font-size: 13px; color: #666; }
.report-app { font-size: 11px; color: #999; }

/* ===== Sections ===== */
section { margin-bottom: 32px; }
h2 {
    font-size: 17px;
    color: #2c3e50;
    border-left: 4px solid #3498db;
    padding-left: 10px;
    margin-bottom: 14px;
}
h3 {
    font-size: 14px;
    color: #555;
    margin-bottom: 8px;
}

/* ===== Tables ===== */
.table-wrapper { overflow-x: auto; }
table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
}
th, td {
    padding: 6px 10px;
    text-align: center;
    border: 1px solid #ddd;
}
th {
    background: #f0f3f6;
    font-weight: 600;
    color: #333;
}
.info-table th { text-align: left; width: 180px; background: #f7f9fb; }
.info-table td { text-align: left; }
.case-name { text-align: left; font-weight: 500; }

/* Verdict colors */
td.pass { background: #e8f5e9; }
td.fail { background: #fde8e8; }
td.na   { background: #f5f5f5; color: #999; }

.badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 3px;
    font-size: 11px;
    font-weight: bold;
}
.badge-pass { background: #27ae60; color: #fff; }
.badge-fail { background: #e74c3c; color: #fff; }
.badge-na   { background: #bbb; color: #fff; }

/* ===== Charts ===== */
.charts-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
    gap: 20px;
}
.chart-container {
    background: #fafbfc;
    border: 1px solid #eee;
    border-radius: 4px;
    padding: 12px;
    text-align: center;
}
.chart-container img {
    max-width: 100%;
    height: auto;
}

/* ===== Footer ===== */
.report-footer {
    margin-top: 40px;
    text-align: center;
    color: #999;
    font-size: 11px;
}
.report-footer hr {
    border: none;
    border-top: 1px solid #ddd;
    margin-bottom: 12px;
}

/* ===== Print ===== */
@media print {
    body { background: #fff; }
    .report-container { box-shadow: none; padding: 0; }
    .chart-container { break-inside: avoid; }
    section { break-inside: avoid; }
}

/* ===== Optimization-specific ===== */
.opt-best-card {
    border: 2px solid #3498db;
    border-radius: 6px;
    overflow: hidden;
    background: #fff;
}
.opt-best-header {
    background: #2c3e50;
    color: #fff;
    padding: 10px 16px;
    display: flex;
    align-items: center;
    gap: 14px;
}
.opt-best-obj {
    font-size: 14px;
    color: #ecf0f1;
}
.opt-best-obj strong {
    font-size: 17px;
    color: #f1c40f;
}
.opt-best-body {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
    padding: 16px;
}
.opt-best-col { min-width: 0; }
.opt-best-col h3 {
    font-size: 13px;
    color: #3498db;
    border-bottom: 1px solid #e0e6ed;
    padding-bottom: 4px;
    margin-bottom: 8px;
}
.infeasible-row { background: #fff8f8; }
.infeasible-row td { color: #999; }
.opt-ranking-section .badge { font-size: 10px; padding: 1px 6px; }
.opt-chart-section { margin-bottom: 32px; }
.opt-chart-section .chart-container { display: inline-block; }
@media (max-width: 700px) {
    .opt-best-body { grid-template-columns: 1fr; }
}
@media print {
    .opt-best-card { break-inside: avoid; }
    .opt-best-body { grid-template-columns: 1fr 1fr; }
}
"""
