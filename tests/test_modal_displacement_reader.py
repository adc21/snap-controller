"""
tests/test_modal_displacement_reader.py
========================================

controller.binary.modal_displacement_reader の回帰テスト。

- 合成バイナリによるヘッダ/データ解釈の確認
- DOF slot マッピング (planar / 3D) の確認
- mode_shape / dominant_direction / available_directions API の確認
- (オプション) 実 SNAP 出力ファイルに対する統合テスト — ファイルが
  存在しない環境ではスキップ
"""

from __future__ import annotations

from pathlib import Path
from typing import List

import numpy as np
import pytest

from controller.binary.modal_displacement_reader import ModalDisplacementReader


# ---------------------------------------------------------------------------
# 合成バイナリ生成
# ---------------------------------------------------------------------------

def _make_md_bytes(
    *,
    magic: int,
    num_modes: int,
    num_items: int,
    dof_per_item: int,
    data: np.ndarray,
    meta_floats: int = 0,
) -> bytes:
    """SNAP の MDFloor/MDNode レイアウトに近い合成バイナリを作る。

    レイアウト
    ----------
    int32  magic
    int32  sub_count (=1)
    int32  values_per_mode (= num_items × dof_per_item)
    int32  num_modes
    int32  mode_no[num_modes]
    int32  meta[meta_floats]  (任意のパディング)
    float  data[num_modes × num_items × dof_per_item]

    data.shape == (num_modes, num_items, dof_per_item) を期待する。
    """
    assert data.shape == (num_modes, num_items, dof_per_item)
    vpm = num_items * dof_per_item
    header = np.array([magic, 1, vpm, num_modes], dtype=np.int32).tobytes()
    mode_nos = np.arange(1, num_modes + 1, dtype=np.int32).tobytes()
    meta = np.zeros(meta_floats, dtype=np.int32).tobytes()
    body = data.astype(np.float32).tobytes()
    return header + mode_nos + meta + body


# ---------------------------------------------------------------------------
# ヘッダ / data 解釈
# ---------------------------------------------------------------------------

class TestParseBasic:
    def test_mdfloor_3d_shape(self, tmp_path: Path):
        """MDFloor (dof=3) の基本的な shape 解釈。"""
        num_modes, num_items, dof = 5, 10, 3
        rng = np.random.default_rng(42)
        data = rng.standard_normal((num_modes, num_items, dof)).astype(np.float32)

        p = tmp_path / "MDFloor.xbn"
        p.write_bytes(_make_md_bytes(
            magic=524296, num_modes=num_modes, num_items=num_items,
            dof_per_item=dof, data=data, meta_floats=3,
        ))

        md = ModalDisplacementReader(p, dof_per_item=3)
        assert md.magic == 524296
        assert md.num_modes == num_modes
        assert md.num_items == num_items
        assert md.dof_per_item == 3
        assert md.values_per_mode == num_items * dof
        assert md.data is not None
        assert md.data.shape == (num_modes, num_items, dof)
        np.testing.assert_allclose(md.data, data, atol=1e-5)

    def test_mdnode_6dof_shape(self, tmp_path: Path):
        """MDNode (dof=6) の基本的な shape 解釈。"""
        num_modes, num_items, dof = 3, 8, 6
        data = np.arange(
            num_modes * num_items * dof, dtype=np.float32
        ).reshape(num_modes, num_items, dof)

        p = tmp_path / "MDNode.xbn"
        p.write_bytes(_make_md_bytes(
            magic=524292, num_modes=num_modes, num_items=num_items,
            dof_per_item=dof, data=data, meta_floats=0,
        ))

        md = ModalDisplacementReader(p, dof_per_item=6)
        assert md.num_modes == num_modes
        assert md.num_items == num_items
        assert md.dof_per_item == 6
        np.testing.assert_allclose(md.data, data, atol=1e-5)

    def test_mode_numbers_extracted(self, tmp_path: Path):
        num_modes, num_items, dof = 4, 2, 3
        data = np.zeros((num_modes, num_items, dof), dtype=np.float32)
        p = tmp_path / "MDFloor.xbn"
        p.write_bytes(_make_md_bytes(
            magic=524296, num_modes=num_modes, num_items=num_items,
            dof_per_item=dof, data=data, meta_floats=4,
        ))
        md = ModalDisplacementReader(p, dof_per_item=3)
        assert md.mode_numbers == [1, 2, 3, 4]

    def test_missing_file_produces_empty_state(self, tmp_path: Path):
        md = ModalDisplacementReader(tmp_path / "does_not_exist.xbn", dof_per_item=3)
        assert md.data is None
        assert md.num_modes == 0
        assert md.num_items == 0

    def test_dof_mismatch_rejected(self, tmp_path: Path):
        """values_per_mode が dof で割り切れないファイルはパースを中止する。"""
        # vpm=7 は 3 で割り切れない
        header = np.array([524296, 1, 7, 2], dtype=np.int32).tobytes()
        body = np.zeros(14, dtype=np.float32).tobytes()
        p = tmp_path / "bad.xbn"
        p.write_bytes(header + np.zeros(2, dtype=np.int32).tobytes() + body)

        md = ModalDisplacementReader(p, dof_per_item=3)
        assert md.data is None


