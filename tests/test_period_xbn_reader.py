"""
tests/test_period_xbn_reader.py
==============================

Period.xbn リーダーの 3D/平面レイアウト判別回帰テスト。

SNAP の Period.xbn は構造形式 (TTL[0]) によってバイナリレイアウトが
異なる:

  - 立体フレーム (3D): β は rec[2..6], PM は rec[w_pos+2..w_pos+5]
  - 平面フレーム:       β は rec[1]  , PM は rec[w_pos+1]

本テストは両レイアウトを手組みしたバイト列で再現し、リーダーが
正しく自動判別・値取得できるかを確認する。
"""
from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from controller.binary.period_xbn_reader import PeriodXbnReader


def _pack_floats(vals):
    return b"".join(struct.pack("<f", v) for v in vals)


def _build_planar_bytes(T: float = 3.973835, beta: float = 1.0, pm: float = 100.0) -> bytes:
    """example_shear.s8i の Period.xbn (80 バイト / 1 モード) を再現する。"""
    omega = 2 * math.pi / T
    # ヘッダ: 4 int + モード番号(1) + pad(1) = 6 float/int
    header_ints = struct.pack("<4i", 524353, 1, 9, 1) + struct.pack("<2i", 1, 0)
    # モードレコード (14 float):
    # rec[0]=0, rec[1]=β, rec[2..6]=0, rec[7]=T, rec[8]=ω, rec[9]=PM, rec[10..13]=0
    rec = [0.0, beta, 0.0, 0.0, 0.0, 0.0, 0.0, T, omega, pm, 0.0, 0.0, 0.0, 0.0]
    return header_ints + _pack_floats(rec)


def _build_3d_bytes(
    T: float = 2.850303, beta_x: float = 1.349314, pm_x: float = 77.72932,
    num_modes: int = 1,
) -> bytes:
    """example_3D の Period.xbn (num_modes モード) を再現する。"""
    omega = 2 * math.pi / T
    # ヘッダ: 4 int + モード番号配列 num_modes int
    header_ints = struct.pack("<4i", 524353, 1, 81, num_modes)
    header_ints += b"".join(struct.pack("<i", i + 1) for i in range(num_modes))
    # Padding: 14 float ヘッダに揃える (3D 観測では 14 float ヘッダ)
    # 4 + num_modes ints しかないなら 14 - (4 + num_modes) 分パディング
    pad_count = 14 - (4 + num_modes)
    body = b""
    if pad_count > 0:
        body += _pack_floats([0.0] * pad_count)
    # 各モードのレコード: rec[0..1]=0, rec[2]=β_X, rec[3..6]=0,
    #                    rec[7]=T, rec[8]=ω, rec[9]=0, rec[10]=PM_X, rec[11..13]=0
    for m in range(num_modes):
        rec = [0.0, 0.0, beta_x, 0.0, 0.0, 0.0, 0.0, T, omega, 0.0, pm_x, 0.0, 0.0, 0.0]
        body += _pack_floats(rec)
    # trailing padding (3D では最後に 1 float 0 がある観測)
    body += _pack_floats([0.0])
    return header_ints + body


# ---------------------------------------------------------------------------
# 平面モデル
# ---------------------------------------------------------------------------


class TestPlanarLayout:
    """平面フレーム Period.xbn のレイアウト判別と値取得。"""

    def test_planar_auto_detect_reads_beta_correctly(self, tmp_path: Path) -> None:
        p = tmp_path / "Period.xbn"
        p.write_bytes(_build_planar_bytes(T=3.97, beta=1.0, pm=100.0))
        reader = PeriodXbnReader(p)

        assert reader.num_modes == 1
        assert len(reader.modes) == 1
        assert reader.layout_is_planar is True
        m = reader.modes[0]
        assert m.period == pytest.approx(3.97, rel=1e-3)
        assert m.beta["X"] == pytest.approx(1.0, rel=1e-5)
        assert m.pm["X"] == pytest.approx(100.0, rel=1e-5)
        # 平面では Y/Z 方向は 0 のまま
        assert m.beta["Y"] == 0.0
        assert m.beta["Z"] == 0.0
        assert m.pm["Y"] == 0.0

    def test_planar_with_explicit_structure_type(self, tmp_path: Path) -> None:
        p = tmp_path / "Period.xbn"
        p.write_bytes(_build_planar_bytes())
        reader = PeriodXbnReader(p, structure_type=1)

        assert reader.layout_is_planar is True
        assert reader.modes[0].beta["X"] == pytest.approx(1.0)
        assert reader.modes[0].pm["X"] == pytest.approx(100.0)

    def test_planar_y_direction(self, tmp_path: Path) -> None:
        """Y 方向指定の場合、β/PM は Y キーに格納される。"""
        p = tmp_path / "Period.xbn"
        p.write_bytes(_build_planar_bytes(beta=0.9, pm=95.0))
        reader = PeriodXbnReader(p, structure_type=1, planar_direction="Y")

        m = reader.modes[0]
        assert m.beta["Y"] == pytest.approx(0.9)
        assert m.beta["X"] == 0.0
        assert m.pm["Y"] == pytest.approx(95.0)
        assert m.pm["X"] == 0.0


