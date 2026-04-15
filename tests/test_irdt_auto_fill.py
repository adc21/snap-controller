"""
tests/test_irdt_auto_fill.py

`app.services.irdt_auto_fill` のユニットテスト。

S8iModel / PeriodXbn / MDFloor.xbn の読み込みは実ファイルを使わず、
軽いダミーオブジェクトで代替する。
"""
from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Dict, List

import pytest

from app.models.s8i_parser import Node, S8iModel
from app.services.irdt_auto_fill import (
    AutoFillResult,
    FloorInfo,
    ModeInfo,
    build_placement_specs,
    extract_floor_info,
    pick_interfloor_nodes,
)


# ----------------------------------------------------------------------
# extract_floor_info
# ----------------------------------------------------------------------

def _make_model_with_nodes(nodes_data: List[tuple]) -> S8iModel:
    """nodes_data: list of (id, z, mass) tuples. x,y=0."""
    model = S8iModel()
    for (nid, z, mass) in nodes_data:
        model.nodes[nid] = Node(id=nid, x=0.0, y=0.0, z=float(z), mass=float(mass))
    return model


def test_extract_floor_info_groups_by_z():
    # 3 層: z=0 (base), z=3, z=6
    model = _make_model_with_nodes([
        (1, 0.0, 100.0),
        (2, 0.0, 100.0),
        (3, 3.0, 80.0),
        (4, 3.0, 80.0),
        (5, 6.0, 60.0),
    ])
    floors = extract_floor_info(model)
    assert len(floors) == 3
    # Z 昇順にソート
    zs = [f.z for f in floors]
    assert zs == sorted(zs)
    # 質量合計
    totals = {f.z: f.mass for f in floors}
    assert totals[0.0] == pytest.approx(200.0)
    assert totals[3.0] == pytest.approx(160.0)
    assert totals[6.0] == pytest.approx(60.0)
    # 節点 ID が保持されている
    for f in floors:
        assert len(f.node_ids) >= 1


def test_extract_floor_info_empty_model():
    model = S8iModel()
    floors = extract_floor_info(model)
    assert floors == []


# ----------------------------------------------------------------------
# pick_interfloor_nodes
# ----------------------------------------------------------------------

def test_pick_interfloor_nodes_basic():
    floors = [
        FloorInfo("F1", 200.0, [1, 2], 0.0),
        FloorInfo("F2", 160.0, [3, 4], 3.0),
        FloorInfo("F3", 60.0, [5], 6.0),
    ]
    assert pick_interfloor_nodes(floors, 0) == (1, 3)
    assert pick_interfloor_nodes(floors, 1) == (3, 5)
    # 最上層以上は (0, 0)
    assert pick_interfloor_nodes(floors, 2) == (0, 0)
    assert pick_interfloor_nodes(floors, -1) == (0, 0)


def test_pick_interfloor_nodes_empty_ids():
    floors = [
        FloorInfo("F1", 200.0, [], 0.0),
        FloorInfo("F2", 160.0, [3, 4], 3.0),
    ]
    assert pick_interfloor_nodes(floors, 0) == (0, 3)


# ----------------------------------------------------------------------
# build_placement_specs
# ----------------------------------------------------------------------

def test_build_placement_specs_generates_specs():
    floors = [
        FloorInfo("F1", 200.0, [1, 2], 0.0),
        FloorInfo("F2", 160.0, [3, 4], 3.0),
        FloorInfo("F3", 60.0, [5], 6.0),
    ]
    mds = [10.0, 20.0, 30.0]
    cds = [100.0, 200.0, 300.0]
    kbs = [1000.0, 2000.0, 3000.0]
    specs = build_placement_specs(floors, mds, cds, kbs)
    # 最上層 (i=2) はスキップされるため 2 件
    assert len(specs) == 2
    assert specs[0].def_name == "IRDT1"
    assert specs[0].floor_name == "F2"  # 上側の層
    assert specs[0].node_i == 1
    assert specs[0].node_j == 3
    assert specs[0].mass_kN_s2_m == pytest.approx(10.0)
    assert specs[0].damping_kN_s_m == pytest.approx(100.0)
    assert specs[0].spring_kN_m == pytest.approx(1000.0)
    assert specs[0].def_only is False

    assert specs[1].def_name == "IRDT2"
    assert specs[1].floor_name == "F3"


