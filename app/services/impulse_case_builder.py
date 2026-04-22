"""
app/services/impulse_case_builder.py
インパルス応答解析用の AnalysisCase 自動生成サービス。

ユーザが選択した既存の AnalysisCase を元に:
1. SNAP wave フォルダにインパルス波 (.wv) を生成
2. 元の .s8i をコピーし、指定 DYC ケースをインパルス入力に切替（他ケース無効化）
3. 新しい AnalysisCase として返却する（呼び出し側で project.add_case() する）

``write_impulse_wave`` と ``S8iModel.apply_impulse_mode`` のラッパー。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.models.analysis_case import AnalysisCase
from app.models.s8i_parser import DycCase, parse_s8i
from app.services.impulse_wave_writer import (
    DEFAULT_DT,
    DEFAULT_IMPULSE_INDEX,
    DEFAULT_NUM_POINTS,
    ImpulseWaveSpec,
    make_impulse_filename,
    write_impulse_wave,
)

logger = logging.getLogger(__name__)


@dataclass
class ImpulseCaseSpec:
    """インパルス応答解析ケース生成の仕様。"""

    base_case: AnalysisCase
    target_case_no: int                    # 1-indexed DYC ケース番号
    snap_wave_dir: str                     # SNAP wave フォルダ
    amax: float = 1000.0                   # 加速度振幅 (gal)
    dt: float = DEFAULT_DT                 # 時間刻み (s)
    num_points: int = DEFAULT_NUM_POINTS   # データ点数
    impulse_index: int = DEFAULT_IMPULSE_INDEX  # インパルス発生位置
    wave_scale: float = 1.0                # DYC 倍率
    case_name: Optional[str] = None        # 省略時自動生成
    output_s8i_path: Optional[str] = None  # 省略時自動生成 (base_case と同フォルダ)

    def validate(self) -> None:
        if self.base_case is None:
            raise ValueError("base_case が指定されていません")
        if not self.base_case.model_path:
            raise ValueError("base_case.model_path が未設定です")
        if not Path(self.base_case.model_path).exists():
            raise FileNotFoundError(
                f".s8i ファイルが見つかりません: {self.base_case.model_path}"
            )
        if self.target_case_no <= 0:
            raise ValueError(f"target_case_no は 1 以上: {self.target_case_no}")
        if not self.snap_wave_dir:
            raise ValueError("snap_wave_dir が未設定です")
        if self.amax == 0.0:
            raise ValueError("amax が 0 です（正負どちらかの値を指定してください）")
        if self.num_points <= 0:
            raise ValueError(f"num_points は 1 以上: {self.num_points}")
        if not (0 <= self.impulse_index < self.num_points):
            raise ValueError(
                f"impulse_index が範囲外: {self.impulse_index} "
                f"(0 <= i < {self.num_points})"
            )
        if self.dt <= 0:
            raise ValueError(f"dt は 0 より大: {self.dt}")


def list_dyc_cases(model_path: str) -> list[DycCase]:
    """.s8i から DYC ケース一覧を返す（UI の選択肢表示用）。"""
    model = parse_s8i(model_path)
    return list(model.dyc_cases)


def build_impulse_case(spec: ImpulseCaseSpec) -> AnalysisCase:
    """インパルス応答解析用の ``AnalysisCase`` を生成する。

    Parameters
    ----------
    spec : ImpulseCaseSpec
        生成パラメータ。

    Returns
    -------
    AnalysisCase
        新しく作成された解析ケース（``project.add_case()`` で追加する）。

    Side effects
    ------------
    - ``snap_wave_dir`` にインパルス波 (.wv) を生成
    - ベース .s8i の隣に新しい .s8i ファイルを作成
      （``output_s8i_path`` が指定されていればそこに）
    """
    spec.validate()

    base_s8i = Path(spec.base_case.model_path)

    # 1. インパルス波の生成
    impulse_name = make_impulse_filename(
        case_id=f"D{spec.target_case_no}_{base_s8i.stem}",
        amax=spec.amax,
    )
    wave_dir = Path(spec.snap_wave_dir)
    wave_dir.mkdir(parents=True, exist_ok=True)
    wave_path = wave_dir / f"{impulse_name}.wv"
    write_impulse_wave(
        wave_path,
        ImpulseWaveSpec(
            amax=spec.amax,
            dt=spec.dt,
            num_points=spec.num_points,
            impulse_index=spec.impulse_index,
            filename=impulse_name,
        ),
    )
    logger.info(
        "インパルス波書き出し: %s (amax=%s gal, dt=%s, N=%d)",
        wave_path, spec.amax, spec.dt, spec.num_points,
    )

    # 2. .s8i のコピーとインパルスモード適用
    if spec.output_s8i_path:
        out_s8i = Path(spec.output_s8i_path)
    else:
        out_s8i = _derive_output_s8i_path(base_s8i, impulse_name)
    out_s8i.parent.mkdir(parents=True, exist_ok=True)

    model = parse_s8i(str(base_s8i))
    applied = model.apply_impulse_mode(
        target_case_no=spec.target_case_no,
        impulse_wave_name=impulse_name,
        wave_scale=spec.wave_scale,
    )
    if applied is None:
        raise ValueError(
            f"DYC ケース D{spec.target_case_no} が .s8i に存在しません"
        )
    model.write(str(out_s8i))
    logger.info("インパルス入力 .s8i を書き出し: %s", out_s8i)

    # 3. 新 AnalysisCase 生成
    default_name = (
        f"{spec.base_case.name} [インパルス D{spec.target_case_no}]"
        if not spec.case_name else spec.case_name
    )
    new_case = AnalysisCase(
        name=default_name,
        model_path=str(out_s8i),
        snap_exe_path=spec.base_case.snap_exe_path,
        notes=(
            f"インパルス応答解析ケース\n"
            f"ベース: {spec.base_case.name} (D{spec.target_case_no}: {applied.name})\n"
            f"インパルス波: {impulse_name}\n"
            f"amax={spec.amax} gal, dt={spec.dt}, N={spec.num_points}, "
            f"pos={spec.impulse_index}"
        ),
    )
    return new_case


def _derive_output_s8i_path(base_s8i: Path, impulse_name: str) -> Path:
    """ベース .s8i と同フォルダに新ファイル名を生成。

    衝突時は (_2, _3, ...) を付与。
    """
    candidate = base_s8i.parent / f"{base_s8i.stem}_{impulse_name}.s8i"
    if not candidate.exists():
        return candidate
    i = 2
    while True:
        c = base_s8i.parent / f"{base_s8i.stem}_{impulse_name}_{i}.s8i"
        if not c.exists():
            return c
        i += 1
