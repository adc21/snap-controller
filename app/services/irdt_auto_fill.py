"""
app/services/irdt_auto_fill.py

iRDT MDOF ダイアログ用の s8i 連携ヘルパー。

提供する機能:
- s8i モデルから各層の質量合計と層ごとの節点 ID リストを取得
- 既存の解析結果 (Period.xbn + MDFloor.xbn) からモード情報を取得
- 固有値解析結果がなければ, 任意のケースを SNAP で短時間実行して取得
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.models.analysis_case import AnalysisCase
from app.models.s8i_parser import S8iModel, parse_s8i

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# データクラス
# ----------------------------------------------------------------------


@dataclass
class FloorInfo:
    """層ごとの質量・節点情報。"""
    name: str                     # 層名 (e.g. "F1")
    mass: float                   # 層の合計質量 [ton 単位、s8i の ND mass を合算]
    node_ids: List[int]           # 層に属する節点 ID リスト
    z: float                      # 層の Z 座標


@dataclass
class ModeInfo:
    """1 つの固有モードに関する情報。"""
    mode_no: int
    period: float                 # 周期 [s]
    omega: float                  # 円振動数 [rad/s]
    dominant_direction: str       # "X" | "Y" | "Z" | ...
    # shape[i] = 層 i のモード振幅 (dominant_direction 成分)
    shape: List[float] = field(default_factory=list)


@dataclass
class AutoFillResult:
    """s8i から取得した自動入力用データ。"""
    floors: List[FloorInfo]
    modes: List[ModeInfo]
    source_case_name: str = ""
    source_result_dir: str = ""
    warnings: List[str] = field(default_factory=list)

    @property
    def n_floors(self) -> int:
        return len(self.floors)

    @property
    def floor_masses(self) -> List[float]:
        return [f.mass for f in self.floors]

    @property
    def floor_names(self) -> List[str]:
        return [f.name for f in self.floors]

    def get_mode(self, mode_no: int) -> Optional[ModeInfo]:
        for m in self.modes:
            if m.mode_no == mode_no:
                return m
        return None

    def has_shape(self) -> bool:
        return any(m.shape for m in self.modes)


# ----------------------------------------------------------------------
# s8i 層情報抽出
# ----------------------------------------------------------------------


def extract_floor_info(model: S8iModel) -> List[FloorInfo]:
    """
    S8iModel から層ごとの質量と節点 ID を抽出します。

    実装は `S8iModel.get_floor_nodes()` を利用し、各層の質量は所属節点の
    `Node.mass` を合算します。
    """
    floor_nodes = model.get_floor_nodes()
    floors: List[FloorInfo] = []
    for name, node_ids in floor_nodes.items():
        masses = [model.nodes[nid].mass for nid in node_ids if nid in model.nodes]
        total = float(sum(masses))
        # Z 座標: 代表として最初の節点の z を使う
        z = float(model.nodes[node_ids[0]].z) if node_ids else 0.0
        floors.append(FloorInfo(name=name, mass=total, node_ids=list(node_ids), z=z))
    # Z 座標昇順に並べる
    floors.sort(key=lambda f: f.z)
    return floors


def pick_interfloor_nodes(floors: List[FloorInfo], i: int) -> Tuple[int, int]:
    """
    層 i と層 i+1 の間にダンパーを配置する際の代表節点 (node_i, node_j) を返します。

    シンプルなヒューリスティック: 各層の先頭の節点 ID を使う。
    節点が無い場合は 0 を返す。
    """
    if i < 0 or i + 1 >= len(floors):
        return (0, 0)
    lower = floors[i]
    upper = floors[i + 1]
    ni = lower.node_ids[0] if lower.node_ids else 0
    nj = upper.node_ids[0] if upper.node_ids else 0
    return (ni, nj)


# ----------------------------------------------------------------------
# 解析結果読み込み
# ----------------------------------------------------------------------


def _find_result_dir_for_case(
    case: AnalysisCase, snap_work_dir: Optional[str]
) -> Optional[Path]:
    """ケースに対応する SNAP 解析結果フォルダ (Period.xbn がある) を探索します。"""
    candidates: List[Path] = []
    if case.output_dir:
        candidates.append(Path(case.output_dir))
    if case.model_path:
        stem = Path(case.model_path).stem
        if snap_work_dir:
            candidates.append(Path(snap_work_dir) / stem)
        candidates.append(Path(case.model_path).parent / stem)

    for base in candidates:
        if not base.exists():
            continue
        # base 直下 or base/DN/ に Period.xbn がある
        if (base / "Period.xbn").exists():
            return base
        for child in base.iterdir():
            if child.is_dir() and (child / "Period.xbn").exists():
                return child
    return None


def _extract_modes_from_result(
    result_dir: Path,
    n_floors: int,
) -> List[ModeInfo]:
    """
    SNAP 結果フォルダから ModeInfo リストを生成します。

    - Period.xbn → 周期・ω・支配方向
    - MDFloor.xbn (存在すれば) → 各層のモード形状振幅
    """
    from controller.binary.period_xbn_reader import PeriodXbnReader
    from controller.binary.xbn_reader import XbnReader
    from controller.binary.mode_analysis import (
        estimate_mdfloor_structure,
        get_mdfloor_mode_series,
    )

    period_reader = PeriodXbnReader(result_dir / "Period.xbn")
    modes: List[ModeInfo] = []
    if not period_reader.modes:
        return modes

    # MDFloor.xbn から各モード形状を取得（存在する場合）
    mdfloor_records = None
    dof_per_mode = 0
    dof_labels: List[str] = []
    mdfloor_path = result_dir / "MDFloor.xbn"
    if mdfloor_path.exists():
        try:
            mdfloor_reader = XbnReader(mdfloor_path)
            mdfloor_records = mdfloor_reader.records
            dof_per_mode, dof_labels = estimate_mdfloor_structure(
                period_reader.num_modes, mdfloor_reader.values_per_record
            )
        except Exception as exc:
            logger.debug("MDFloor.xbn 読み込み失敗: %s", exc)
            mdfloor_records = None

    for pm in period_reader.modes:
        dom = pm.dominant_direction or "X"
        shape: List[float] = []
        if mdfloor_records is not None and dof_per_mode > 0:
            # DOF ラベルから dominant_direction に対応する index を探す
            dof_idx = 0
            # "Dx" ↔ "X" のマッピング
            want_prefix = f"D{dom.lower()}"
            for i, lbl in enumerate(dof_labels):
                if lbl.lower() == want_prefix:
                    dof_idx = i
                    break
            arr = get_mdfloor_mode_series(
                mdfloor_records, pm.mode_no - 1, dof_idx, dof_per_mode
            )
            shape = [float(v) for v in arr[:n_floors]]
            # 最大絶対値で正規化 (adc-tools と整合)
            if shape:
                peak = max(abs(v) for v in shape)
                if peak > 0:
                    shape = [v / peak for v in shape]

        modes.append(ModeInfo(
            mode_no=pm.mode_no,
            period=float(pm.period),
            omega=float(pm.omega),
            dominant_direction=dom,
            shape=shape,
        ))
    return modes


# ----------------------------------------------------------------------
# 公開 API
# ----------------------------------------------------------------------


def auto_fill_from_project(
    project,  # type: ignore[no-untyped-def]
    case: Optional[AnalysisCase] = None,
    run_if_missing: bool = False,
    log_callback=None,
) -> AutoFillResult:
    """
    プロジェクト情報から iRDT MDOF 入力用データを自動生成します。

    Parameters
    ----------
    project : Project
        snap-controller の Project インスタンス。`s8i_path` と `cases` を使用。
    case : AnalysisCase, optional
        固有値解析結果を取得するケース。None の場合は `project.cases` の最初の
        利用可能ケースを用いる。
    run_if_missing : bool
        True かつ解析結果が見つからない場合、SNAP を実行して固有値解析を行う。
    log_callback : callable, optional
        進捗メッセージの受信用。

    Returns
    -------
    AutoFillResult

    Raises
    ------
    ValueError
        s8i_path が未設定の場合。
    FileNotFoundError
        固有値解析結果が見つからず、かつ run_if_missing=False の場合。
    """
    if not project or not getattr(project, "s8i_path", None):
        raise ValueError("プロジェクトに s8i_path が設定されていません。")

    s8i_path = project.s8i_path
    model = getattr(project, "s8i_model", None)
    if model is None:
        model = parse_s8i(s8i_path)

    floors = extract_floor_info(model)
    warnings: List[str] = []
    if not floors:
        warnings.append("s8i モデルから層情報を取得できませんでした。")

    # ケース選択
    target_case = case
    if target_case is None:
        for c in getattr(project, "cases", []) or []:
            if c.model_path:
                target_case = c
                break

    modes: List[ModeInfo] = []
    result_dir: Optional[Path] = None
    source_name = ""
    if target_case is not None:
        source_name = target_case.name
        result_dir = _find_result_dir_for_case(
            target_case, getattr(project, "snap_work_dir", None)
        )

    if result_dir is None:
        if run_if_missing and target_case is not None:
            if log_callback:
                log_callback(f"固有値解析結果が見つかりません。SNAP で '{target_case.name}' を実行します...")
            result_dir = run_eigenvalue_analysis(
                project, target_case, log_callback=log_callback
            )
        else:
            raise FileNotFoundError(
                "固有値解析結果 (Period.xbn) が見つかりません。"
                "解析を実行するか、run_if_missing=True を指定してください。"
            )

    if result_dir is not None:
        modes = _extract_modes_from_result(result_dir, n_floors=len(floors))
        if not modes:
            warnings.append(f"{result_dir} からモード情報を取得できませんでした。")

    return AutoFillResult(
        floors=floors,
        modes=modes,
        source_case_name=source_name,
        source_result_dir=str(result_dir) if result_dir else "",
        warnings=warnings,
    )


def run_eigenvalue_analysis(
    project,  # type: ignore[no-untyped-def]
    case: AnalysisCase,
    log_callback=None,
) -> Optional[Path]:
    """
    指定ケースで SNAP を実行し、結果フォルダパスを返します。

    SNAP 実行は `controller.snap_exec.snap_exec` を使用し、既存の DYC ケース
    をそのまま使います (短時間で済む既存ケースを 1 つだけ選んでおく想定)。
    """
    from controller.snap_exec import snap_exec

    snap_exe = getattr(project, "snap_exe_path", "") or case.snap_exe_path
    if not snap_exe:
        raise ValueError("SNAP.exe のパスが設定されていません。")
    if not case.model_path:
        raise ValueError(f"ケース '{case.name}' に model_path が設定されていません。")

    def _log(line: str) -> None:
        if log_callback:
            log_callback(line)

    _log(f"SNAP 実行開始: {case.model_path}")
    result = snap_exec(
        snap_exe=snap_exe,
        input_file=case.model_path,
        stdout_callback=_log,
    )
    _log(f"SNAP 実行終了: returncode={result.returncode}")

    if result.returncode != 0:
        _log(f"警告: SNAP が非ゼロ返値で終了しました ({result.returncode})。")

    # 結果フォルダを再検索
    return _find_result_dir_for_case(
        case, getattr(project, "snap_work_dir", None)
    )


# ----------------------------------------------------------------------
# 配置計画ユーティリティ
# ----------------------------------------------------------------------


def build_placement_specs(
    floors: List[FloorInfo],
    mds: List[float],
    cds: List[float],
    kbs: List[float],
    base_def_name: str = "IRDT",
    quantity: int = 1,
    stroke_m: float = 0.3,
    def_only: bool = False,
) -> List:
    """
    層ごとの (md, cd, kb) から DamperInsertSpec のリストを生成します。

    - 層 i (i=0..n-1) に対してダンパーを配置し、各層で一意な定義名 `{base}{i+1}` を使う。
    - node_i, node_j は `pick_interfloor_nodes` で選定。
    - md が 0 または NaN の層はスキップ。
    """
    from app.services.damper_injector import DamperInsertSpec

    specs = []
    n = min(len(floors), len(mds), len(cds), len(kbs))
    for i in range(n):
        md = mds[i]
        cd = cds[i]
        kb = kbs[i]
        if (not md) or math.isnan(md) or math.isnan(cd) or math.isnan(kb) or md <= 0:
            continue
        # iRDT は層 i-1 と層 i の間に配置するのが自然 (基部=0 と 1階の間に配置)
        # ここでは簡易的に「層 i と層 i+1 の間」の iRDT を層 i の md で設計。
        # 配置対象が最上層の場合はスキップ。
        if i + 1 >= len(floors):
            continue
        ni, nj = pick_interfloor_nodes(floors, i)
        spec = DamperInsertSpec(
            damper_type="iRDT",
            def_name=f"{base_def_name}{i + 1}",
            floor_name=floors[i + 1].name,   # 配置先の階 (上側)
            node_i=ni,
            node_j=nj,
            quantity=quantity,
            mass_kN_s2_m=float(md),
            spring_kN_m=float(kb),
            damping_kN_s_m=float(cd),
            stroke_m=stroke_m,
            def_only=def_only,
        )
        specs.append(spec)
    return specs