def test_build_placement_specs_skips_zero_and_nan():
    floors = [
        FloorInfo("F1", 200.0, [1], 0.0),
        FloorInfo("F2", 160.0, [2], 3.0),
        FloorInfo("F3", 60.0, [3], 6.0),
    ]
    # md=0 と md=NaN はスキップ
    mds = [0.0, float("nan"), 30.0]
    cds = [100.0, 200.0, 300.0]
    kbs = [1000.0, 2000.0, 3000.0]
    specs = build_placement_specs(floors, mds, cds, kbs)
    # 層 0,1 はスキップ (md=0 / NaN)、層 2 は最上層なのでスキップ → 0 件
    assert specs == []


def test_build_placement_specs_def_only_flag():
    floors = [
        FloorInfo("F1", 200.0, [1], 0.0),
        FloorInfo("F2", 160.0, [2], 3.0),
    ]
    specs = build_placement_specs(
        floors, [10.0], [100.0], [1000.0], def_only=True, base_def_name="TEST"
    )
    assert len(specs) == 1
    assert specs[0].def_only is True
    assert specs[0].def_name == "TEST1"


def test_build_placement_specs_respects_shortest_list_length():
    floors = [
        FloorInfo("F1", 200.0, [1], 0.0),
        FloorInfo("F2", 160.0, [2], 3.0),
        FloorInfo("F3", 60.0, [3], 6.0),
    ]
    # mds は 1 件のみ → 最初の層しか扱わない
    mds = [10.0]
    cds = [100.0, 200.0, 300.0]
    kbs = [1000.0, 2000.0, 3000.0]
    specs = build_placement_specs(floors, mds, cds, kbs)
    assert len(specs) == 1
    assert specs[0].def_name == "IRDT1"


# ----------------------------------------------------------------------
# AutoFillResult データクラスの補助プロパティ
# ----------------------------------------------------------------------

def test_auto_fill_result_properties():
    floors = [
        FloorInfo("F1", 100.0, [1], 0.0),
        FloorInfo("F2", 80.0, [2], 3.0),
    ]
    modes = [
        ModeInfo(mode_no=1, period=1.0, omega=2 * math.pi, dominant_direction="X",
                 shape=[1.0, 0.5]),
        ModeInfo(mode_no=2, period=0.5, omega=4 * math.pi, dominant_direction="X",
                 shape=[1.0, -0.5]),
    ]
    result = AutoFillResult(floors=floors, modes=modes)
    assert result.n_floors == 2
    assert result.floor_masses == [100.0, 80.0]
    assert result.floor_names == ["F1", "F2"]
    assert result.get_mode(1).period == pytest.approx(1.0)
    assert result.get_mode(3) is None
    assert result.has_shape() is True

    # shape なしモードのみでは has_shape=False
    modes_no_shape = [
        ModeInfo(mode_no=1, period=1.0, omega=2 * math.pi, dominant_direction="X"),
    ]
    r2 = AutoFillResult(floors=floors, modes=modes_no_shape)
    assert r2.has_shape() is False


# ----------------------------------------------------------------------
# auto_fill_from_project (軽い統合テスト: parse_s8i をモンキーパッチ)
# ----------------------------------------------------------------------

def test_auto_fill_from_project_raises_without_s8i():
    from app.services.irdt_auto_fill import auto_fill_from_project

    class FakeProject:
        s8i_path = ""
        cases = []

    with pytest.raises(ValueError):
        auto_fill_from_project(FakeProject())


def test_auto_fill_from_project_file_not_found(monkeypatch, tmp_path):
    """s8i は読めるが Period.xbn が見つからない → FileNotFoundError。"""
    from app.services import irdt_auto_fill

    fake_model = _make_model_with_nodes([
        (1, 0.0, 100.0), (2, 3.0, 80.0),
    ])

    def _fake_parse(path):
        return fake_model

    monkeypatch.setattr(irdt_auto_fill, "parse_s8i", _fake_parse)

    class FakeCase:
        name = "test_case"
        model_path = str(tmp_path / "nonexistent.s8i")
        output_dir = str(tmp_path / "noresult")

    class FakeProject:
        s8i_path = str(tmp_path / "dummy.s8i")
        s8i_model = None
        cases = [FakeCase()]
        snap_work_dir = ""

    with pytest.raises(FileNotFoundError):
        irdt_auto_fill.auto_fill_from_project(FakeProject(), run_if_missing=False)
