"""controller.nap_converter の結合テスト。

SNAP.exe と example_3D.NAP が揃っている Windows 環境でのみ実行される。
ヘッドレス CI ではスキップ。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="controller.nap_converter は Windows 専用 (SNAP.exe GUI 自動化)",
)

DEFAULT_SNAP = Path(r"C:\Program Files\SNAP Ver.8\Snap.exe")
_SOURCE_NAP = (
    Path(__file__).resolve().parent.parent
    / "example_model"
    / "example_3D"
    / "example_3D.NAP"
)


@pytest.fixture
def nap_sample(tmp_path):
    """example_3D.NAP を tmp へコピーして返す。見つからなければスキップ。"""
    if not _SOURCE_NAP.exists():
        pytest.skip(f"サンプル NAP が無い: {_SOURCE_NAP}")
    dst = tmp_path / _SOURCE_NAP.name
    dst.write_bytes(_SOURCE_NAP.read_bytes())
    return dst


@pytest.fixture
def snap_exe_available():
    if not DEFAULT_SNAP.exists():
        pytest.skip(f"SNAP.exe が無い: {DEFAULT_SNAP}")
    # 環境変数で明示的に無効化されている場合もスキップ (CI 用)
    if os.environ.get("SKIP_SNAP_GUI") == "1":
        pytest.skip("SKIP_SNAP_GUI=1 によりスキップ")


def test_default_snap_exe_constant_points_to_ver8():
    """DEFAULT_SNAP_EXE が SNAP Ver.8 を向いていること (smoke)。"""
    from controller.nap_converter import DEFAULT_SNAP_EXE

    assert "SNAP Ver.8" in DEFAULT_SNAP_EXE
    assert DEFAULT_SNAP_EXE.lower().endswith("snap.exe")


def test_missing_nap_raises_file_not_found(tmp_path):
    from controller.nap_converter import convert_nap_to_s8i

    with pytest.raises(FileNotFoundError, match="NAP"):
        convert_nap_to_s8i(
            tmp_path / "does_not_exist.NAP",
            tmp_path / "out.s8i",
            snap_exe=str(DEFAULT_SNAP),
        )


def test_missing_snap_exe_raises_file_not_found(tmp_path):
    from controller.nap_converter import convert_nap_to_s8i

    fake_nap = tmp_path / "x.NAP"
    fake_nap.write_bytes(b"SNAP")
    with pytest.raises(FileNotFoundError, match="SNAP.exe"):
        convert_nap_to_s8i(
            fake_nap,
            tmp_path / "out.s8i",
            snap_exe=str(tmp_path / "nonexistent_snap.exe"),
        )


def test_convert_produces_valid_s8i(snap_exe_available, nap_sample, tmp_path):
    """実 SNAP.exe で変換し、生成 s8i が parse_s8i で読めることを確認。"""
    from app.models.s8i_parser import parse_s8i
    from controller.nap_converter import convert_nap_to_s8i

    out = tmp_path / "converted.s8i"
    result = convert_nap_to_s8i(nap_sample, out, snap_exe=str(DEFAULT_SNAP))

    assert result == out
    assert out.exists(), "出力 s8i が生成されていない"
    assert out.stat().st_size > 100_000, (
        f"生成 s8i が小さすぎる: {out.stat().st_size} bytes"
    )

    m = parse_s8i(out)
    assert m.num_nodes > 0
    assert m.num_floors > 0
    # example_3D は 21 層 916 節点 240 ダンパー
    assert m.num_floors == 21
    assert m.num_nodes == 916
    assert m.num_dampers == 240
