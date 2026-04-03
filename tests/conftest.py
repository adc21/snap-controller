"""
tests/conftest.py
Shared pytest fixtures for all tests.
"""

import pytest
from pathlib import Path
from app.models import AnalysisCase, Project
from app.models.performance_criteria import PerformanceCriteria


@pytest.fixture
def tmp_s8i_file(tmp_path):
    """
    Create a minimal .s8i file with known content (Shift-JIS encoding).
    """
    content = """TTL / 3,3,3,0,0,テストモデル
VER / 8
REM / テスト用最小限のモデル
ND / 1,0.0,0.0,0.0
ND / 2,0.0,0.0,5.0
ND / 3,0.0,0.0,10.0
FL / F1
FL / F2
FL / F3
"""
    s8i_file = tmp_path / "test_model.s8i"
    s8i_file.write_text(content, encoding="shift_jis")
    return str(s8i_file)


@pytest.fixture
def tmp_result_dir(tmp_path):
    """
    SNAP の出力フォーマット（Floor*.txt / Story*.txt）に準拠した
    モック結果ディレクトリを作成する。

    フォーマット:
        Floor*.txt : //No,Z1,Dx2,Dy3,Vx4,Vy5,Ax6,Ay7,RAx8,RAy9
                     単位 D=mm, V=mm/s, A=mm/s²
                     Z=0 行（地盤ノード）は入力 PGA として扱われる
        Story*.txt : //No,Z1,Sx2,Sy3,Qx4,Qy5,Cx6,Cy7,Mx8,My9,Drx10,Dry11
                     単位 S=mm, Q=kN, M=kN.m
                     Z=0 行（基部）は base_otm として扱われる
    """
    result_dir = tmp_path / "results"
    result_dir.mkdir()

    # Floor0.txt: 地盤(Z=0) + 3フロア(Z=4,8,12m)
    # No Z  Dx    Dy  Vx     Vy  Ax      Ay   RAx RAy
    floor_content = (
        "//FloorResult[GrpNo:0]\n"
        "//Unit:D:mm,V:mm/s,A:mm/s~2\n"
        "//No,Z1,Dx2,Dy3,Vx4,Vy5,Ax6,Ay7,RAx8,RAy9\n"
        "   1.00    0.00    0.00    0.00    0.00    0.00 3000.00    0.00    0.00    0.00\n"
        "   1.00    4.00   12.00    0.00  250.00    0.00 3500.00    0.00    0.00    0.00\n"
        "   2.00    8.00   25.00    0.00  350.00    0.00 4200.00    0.00    0.00    0.00\n"
        "   3.00   12.00   18.00    0.00  200.00    0.00 2800.00    0.00    0.00    0.00\n"
    )
    (result_dir / "Floor0.txt").write_text(floor_content, encoding="utf-8")

    # Story0.txt: 基部(Z=0) + 3層(Z=4,8,12m)
    # No Z  Sx    Sy  Qx     Qy  Cx    Cy  Mx      My    Drx    Dry
    story_content = (
        "//StoryResult[GrpNo:0]\n"
        "//Unit:S:mm,Q:kN,M:kN.m\n"
        "//No,Z1,Sx2,Sy3,Qx4,Qy5,Cx6,Cy7,Mx8,My9,Drx10,Dry11\n"
        "   0.00    0.00    0.00    0.00    0.00    0.00    0.00    0.00 8000.00    0.00    0.00    0.00\n"
        "   1.00    4.00    8.00    0.00  100.00    0.00    0.12    0.00 2500.00    0.00    0.0005    0.00\n"
        "   2.00    8.00   15.00    0.00  150.00    0.00    0.14    0.00 3200.00    0.00    0.0008    0.00\n"
        "   3.00   12.00   10.00    0.00   80.00    0.00    0.11    0.00 2100.00    0.00    0.0006    0.00\n"
    )
    (result_dir / "Story0.txt").write_text(story_content, encoding="utf-8")

    return str(result_dir)


@pytest.fixture
def sample_case():
    """Return a basic AnalysisCase with sample data."""
    case = AnalysisCase()
    case.name = "Sample Case"
    case.model_path = "/path/to/model.s8i"
    case.snap_exe_path = "/path/to/snap.exe"
    case.parameters = {"DAMPING": 0.05}
    case.damper_params = {"Cd": 500.0}
    case.notes = "Test case"
    return case


@pytest.fixture
def sample_project(tmp_s8i_file):
    """Return a Project with a few sample cases."""
    project = Project(name="Test Project")
    project.snap_exe_path = "/path/to/snap.exe"
    project.s8i_path = tmp_s8i_file

    # Add a few cases
    case1 = AnalysisCase()
    case1.name = "Case 1"
    case1.model_path = tmp_s8i_file
    case1.parameters = {"DAMPING": 0.05}
    project.add_case(case1)

    case2 = AnalysisCase()
    case2.name = "Case 2"
    case2.model_path = tmp_s8i_file
    case2.parameters = {"DAMPING": 0.08}
    project.add_case(case2)

    return project
