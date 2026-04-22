"""
tests/test_impulse_wave_writer.py
Unit tests for the SNAP .wv impulse wave writer.
"""

from pathlib import Path

import pytest

from app.services.impulse_wave_writer import (
    DEFAULT_IMPULSE_INDEX,
    DEFAULT_NUM_POINTS,
    ImpulseWaveSpec,
    make_impulse_filename,
    write_impulse_wave,
)


class TestImpulseWaveSpec:
    def test_defaults(self):
        spec = ImpulseWaveSpec(amax=1000.0)
        assert spec.num_points == DEFAULT_NUM_POINTS
        assert spec.impulse_index == DEFAULT_IMPULSE_INDEX
        assert spec.dt > 0

    def test_validate_rejects_zero_amax(self):
        spec = ImpulseWaveSpec(amax=0.0)
        with pytest.raises(ValueError, match="amax"):
            spec.validate()

    def test_validate_rejects_bad_index(self):
        spec = ImpulseWaveSpec(amax=100.0, num_points=100, impulse_index=200)
        with pytest.raises(ValueError, match="impulse_index"):
            spec.validate()

    def test_validate_rejects_zero_dt(self):
        spec = ImpulseWaveSpec(amax=100.0, dt=0.0)
        with pytest.raises(ValueError, match="dt"):
            spec.validate()


class TestWriteImpulseWave:
    def test_writes_file(self, tmp_path):
        out = tmp_path / "impulse.wv"
        spec = ImpulseWaveSpec(amax=500.0, dt=0.01, num_points=128, impulse_index=9)
        p = write_impulse_wave(out, spec)
        assert p.exists()
        assert p == out

    def test_header_format(self, tmp_path):
        out = tmp_path / "impulse.wv"
        spec = ImpulseWaveSpec(
            amax=987.65, dt=0.02, num_points=100, impulse_index=9,
            filename="MY_IMPULSE",
        )
        write_impulse_wave(out, spec)
        text = out.read_text("ascii")
        assert 'VERSION="' in text
        assert 'FILENAME="MY_IMPULSE"' in text
        assert 'DT="0.02"' in text
        assert 'AMAX="987.650"' in text
        assert 'DATA' in text

    def test_crlf_line_endings(self, tmp_path):
        out = tmp_path / "impulse.wv"
        spec = ImpulseWaveSpec(amax=100.0, num_points=32, impulse_index=5)
        write_impulse_wave(out, spec)
        raw = out.read_bytes()
        # Every non-empty line must end with CRLF
        assert raw.count(b"\r\n") >= 32 + 10  # values + header

    def test_8192_points_and_single_impulse(self, tmp_path):
        out = tmp_path / "impulse.wv"
        spec = ImpulseWaveSpec(amax=1234.5, num_points=8192, impulse_index=9)
        write_impulse_wave(out, spec)
        lines = out.read_text("ascii").splitlines()
        # 10 header lines + 8192 values
        assert len(lines) == 10 + 8192
        data_values = lines[10:]
        assert len(data_values) == 8192
        nonzero = [(i, v) for i, v in enumerate(data_values) if v != "0"]
        assert len(nonzero) == 1
        assert nonzero[0] == (9, "1234.500")

    def test_creates_parent_dirs(self, tmp_path):
        out = tmp_path / "nested" / "dir" / "impulse.wv"
        spec = ImpulseWaveSpec(amax=100.0, num_points=32, impulse_index=5)
        write_impulse_wave(out, spec)
        assert out.exists()

    def test_negative_amax_is_accepted(self, tmp_path):
        """負の加速度（例: -1000 gal）もインパルスとして有効。"""
        out = tmp_path / "impulse.wv"
        spec = ImpulseWaveSpec(amax=-1000.0, num_points=64, impulse_index=5)
        write_impulse_wave(out, spec)
        lines = out.read_text("ascii").splitlines()
        data = lines[10:]
        assert data[5] == "-1000.000"
        assert data[0] == "0"


class TestMakeImpulseFilename:
    def test_includes_case_id_and_amax(self):
        name = make_impulse_filename("abc12345xyz", 1234.5)
        assert "IMPULSE_" in name
        assert "1234" in name
        # Only uses first 8 chars of case_id
        assert "abc12345" in name