# ---------------------------------------------------------------------------
# slot ↔ direction マッピング
# ---------------------------------------------------------------------------

class TestSlotMapping:
    """planar vs 3D で Dx の slot index が異なることを確認する。"""

    def test_planar_mdfloor_dx_is_slot0(self):
        md = ModalDisplacementReader.__new__(ModalDisplacementReader)
        md.dof_per_item = 3
        md.structure_type = 1  # planar
        assert md.slot_for_direction("Dx") == 0
        assert md.direction_for_slot(0) == "Dx"
        assert md.direction_for_slot(1) == "Dy"

    def test_3d_mdfloor_dx_is_slot1(self):
        """3D の MDFloor は slot 0 が Rz、slot 1 が Dx。"""
        md = ModalDisplacementReader.__new__(ModalDisplacementReader)
        md.dof_per_item = 3
        md.structure_type = 0  # 3D
        assert md.slot_for_direction("Dx") == 1
        assert md.slot_for_direction("Dy") == 2
        assert md.direction_for_slot(0) == "Rz"

    def test_planar_mdnode_full_6dof(self):
        md = ModalDisplacementReader.__new__(ModalDisplacementReader)
        md.dof_per_item = 6
        md.structure_type = 1
        assert md.slot_for_direction("Dx") == 0
        assert md.slot_for_direction("Rz") == 5

    def test_3d_mdnode_shifted(self):
        """3D MDNode は slot 0 = Rz, 1 = Dx, 2 = Dy, 3 = Dz。"""
        md = ModalDisplacementReader.__new__(ModalDisplacementReader)
        md.dof_per_item = 6
        md.structure_type = 0
        assert md.slot_for_direction("Rz") == 0
        assert md.slot_for_direction("Dx") == 1
        assert md.slot_for_direction("Dy") == 2
        assert md.slot_for_direction("Dz") == 3

    def test_unknown_direction_returns_none(self):
        md = ModalDisplacementReader.__new__(ModalDisplacementReader)
        md.dof_per_item = 3
        md.structure_type = 1
        assert md.slot_for_direction("ABC") is None

    def test_default_map_for_unknown_struct_type(self):
        """未知の structure_type でも落ちずに機能する。"""
        md = ModalDisplacementReader.__new__(ModalDisplacementReader)
        md.dof_per_item = 3
        md.structure_type = None
        m = md.slot_map()
        assert len(m) == 3  # dof に合わせたスロット数


# ---------------------------------------------------------------------------
# mode_shape / dominant_direction
# ---------------------------------------------------------------------------

