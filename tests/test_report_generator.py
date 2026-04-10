"""
tests/test_report_generator.py
HTML レポート生成のユニットテスト。
"""

import struct
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.models.analysis_case import AnalysisCase, AnalysisCaseStatus
from app.models.project import Project
from app.models.performance_criteria import PerformanceCriteria
from app.services.report_generator import (
    generate_report,
    RESPONSE_ITEMS,
    _find_period_xbn,
    _build_modal_analysis_section,
)


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


# ---------------------------------------------------------------------------
# 固有値解析セクション テスト
# ---------------------------------------------------------------------------

def _create_period_xbn(path: Path, num_modes: int = 3) -> None:
    """テスト用の簡易 Period.xbn バイナリを生成します。"""
    # PeriodXbnReader は _detect_mode_layout で T/ω の位置を自動検出する。
    # 最低限、ヘッダ + モードレコードの形式を守る必要がある。
    # ここではモック経由でテストするため、ファイル生成はスキップ可能。
    path.touch()


class TestFindPeriodXbn:
    """_find_period_xbn() のテスト。"""

    def test_finds_in_binary_result_dir(self, tmp_path):
        """binary_result_dir に Period.xbn がある場合に見つける。"""
        result_dir = tmp_path / "results"
        result_dir.mkdir()
        (result_dir / "Period.xbn").touch()

        case = AnalysisCase(name="Test")
        case.binary_result_dir = str(result_dir)  # type: ignore

        found = _find_period_xbn(case)
        assert found is not None
        assert found.name == "Period.xbn"

    def test_finds_in_output_dir(self, tmp_path):
        """output_dir に Period.xbn がある場合に見つける。"""
        out = tmp_path / "output"
        out.mkdir()
        (out / "Period.xbn").touch()

        case = AnalysisCase(name="Test", output_dir=str(out))

        found = _find_period_xbn(case)
        assert found is not None

    def test_finds_in_dyc_result_dir(self, tmp_path):
        """dyc_results の result_dir に Period.xbn がある場合に見つける。"""
        d1 = tmp_path / "D1"
        d1.mkdir()
        (d1 / "Period.xbn").touch()

        case = AnalysisCase(name="Test")
        case.dyc_results = [{"result_dir": str(d1)}]

        found = _find_period_xbn(case)
        assert found is not None

    def test_returns_none_when_missing(self):
        """Period.xbn がない場合は None を返す。"""
        case = AnalysisCase(name="Test")
        assert _find_period_xbn(case) is None

    def test_finds_in_model_subdir(self, tmp_path):
        """model_path の D* サブフォルダに Period.xbn がある場合に見つける。"""
        model_dir = tmp_path / "model"
        model_dir.mkdir()
        (model_dir / "test.s8i").touch()
        d1 = model_dir / "D1"
        d1.mkdir()
        (d1 / "Period.xbn").touch()

        case = AnalysisCase(name="Test", model_path=str(model_dir / "test.s8i"))

        found = _find_period_xbn(case)
        assert found is not None