# ---------------------------------------------------------------------------
# 3D モデル
# ---------------------------------------------------------------------------


class Test3dLayout:
    """立体フレーム Period.xbn のレイアウト判別と値取得 (回帰)。"""

    def test_3d_auto_detect(self, tmp_path: Path) -> None:
        p = tmp_path / "Period.xbn"
        p.write_bytes(_build_3d_bytes(num_modes=1))
        reader = PeriodXbnReader(p)

        assert reader.num_modes == 1
        assert reader.layout_is_planar is False
        m = reader.modes[0]
        assert m.beta["X"] == pytest.approx(1.349314, rel=1e-5)
        assert m.pm["X"] == pytest.approx(77.72932, rel=1e-5)
        assert m.beta["Y"] == 0.0  # この簡易ケースでは X のみ

    def test_3d_explicit_structure_type(self, tmp_path: Path) -> None:
        """structure_type=0 を渡しても同じ結果。"""
        p = tmp_path / "Period.xbn"
        p.write_bytes(_build_3d_bytes(num_modes=1))
        reader = PeriodXbnReader(p, structure_type=0)

        assert reader.layout_is_planar is False
        assert reader.modes[0].beta["X"] == pytest.approx(1.349314, rel=1e-5)


# ---------------------------------------------------------------------------
# 実ファイルでの回帰テスト (可能なら)
# ---------------------------------------------------------------------------


_PLANAR_REAL = Path(
    r"D:/Kakemoto/kozosystem/SNAPV8/work/example_shear__example_shear_Case-01/D1/Period.xbn"
)
_3D_REAL = Path(r"D:/Kakemoto/kozosystem/SNAPV8/work/example_3D/D1/Period.xbn")


@pytest.mark.skipif(not _PLANAR_REAL.exists(), reason="平面実サンプルが未生成")
def test_real_planar_file_reads_nonzero_beta_and_pm() -> None:
    reader = PeriodXbnReader(_PLANAR_REAL)
    assert reader.layout_is_planar is True
    assert reader.modes, "mode が 1 個以上あるはず"
    m = reader.modes[0]
    assert m.period > 0
    assert m.beta["X"] != 0.0, "平面モデルで β が全ゼロなら旧バグ再発"
    assert m.pm["X"] > 0.0, "平面モデルで PM が全ゼロなら旧バグ再発"


@pytest.mark.skipif(not _3D_REAL.exists(), reason="3D 実サンプルが未生成")
def test_real_3d_file_reads_multi_mode() -> None:
    reader = PeriodXbnReader(_3D_REAL)
    assert reader.layout_is_planar is False
    assert len(reader.modes) >= 5
    # X 方向支配モードが存在
    assert any(m.dominant_direction == "X" for m in reader.modes)
    # Y 方向支配モードが存在
    assert any(m.dominant_direction == "Y" for m in reader.modes)


# ---------------------------------------------------------------------------
# s8i_parser が TTL から structure_type を拾うこと
# ---------------------------------------------------------------------------


def test_s8i_parser_extracts_structure_type(tmp_path: Path) -> None:
    from app.models.s8i_parser import parse_s8i

    # TTL[0] = 1 → 平面
    planar_s8i = tmp_path / "planar.s8i"
    planar_s8i.write_text(
        "REM /\nVER / 8.1\nTTL / 1,2,0,0,9.80665,Test,,,\n", encoding="shift_jis"
    )
    model = parse_s8i(str(planar_s8i))
    assert model.structure_type == 1

    # TTL[0] = 0 → 立体
    three_d_s8i = tmp_path / "3d.s8i"
    three_d_s8i.write_text(
        "REM /\nVER / 8.1\nTTL / 0,2,0,0,9.80665,Test,,,\n", encoding="shift_jis"
    )
    model2 = parse_s8i(str(three_d_s8i))
    assert model2.structure_type == 0
