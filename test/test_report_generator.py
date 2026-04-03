"""
test/test_report_generator.py
レポート生成サービスのユニットテスト。
"""

import tempfile
from pathlib import Path

import pytest

from app.models.analysis_case import AnalysisCase, AnalysisCaseStatus
from app.models.performance_criteria import PerformanceCriteria
from app.models.project import Project
from app.services.report_generator import (
    generate_report, _format_value, _esc, RESPONSE_ITEMS,
)


def _make_completed_case(name: str, drift: float = 0.004) -> AnalysisCase:
    """テスト用の完了ケースを作成。"""
    return AnalysisCase(
        name=name,
        model_path="test.s8i",
        status=AnalysisCaseStatus.COMPLETED,
        result_summary={
            "max_disp": 0.025,
            "max_vel": 0.30,
            "max_acc": 3.5,
            "max_story_disp": 0.008,
            "max_story_drift": drift,
            "max_drift": drift,  # criteria uses this key
            "shear_coeff": 0.15,
            "max_otm": 5000.0,
        },
    )


class TestReportGenerator:
    """レポート生成のテスト。"""

    def test_generate_basic_html(self):
        proj = Project(name="テストPJ")
        case = _make_completed_case("Case1")
        proj.cases.append(case)
        html = generate_report(proj, [case], include_charts=False)
        assert "<!DOCTYPE html>" in html
        assert "テストPJ" in html
        assert "Case1" in html

    def test_generate_with_no_cases(self):
        proj = Project(name="空PJ")
        html = generate_report(proj, [], include_charts=False)
        assert "完了済みケースがありません" in html

    def test_generate_with_criteria(self):
        proj = Project(name="基準テスト")
        case_pass = _make_completed_case("Pass", drift=0.003)
        case_fail = _make_completed_case("Fail", drift=0.01)
        proj.cases.extend([case_pass, case_fail])

        html = generate_report(
            proj, [case_pass, case_fail], include_charts=False,
        )
        assert "合格" in html
        assert "不合格" in html

    def test_generate_with_charts(self):
        proj = Project(name="チャートテスト")
        case = _make_completed_case("WithChart")
        proj.cases.append(case)
        html = generate_report(proj, [case], include_charts=True)
        # matplotlib が利用可能なら画像が含まれる
        assert "<!DOCTYPE html>" in html
        # base64画像 or チャートなし (matplotlibなしの場合)
        if "data:image/png;base64" in html:
            assert "応答値比較チャート" in html

    def test_generate_save_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proj = Project(name="ファイル保存テスト")
            case = _make_completed_case("SaveCase")
            proj.cases.append(case)

            path = str(Path(tmpdir) / "report.html")
            html = generate_report(
                proj, [case], output_path=path, include_charts=False,
            )
            assert Path(path).exists()
            content = Path(path).read_text(encoding="utf-8")
            assert content == html

    def test_generate_multiple_cases(self):
        proj = Project(name="複数ケース")
        for i in range(5):
            case = _make_completed_case(f"Case{i+1}", drift=0.002 + i * 0.001)
            proj.cases.append(case)
        html = generate_report(proj, proj.cases, include_charts=False)
        for i in range(5):
            assert f"Case{i+1}" in html

    def test_custom_title(self):
        proj = Project(name="PJ")
        case = _make_completed_case("C1")
        proj.cases.append(case)
        html = generate_report(proj, [case], title="カスタムタイトル", include_charts=False)
        assert "カスタムタイトル" in html

    def test_auto_selects_completed_cases(self):
        proj = Project(name="自動選択テスト")
        c1 = _make_completed_case("Done")
        c2 = AnalysisCase(name="Pending", status=AnalysisCaseStatus.PENDING)
        proj.cases.extend([c1, c2])
        html = generate_report(proj, include_charts=False)
        assert "Done" in html
        assert "Pending" not in html  # 結果テーブルに未完了は含まれない


class TestFormatValue:
    """値フォーマットのテスト。"""

    def test_none(self):
        assert _format_value(None) == "N/A"

    def test_drift_format(self):
        result = _format_value(0.005, "max_story_drift")
        assert "0.005000" in result

    def test_otm_format(self):
        result = _format_value(12345.678, "max_otm")
        assert "12345.7" in result

    def test_dict_value(self):
        result = _format_value({1: 0.01, 2: 0.02, 3: 0.03}, "max_disp")
        assert "0.03" in result  # max value

    def test_empty_dict(self):
        assert _format_value({}) == "N/A"


class TestEsc:
    """HTMLエスケープのテスト。"""

    def test_basic(self):
        assert _esc("<script>") == "&lt;script&gt;"

    def test_none(self):
        assert _esc(None) == ""

    def test_ampersand(self):
        assert _esc("A & B") == "A &amp; B"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