class TestBuildModalAnalysisSection:
    """_build_modal_analysis_section() のテスト。"""

    def _make_mock_mode(self, mode_no, period, beta_x, beta_y, pm_x, pm_y):
        """ModeInfo 相当のモックを生成。"""
        from controller.binary.period_xbn_reader import ModeInfo
        import math
        omega = 2 * math.pi / period if period > 0 else 0
        return ModeInfo(
            mode_no=mode_no,
            period=period,
            omega=omega,
            beta={"X": beta_x, "Y": beta_y, "Z": 0.0, "RX": 0.0, "RY": 0.0},
            pm={"X": pm_x, "Y": pm_y, "Z": 0.0, "R": 0.0},
            raw=[0.0] * 14,
        )

    def test_returns_none_without_period_files(self):
        """Period.xbn がなければ None を返す。"""
        case = _make_completed_case("Case1")
        result = _build_modal_analysis_section([case], False)
        assert result is None

    @patch("app.services.report_generator._find_period_xbn")
    @patch("app.services.report_generator.PeriodXbnReader")
    def test_builds_table_with_modes(self, MockReader, mock_find):
        """モード情報テーブルが生成される。"""
        modes = [
            self._make_mock_mode(1, 2.0, 1.5, 0.1, 80.0, 1.0),
            self._make_mock_mode(2, 1.5, 0.2, 1.4, 2.0, 75.0),
        ]
        reader_instance = MagicMock()
        reader_instance.modes = modes
        MockReader.return_value = reader_instance
        mock_find.return_value = Path("dummy/Period.xbn")

        case = _make_completed_case("Case1")
        result = _build_modal_analysis_section([case], False)

        assert result is not None
        assert "固有値解析" in result
        assert "Case1" in result
        assert "2.0000" in result  # period of mode 1
        assert "1.5000" in result  # beta_x of mode 1
        assert "80.00" in result   # PM_X of mode 1

    @patch("app.services.report_generator._find_period_xbn")
    @patch("app.services.report_generator.PeriodXbnReader")
    def test_multiple_cases_in_table(self, MockReader, mock_find):
        """複数ケースのモード情報が含まれる。"""
        modes = [self._make_mock_mode(1, 2.0, 1.5, 0.1, 80.0, 1.0)]
        reader_instance = MagicMock()
        reader_instance.modes = modes
        MockReader.return_value = reader_instance
        mock_find.return_value = Path("dummy/Period.xbn")

        cases = [
            _make_completed_case("CaseA"),
            _make_completed_case("CaseB"),
        ]
        result = _build_modal_analysis_section(cases, False)

        assert result is not None
        assert "CaseA" in result
        assert "CaseB" in result

    @patch("app.services.report_generator._find_period_xbn")
    @patch("app.services.report_generator.PeriodXbnReader")
    def test_includes_chart_when_requested(self, MockReader, mock_find):
        """include_charts=True で β チャートが含まれる。"""
        modes = [
            self._make_mock_mode(1, 2.0, 1.5, 0.1, 80.0, 1.0),
            self._make_mock_mode(2, 1.5, 0.2, 1.4, 2.0, 75.0),
        ]
        reader_instance = MagicMock()
        reader_instance.modes = modes
        MockReader.return_value = reader_instance
        mock_find.return_value = Path("dummy/Period.xbn")

        case = _make_completed_case("Case1")
        result = _build_modal_analysis_section([case], True)

        assert result is not None
        assert "data:image/png;base64," in result
        assert "刺激係数" in result

    @patch("app.services.report_generator._find_period_xbn")
    @patch("app.services.report_generator.PeriodXbnReader")
    def test_no_chart_when_disabled(self, MockReader, mock_find):
        """include_charts=False で画像が含まれない。"""
        modes = [self._make_mock_mode(1, 2.0, 1.5, 0.1, 80.0, 1.0)]
        reader_instance = MagicMock()
        reader_instance.modes = modes
        MockReader.return_value = reader_instance
        mock_find.return_value = Path("dummy/Period.xbn")

        case = _make_completed_case("Case1")
        result = _build_modal_analysis_section([case], False)

        assert result is not None
        assert "data:image/png" not in result

    @patch("app.services.report_generator._find_period_xbn")
    @patch("app.services.report_generator.PeriodXbnReader")
    def test_dominant_direction_shown(self, MockReader, mock_find):
        """支配方向が正しく表示される。"""
        modes = [self._make_mock_mode(1, 2.0, 0.1, 1.8, 5.0, 85.0)]
        reader_instance = MagicMock()
        reader_instance.modes = modes
        MockReader.return_value = reader_instance
        mock_find.return_value = Path("dummy/Period.xbn")

        case = _make_completed_case("Case1")
        result = _build_modal_analysis_section([case], False)

        assert result is not None
        # β_Y=1.8 > β_X=0.1 → dominant_direction = "Y"
        assert ">Y<" in result

    @patch("app.services.report_generator._find_period_xbn")
    @patch("app.services.report_generator.PeriodXbnReader")
    def test_reader_exception_handled(self, MockReader, mock_find):
        """Period.xbn の読み込みに失敗しても例外にならない。"""
        MockReader.side_effect = RuntimeError("corrupt file")
        mock_find.return_value = Path("dummy/Period.xbn")

        case = _make_completed_case("Case1")
        result = _build_modal_analysis_section([case], False)

        # 全ケースが読めなければ None
        assert result is None
