"""
tests/test_report_generator.py
HTML レポート生成のユニットテスト。
"""

import pytest
from pathlib import Path

from app.models.analysis_case import AnalysisCase, AnalysisCaseStatus
from app.models.project import Project
from app.models.performance_criteria import PerformanceCriteria
from app.services.report_generator import generate_report, RESPONSE_ITEMS


def _make_completed_case(name: str, drift: float = 0.005, acc: float = 3.0) -> AnalysisCase:
    """完了済みケースを生成するヘルパー。"""
    case = AnalysisCase(name=name)
    case.status = AnalysisCaseStatus.COMPLETED
    case.return_code = 0
    case.result_summary = {
        "max_disp": drift * 10,
        "max_vel": 0.25,
        "max_acc": acc,
        "max_story_disp": drift * 3,
        "max_story_drift": drift,
        "shear_coeff": 0.15,
        "max_otm": 10000.0,
    }
    return case


class TestGenerateReport:
    """generate_report() のテスト。"""

    def test_returns_html_string(self):
        """HTML 文字列を返す。"""
        proj = Project(name="テストプロジェクト")
        case = _make_completed_case("Case 1")
        proj.cases.append(case)

        html = generate_report(proj, cases=[case])

        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html
        assert "テストプロジェクト" in html

    def test_contains_case_name(self):
        """ケース名がレポートに含まれる。"""
        proj = Project(name="Test")
        case = _make_completed_case("オイルダンパー500kN")
        proj.cases.append(case)

        html = generate_report(proj, cases=[case])

        assert "オイルダンパー500kN" in html

    def test_contains_response_values(self):
        """応答値がレポートに含まれる。"""
        proj = Project(name="Test")
        case = _make_completed_case("Case1", drift=0.003, acc=2.5)
        proj.cases.append(case)

        html = generate_report(proj, cases=[case])

        assert "0.003" in html  # max_story_drift value
        assert "2.5" in html    # max_acc value

    def test_multiple_cases(self):
        """複数ケースのレポート。"""
        proj = Project(name="Test")
        for i in range(5):
            case = _make_completed_case(f"Case {i+1}", drift=0.003 + i * 0.001)
            proj.cases.append(case)

        html = generate_report(proj)

        for i in range(5):
            assert f"Case {i+1}" in html

    def test_writes_to_file(self, tmp_path):
        """ファイルに書き出せる。"""
        proj = Project(name="Test")
        case = _make_completed_case("Case 1")
        proj.cases.append(case)

        output = tmp_path / "report.html"
        html = generate_report(proj, cases=[case], output_path=str(output))

        assert output.exists()
        content = output.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert content == html

    def test_creates_parent_directory(self, tmp_path):
        """親ディレクトリが存在しなくても作成される。"""
        proj = Project(name="Test")
        case = _make_completed_case("Case 1")
        proj.cases.append(case)

        output = tmp_path / "a" / "b" / "report.html"
        generate_report(proj, cases=[case], output_path=str(output))

        assert output.exists()

    def test_empty_cases(self):
        """完了ケースがない場合もエラーにならない。"""
        proj = Project(name="Test")
        html = generate_report(proj, cases=[])
        assert isinstance(html, str)
        assert "完了済みケースがありません" in html

    def test_with_criteria(self):
        """性能基準判定がレポートに含まれる。"""
        proj = Project(name="Test")
        case = _make_completed_case("Case 1", drift=0.003)
        proj.cases.append(case)

        # 基準: max_drift <= 0.005 (合格するはず)
        proj.criteria = PerformanceCriteria()
        for item in proj.criteria.items:
            if item.key == "max_story_drift":
                item.enabled = True
                item.limit_value = 0.005

        html = generate_report(proj, cases=[case])

        assert "合格" in html or "pass" in html

    def test_with_failing_criteria(self):
        """不合格ケースのレポート。"""
        proj = Project(name="Test")
        case = _make_completed_case("Case Fail", drift=0.01)
        proj.cases.append(case)

        proj.criteria = PerformanceCriteria()
        for item in proj.criteria.items:
            if item.key == "max_story_drift":
                item.enabled = True
                item.limit_value = 0.005  # 0.01 > 0.005 → 不合格

        html = generate_report(proj, cases=[case])

        assert "不合格" in html or "fail" in html

    def test_custom_title(self):
        """カスタムタイトル。"""
        proj = Project(name="Test")
        case = _make_completed_case("Case 1")
        proj.cases.append(case)

        html = generate_report(proj, cases=[case], title="カスタムレポート")

        assert "カスタムレポート" in html

    def test_no_charts(self):
        """チャートなしモード。"""
        proj = Project(name="Test")
        case = _make_completed_case("Case 1")
        proj.cases.append(case)

        html = generate_report(proj, cases=[case], include_charts=False)

        assert isinstance(html, str)
        # base64 画像がない（charts なし）
        assert "data:image/png" not in html

    def test_html_escaping(self):
        """特殊文字がエスケープされる。"""
        proj = Project(name='<script>alert("XSS")</script>')
        case = _make_completed_case('<b>Dangerous</b>')
        proj.cases.append(case)

        html = generate_report(proj, cases=[case])

        # 生のタグが含まれない
        assert '<script>alert' not in html
        assert '&lt;script&gt;' in html
