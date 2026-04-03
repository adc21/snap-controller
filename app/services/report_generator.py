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
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

    if include_charts and _MPL_AVAILABLE and cases:
        sections.append(_build_chart_section(cases, project.criteria))

    sections.append(_build_criteria_verdict(cases, project.criteria))
    sections.append(_build_footer(now))

    html_content = _wrap_html(report_title, "\n".join(sections))

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(html_content, encoding="utf-8")

    return html_content


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
"""