class TestModeShapeAPI:
    def _make_reader(self, tmp_path: Path, *, structure_type: int) -> ModalDisplacementReader:
        """各 slot にユニークなシグネチャを埋め込んだ 3D MDFloor を作る。

        data[m, i, s] = (m + 1) * 100 + (i + 1) * 10 + s
        """
        num_modes, num_items, dof = 3, 5, 3
        data = np.zeros((num_modes, num_items, dof), dtype=np.float32)
        for m in range(num_modes):
            for i in range(num_items):
                for s in range(dof):
                    data[m, i, s] = (m + 1) * 100 + (i + 1) * 10 + s
        p = tmp_path / "MDFloor.xbn"
        p.write_bytes(_make_md_bytes(
            magic=524296, num_modes=num_modes, num_items=num_items,
            dof_per_item=dof, data=data,
        ))
        return ModalDisplacementReader(p, dof_per_item=3, structure_type=structure_type)

    def test_mode_shape_3d_dx(self, tmp_path: Path):
        """3D で Dx 指定 → slot 1 を返す。"""
        md = self._make_reader(tmp_path, structure_type=0)
        arr = md.mode_shape(0, "Dx")  # mode 1, Dx (slot 1)
        # data[0, i, 1] = 100 + (i+1)*10 + 1 = 111, 121, 131, 141, 151
        assert arr.shape == (5,)
        np.testing.assert_allclose(arr, [111, 121, 131, 141, 151])

    def test_mode_shape_planar_dx(self, tmp_path: Path):
        """planar で Dx 指定 → slot 0 を返す。"""
        md = self._make_reader(tmp_path, structure_type=1)
        arr = md.mode_shape(0, "Dx")  # mode 1, Dx (slot 0)
        # data[0, i, 0] = 100 + (i+1)*10 + 0 = 110, 120, 130, 140, 150
        np.testing.assert_allclose(arr, [110, 120, 130, 140, 150])

    def test_mode_shape_unknown_direction_zero(self, tmp_path: Path):
        md = self._make_reader(tmp_path, structure_type=0)
        arr = md.mode_shape(0, "ZZZ")
        assert arr.shape == (5,)
        assert np.allclose(arr, 0.0)

    def test_mode_shape_out_of_range_mode_zero(self, tmp_path: Path):
        md = self._make_reader(tmp_path, structure_type=0)
        arr = md.mode_shape(99, "Dx")
        assert arr.shape == (5,)
        assert np.allclose(arr, 0.0)

    def test_dominant_direction_picks_largest_slot(self, tmp_path: Path):
        """最大の slot total を持つ direction が返る。"""
        num_modes, num_items, dof = 2, 4, 3
        data = np.zeros((num_modes, num_items, dof), dtype=np.float32)
        # mode 0: slot 1 (3D で Dx) が大きい
        data[0, :, 1] = [1.0, 2.0, 3.0, 4.0]
        data[0, :, 0] = [0.01, 0.01, 0.01, 0.01]
        # mode 1: slot 2 (3D で Dy) が大きい
        data[1, :, 2] = [5.0, 5.0, 5.0, 5.0]

        p = tmp_path / "MDFloor.xbn"
        p.write_bytes(_make_md_bytes(
            magic=524296, num_modes=num_modes, num_items=num_items,
            dof_per_item=dof, data=data,
        ))
        md = ModalDisplacementReader(p, dof_per_item=3, structure_type=0)
        assert md.dominant_direction(0) == "Dx"
        assert md.dominant_direction(1) == "Dy"

    def test_available_directions_filters_zero_slots(self, tmp_path: Path):
        """全て 0 の slot は available_directions に出ない。"""
        num_modes, num_items, dof = 1, 3, 3
        data = np.zeros((num_modes, num_items, dof), dtype=np.float32)
        data[0, :, 1] = [1.0, 2.0, 3.0]  # 3D Dx のみ非ゼロ

        p = tmp_path / "MDFloor.xbn"
        p.write_bytes(_make_md_bytes(
            magic=524296, num_modes=num_modes, num_items=num_items,
            dof_per_item=dof, data=data,
        ))
        md = ModalDisplacementReader(p, dof_per_item=3, structure_type=0)
        dirs = md.available_directions()
        assert "Dx" in dirs
        assert "Dy" not in dirs
        assert "Rz" not in dirs


# ---------------------------------------------------------------------------
# 実ファイル統合テスト (オプション)
# ---------------------------------------------------------------------------

_REAL_3D = Path(r"D:/Kakemoto/kozosystem/SNAPV8/work/example_3D/D1")
_REAL_PLANAR = Path(
    r"D:/Kakemoto/kozosystem/SNAPV8/work/example_shear__example_shear_Case-01/D1"
)


@pytest.mark.skipif(
    not (_REAL_3D / "MDFloor.xbn").exists(),
    reason="SNAP 実出力ファイルが利用不可",
)
class TestRealFiles3D:
    def test_3d_mdfloor_reads(self):
        md = ModalDisplacementReader(
            _REAL_3D / "MDFloor.xbn", dof_per_item=3, structure_type=0
        )
        assert md.data is not None
        assert md.num_modes > 0
        assert md.num_items > 0

    def test_3d_mdnode_reads(self):
        md = ModalDisplacementReader(
            _REAL_3D / "MDNode.xbn", dof_per_item=6, structure_type=0
        )
        assert md.data is not None
        assert md.num_modes > 0
        # 各モードで何らかの direction に非ゼロがある
        for m in range(md.num_modes):
            arr = np.abs(md.data[m]).sum()
            assert arr > 0, f"mode {m+1} が全ゼロ"

    def test_3d_mdfloor_mode1_peak_is_dx(self):
        """3D のモード 1 は X 方向支配のはず (刺激係数も β_X が最大)。"""
        md = ModalDisplacementReader(
            _REAL_3D / "MDFloor.xbn", dof_per_item=3, structure_type=0
        )
        dom = md.dominant_direction(0)
        assert dom == "Dx", f"expected Dx but got {dom}"


@pytest.mark.skipif(
    not (_REAL_PLANAR / "MDFloor.xbn").exists(),
    reason="SNAP 実 planar 出力ファイルが利用不可",
)
class TestRealFilesPlanar:
    def test_planar_mdfloor_reads(self):
        md = ModalDisplacementReader(
            _REAL_PLANAR / "MDFloor.xbn", dof_per_item=3, structure_type=1
        )
        assert md.data is not None
        assert md.num_modes > 0

    def test_planar_mdfloor_mode1_is_dx(self):
        md = ModalDisplacementReader(
            _REAL_PLANAR / "MDFloor.xbn", dof_per_item=3, structure_type=1
        )
        dom = md.dominant_direction(0)
        assert dom == "Dx", f"expected Dx but got {dom}"
