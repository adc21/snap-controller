"""
tests/test_impulse_case_builder.py
インパルス応答解析ケース生成サービスのユニットテスト。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models.analysis_case import AnalysisCase
from app.services.impulse_case_builder import (
    ImpulseCaseSpec,
    build_impulse_case,
    list_dyc_cases,
)

EXAMPLE_S8I = Path(__file__).parent.parent / "example_model" / "example_3D" / "example_3D.s8i"


@pytest.fixture
def base_case() -> AnalysisCase:
    assert EXAMPLE_S8I.exists(), f"Missing example .s8i: {EXAMPLE_S8I}"
    return AnalysisCase(
        name="base", model_path=str(EXAMPLE_S8I), snap_exe_path="C:/fake/SNAP.exe",
    )


# ---------------------------------------------------------------------------
# ImpulseCaseSpec.validate
# ---------------------------------------------------------------------------

class TestSpecValidate:
    def test_valid_spec(self, tmp_path, base_case):
        spec = ImpulseCaseSpec(
            base_case=base_case, target_case_no=1,
            snap_wave_dir=str(tmp_path / "wave"),
        )
        spec.validate()

    def test_missing_base_case(self, tmp_path):
        with pytest.raises(ValueError, match="base_case"):
            ImpulseCaseSpec(
                base_case=None, target_case_no=1,
                snap_wave_dir=str(tmp_path),
            ).validate()

    def test_missing_model_path(self, tmp_path):
        case = AnalysisCase(name="x", model_path="")
        with pytest.raises(ValueError, match="model_path"):
            ImpulseCaseSpec(
                base_case=case, target_case_no=1, snap_wave_dir=str(tmp_path),
            ).validate()

    def test_nonexistent_model(self, tmp_path):
        case = AnalysisCase(name="x", model_path="C:/nowhere/nonexistent.s8i")
        with pytest.raises(FileNotFoundError):
            ImpulseCaseSpec(
                base_case=case, target_case_no=1, snap_wave_dir=str(tmp_path),
            ).validate()

    def test_bad_target_case(self, tmp_path, base_case):
        with pytest.raises(ValueError, match="target_case_no"):
            ImpulseCaseSpec(
                base_case=base_case, target_case_no=0,
                snap_wave_dir=str(tmp_path),
            ).validate()

    def test_empty_wave_dir(self, base_case):
        with pytest.raises(ValueError, match="snap_wave_dir"):
            ImpulseCaseSpec(
                base_case=base_case, target_case_no=1, snap_wave_dir="",
            ).validate()

    def test_zero_amax(self, tmp_path, base_case):
        with pytest.raises(ValueError, match="amax"):
            ImpulseCaseSpec(
                base_case=base_case, target_case_no=1,
                snap_wave_dir=str(tmp_path), amax=0.0,
            ).validate()

    def test_bad_impulse_index(self, tmp_path, base_case):
        with pytest.raises(ValueError, match="impulse_index"):
            ImpulseCaseSpec(
                base_case=base_case, target_case_no=1,
                snap_wave_dir=str(tmp_path),
                num_points=100, impulse_index=200,
            ).validate()


# ---------------------------------------------------------------------------
# list_dyc_cases
# ---------------------------------------------------------------------------

class TestListDycCases:
    def test_returns_cases(self):
        cases = list_dyc_cases(str(EXAMPLE_S8I))
        assert len(cases) > 0
        for c in cases:
            assert hasattr(c, "case_no")
            assert hasattr(c, "name")
            assert hasattr(c, "run_flag")

    def test_case_nos_are_one_indexed_and_unique(self):
        cases = list_dyc_cases(str(EXAMPLE_S8I))
        nos = [c.case_no for c in cases]
        assert all(n >= 1 for n in nos)
        assert len(set(nos)) == len(nos)


# ---------------------------------------------------------------------------
# build_impulse_case
# ---------------------------------------------------------------------------

class TestBuildImpulseCase:
    def test_creates_case(self, tmp_path, base_case):
        spec = ImpulseCaseSpec(
            base_case=base_case,
            target_case_no=1,
            snap_wave_dir=str(tmp_path / "wave"),
            amax=500.0,
            output_s8i_path=str(tmp_path / "out.s8i"),
        )
        new_case = build_impulse_case(spec)

        assert isinstance(new_case, AnalysisCase)
        assert new_case.id != base_case.id
        assert "インパルス" in new_case.name
        assert new_case.model_path == str(tmp_path / "out.s8i")
        assert Path(new_case.model_path).exists()

    def test_creates_wave_file(self, tmp_path, base_case):
        wave_dir = tmp_path / "wave"
        spec = ImpulseCaseSpec(
            base_case=base_case, target_case_no=1,
            snap_wave_dir=str(wave_dir), amax=1234.5,
            output_s8i_path=str(tmp_path / "out.s8i"),
        )
        build_impulse_case(spec)
        wv_files = list(wave_dir.glob("*.wv"))
        assert len(wv_files) == 1
        # ファイル名に amax が含まれる
        assert "1234" in wv_files[0].name

    def test_s8i_has_impulse_wave(self, tmp_path, base_case):
        from app.models.s8i_parser import parse_s8i
        spec = ImpulseCaseSpec(
            base_case=base_case, target_case_no=1,
            snap_wave_dir=str(tmp_path / "wave"),
            output_s8i_path=str(tmp_path / "out.s8i"),
        )
        new_case = build_impulse_case(spec)
        model = parse_s8i(new_case.model_path)
        # 対象ケースは run_flag=1, 波名は IMPULSE_... に更新
        target = next(c for c in model.dyc_cases if c.case_no == 1)
        assert target.run_flag == 1
        assert "IMPULSE_" in target.values[19]
        # 他ケースは run_flag=0
        for c in model.dyc_cases:
            if c.case_no != 1:
                assert c.run_flag == 0

    def test_custom_case_name(self, tmp_path, base_case):
        spec = ImpulseCaseSpec(
            base_case=base_case, target_case_no=1,
            snap_wave_dir=str(tmp_path / "wave"),
            case_name="MY_IMPULSE_RUN",
            output_s8i_path=str(tmp_path / "out.s8i"),
        )
        new_case = build_impulse_case(spec)
        assert new_case.name == "MY_IMPULSE_RUN"

    def test_unknown_case_no_raises(self, tmp_path, base_case):
        spec = ImpulseCaseSpec(
            base_case=base_case, target_case_no=9999,
            snap_wave_dir=str(tmp_path / "wave"),
            output_s8i_path=str(tmp_path / "out.s8i"),
        )
        with pytest.raises(ValueError, match="D9999"):
            build_impulse_case(spec)

    def test_auto_output_path_unique(self, tmp_path, base_case):
        """出力パス自動生成: 衝突時に _2 が付く。"""
        # このテストのために一時プロジェクトの s8i をコピー
        import shutil
        workdir = tmp_path / "work"
        workdir.mkdir()
        local_s8i = workdir / "example_3D.s8i"
        shutil.copy(EXAMPLE_S8I, local_s8i)
        # 同じフォルダから support files もコピー（parse_s8i 単体には不要だが write 時は関係ない）
        case = AnalysisCase(name="base", model_path=str(local_s8i))

        spec1 = ImpulseCaseSpec(
            base_case=case, target_case_no=1,
            snap_wave_dir=str(tmp_path / "wave"),
            amax=100.0,
        )
        new1 = build_impulse_case(spec1)

        spec2 = ImpulseCaseSpec(
            base_case=case, target_case_no=1,
            snap_wave_dir=str(tmp_path / "wave"),
            amax=100.0,
        )
        new2 = build_impulse_case(spec2)

        assert new1.model_path != new2.model_path
        assert Path(new1.model_path).exists()
        assert Path(new2.model_path).exists()
