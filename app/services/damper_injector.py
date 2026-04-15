"""
app/services/damper_injector.py
SNAPモデル（.s8i）へのiRDT/iODダンパー自動挿入サービス。

指定した層にiRDT（慣性質量減衰型制振装置）またはiOD（大質量型オイルダンパー）の
ダンパー定義（DVOD: 粘性/オイルダンパー）を自動的に挿入し、
対応するRD要素（制振ブレース）と共に新しいSNAPケースとして保存します。

iRDT は SNAP 上では DVOD として登録し、
  - 減衰モデル = 3 (ダッシュポットと質量)
  - 質量        = 慣性質量 md (t = kN·s²/m)
  - ダッシュポット特性 種別 = 0 (線形弾性型 EL1)
  - C0          = 減衰係数 cd (kN·s/m)
  - 取付け剛性   = 支持部材剛性 kb (kN/m)
の形で表現します（DVOD の仕様は hhD8A3.pdf を参照）。
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.models.s8i_parser import (
    S8iModel,
    parse_s8i,
    DamperDefinition,
    DamperElement,
)
from app.models.analysis_case import AnalysisCase, AnalysisCaseStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DamperInsertSpec:
    """挿入するダンパー1件の仕様。

    Attributes
    ----------
    damper_type : str
        装置種別。"iRDT" または "iOD"。
    def_name : str
        SNAP 定義名（例: "IRDT1"）。既存の同名定義は上書きされます。
    floor_name : str
        挿入する層名（例: "F5"）。ログ・UI 表示用途。
    node_i : int
        RD 要素の始端節点番号。
    node_j : int
        RD 要素の終端節点番号。
    quantity : int
        配置基数（本数）。
    mass_kN_s2_m : float
        慣性質量 m_d (kN·s²/m)。
    spring_kN_m : float
        支持バネ剛性 k_b (kN/m)。iOD の場合は 0。
    damping_kN_s_m : float
        減衰係数 c_d (kN·s/m)。
    stroke_m : float
        最大ストローク (m)。
    """

    damper_type: str = "iRDT"
    def_name: str = "IRDT1"
    floor_name: str = ""
    node_i: int = 0
    node_j: int = 0
    quantity: int = 1
    mass_kN_s2_m: float = 100.0
    spring_kN_m: float = 5000.0
    damping_kN_s_m: float = 200.0
    stroke_m: float = 0.3
    # True の場合、ダンパー定義 (DVOD) だけを追加し RD 要素 (配置) は追加しない。
    def_only: bool = False


@dataclass
class InjectionResult:
    """挿入操作の結果。

    Attributes
    ----------
    success : bool
        True の場合、挿入と書き出しが成功。
    output_s8i_path : str
        書き出した .s8i ファイルのパス。
    new_case : AnalysisCase or None
        生成した解析ケース（base_case が None の場合は None）。
    added_def_names : list of str
        追加されたダンパー定義名の一覧。
    added_element_count : int
        追加された RD 要素数。
    message : str
        結果メッセージ（成功・失敗の概要）。
    warnings : list of str
        警告メッセージ一覧（成功でも発生し得る）。
    """

    success: bool = False
    output_s8i_path: str = ""
    new_case: Optional[AnalysisCase] = None
    added_def_names: List[str] = field(default_factory=list)
    added_element_count: int = 0
    message: str = ""
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# DamperInjector サービス
# ---------------------------------------------------------------------------

class DamperInjector:
    """
    .s8i ファイルへの iRDT/iOD ダンパー自動挿入サービス。

    DVOD キーワード (粘性/オイルダンパー) を用いてダンパー定義を追加し、
    RD 要素 (制振ブレース) を指定ノード間に挿入します。iRDT の場合は
    DVOD の減衰モデルを 3 (ダッシュポットと質量) に設定し、
    慣性質量・C0・取付け剛性を書き込みます。

    使用例
    ------
    >>> injector = DamperInjector()
    >>> spec = DamperInsertSpec(
    ...     damper_type="iRDT",
    ...     def_name="IRDT1",
    ...     floor_name="F5",
    ...     node_i=101,
    ...     node_j=201,
    ...     mass_kN_s2_m=150.0,
    ...     spring_kN_m=8000.0,
    ...     damping_kN_s_m=300.0,
    ... )
    >>> result = injector.inject(
    ...     base_s8i_path="/path/to/model.s8i",
    ...     specs=[spec],
    ...     output_s8i_path="/path/to/model_irdt.s8i",
    ... )
    >>> print(result.success, result.message)
    """

    # DVOD フィールドインデックス (values[0]=名前, values[N]=Nフィールド目)
    # add_damper_def_new の overrides: "N" → values[N]
    # (参考: hhD8A3.pdf "DVOD 粘性/オイルダンパー" の項目順序)
    #   order  2: k-DB 種別      → values[1]
    #   order  3: 会社           → values[2]
    #   order  4: 製品           → values[3]
    #   order  5: 型番           → values[4]
    #   order  6: 減衰モデル     → values[5]   (3 = ダッシュポットと質量)
    #   order  7: 質量 (t)       → values[6]
    #   order  8: ダッシュポット特性種別 → values[7] (0 = 線形弾性型 EL1)
    #   order  9: C0             → values[8]   (減衰係数 kN·s/m)
    #   order 10: Fc             → values[9]
    #   order 11: Fy             → values[10]
    #   order 12: Ve             → values[11]
    #   order 13: α             → values[12]
    #   order 14: β             → values[13]
    #   order 15: 剛性 (装置剛性)→ values[14]
    #   order 16: 取付け剛性     → values[15]  (支持部材剛性 kb)
    #   order 17: 装置高さ       → values[16]
    #   order 18: 重量種別       → values[17]
    #   order 19: 重量           → values[18]
    #   order 20: 下限温度       → values[19]
    #   order 21: 下限 τ        → values[20]  (1.0)
    #   order 22: 上限温度       → values[21]
    #   order 23: 上限 τ        → values[22]  (1.0)
    _FIELD_KDB_TYPE = "1"
    _FIELD_KDB_COMPANY = "2"
    _FIELD_KDB_PRODUCT = "3"
    _FIELD_KDB_MODEL = "4"
    _FIELD_DAMP_MODEL = "5"         # 減衰モデル (3=ダッシュポットと質量)
    _FIELD_MASS = "6"               # 質量 (t = kN·s²/m)
    _FIELD_DASHPOT_TYPE = "7"       # ダッシュポット特性-種別 (0=EL1)
    _FIELD_C0 = "8"                 # C0 (kN·s/m)
    _FIELD_DEVICE_STIFF = "14"      # 装置剛性
    _FIELD_MOUNT_STIFF = "15"       # 取付け剛性 (= 支持部材剛性 kb)
    _FIELD_TAU_LOW = "20"           # 変動係数 下限 τ
    _FIELD_TAU_HIGH = "22"          # 変動係数 上限 τ
    _DVOD_NUM_FIELDS = 22           # DVOD 定義のフィールド数（名前除く）
    _DVOD_DAMP_MODEL_DASHPOT_MASS = "3"
    _DVOD_DASHPOT_LINEAR_EL1 = "0"

    # RD 種別コード: 1 = 粘性/オイルダンパー（DVOD に対応）
    _RD_DAMPER_TYPE_CODE = "1"

    def inject(
        self,
        base_s8i_path: str,
        specs: List[DamperInsertSpec],
        output_s8i_path: str,
        base_case: Optional[AnalysisCase] = None,
        new_case_name: Optional[str] = None,
    ) -> InjectionResult:
        """
        指定仕様のiRDT/iODダンパーを .s8i に挿入して保存する。

        Parameters
        ----------
        base_s8i_path : str
            元の .s8i ファイルパス。
        specs : list of DamperInsertSpec
            挿入するダンパーの仕様リスト。空の場合はエラー。
        output_s8i_path : str
            出力先 .s8i ファイルパス（元ファイルと別パスを推奨）。
        base_case : AnalysisCase, optional
            元の解析ケース。指定時は新ケースを生成して返す。
        new_case_name : str, optional
            新ケースの名称。省略時は自動生成。

        Returns
        -------
        InjectionResult
        """
        if not specs:
            return InjectionResult(
                success=False,
                message="挿入するダンパー仕様が指定されていません。",
            )

        # .s8i 読み込み
        try:
            model = parse_s8i(base_s8i_path)
        except Exception as exc:
            logger.exception("s8i 読み込み失敗: %s", base_s8i_path)
            return InjectionResult(
                success=False,
                message=f".s8iファイルの読み込みに失敗しました: {exc}",
            )

        warnings: List[str] = []
        added_def_names: List[str] = []
        added_elements = 0

        for spec in specs:
            ok = self._inject_single(model, spec, warnings)
            if ok:
                if spec.def_name not in added_def_names:
                    added_def_names.append(spec.def_name)
                added_elements += 1

        # 書き出し
        try:
            Path(output_s8i_path).parent.mkdir(parents=True, exist_ok=True)
            model.write(output_s8i_path)
        except Exception as exc:
            logger.exception("s8i 書き出し失敗: %s", output_s8i_path)
            return InjectionResult(
                success=False,
                message=f".s8iファイルの書き出しに失敗しました: {exc}",
                warnings=warnings,
            )

        # 新規 AnalysisCase 生成
        new_case = self._create_new_case(
            base_case, output_s8i_path, specs, new_case_name
        )

        def_str = ", ".join(added_def_names) if added_def_names else "なし"
        msg = (
            f"挿入完了: 定義 [{def_str}], RD要素 {added_elements}件を追加しました。"
        )
        logger.info(msg)

        return InjectionResult(
            success=True,
            output_s8i_path=output_s8i_path,
            new_case=new_case,
            added_def_names=added_def_names,
            added_element_count=added_elements,
            message=msg,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # 内部実装
    # ------------------------------------------------------------------

    def _inject_single(
        self,
        model: S8iModel,
        spec: DamperInsertSpec,
        warnings: List[str],
    ) -> bool:
        """1件の DamperInsertSpec をモデルに追加する。成功で True。"""
        # 重複チェック
        existing_def = model.get_damper_def(spec.def_name)
        if existing_def is not None:
            warnings.append(
                f"定義名 '{spec.def_name}' が既に存在します。上書きします。"
            )

        # DVOD 定義を追加（上書き or 新規）
        overrides = self._build_dvod_overrides(spec)
        new_def = model.add_damper_def_new(
            keyword="DVOD",
            new_name=spec.def_name,
            num_fields=self._DVOD_NUM_FIELDS,
            overrides=overrides,
        )
        if new_def is None:
            warnings.append(f"定義 '{spec.def_name}' の追加に失敗しました。")
            return False

        # 定義のみモード: RD 配置はスキップ
        if spec.def_only:
            logger.debug(
                "定義のみ追加: DVOD '%s' (md=%.1f, cd=%.1f, kb=%.1f)",
                spec.def_name, spec.mass_kN_s2_m, spec.damping_kN_s_m, spec.spring_kN_m,
            )
            return True

        # ノード検証
        if spec.node_i not in model.nodes and model.nodes:
            warnings.append(
                f"節点 {spec.node_i} がモデルに存在しません（RD は追加）。"
            )
        if spec.node_j not in model.nodes and model.nodes:
            warnings.append(
                f"節点 {spec.node_j} がモデルに存在しません（RD は追加）。"
            )

        # RD 要素を追加
        rd_name = f"RD{spec.def_name}"
        # RD フィールド: index 0=名称, 1=節点I, 2=節点J, 3=種別,
        #               4=装置名, 5-9=剛性/重量, 10=倍数
        rd_values = [
            rd_name,                     # 0: 名称
            str(spec.node_i),            # 1: 節点I
            str(spec.node_j),            # 2: 節点J
            self._RD_DAMPER_TYPE_CODE,   # 3: 種別 (1=粘性/オイル)
            spec.def_name,               # 4: 装置名
            "0",                         # 5: 装置剛性
            "0",                         # 6: 取付け剛性
            "0",                         # 7: アスペクト比
            "0",                         # 8: 付加重量1
            "0",                         # 9: 付加重量2
            str(spec.quantity),          # 10: 倍数/基数
        ]
        new_elem = DamperElement(
            name=rd_name,
            node_i=spec.node_i,
            node_j=spec.node_j,
            quantity=spec.quantity,
            damper_def_name=spec.def_name,
            damper_type=int(self._RD_DAMPER_TYPE_CODE),
            values=rd_values,
            raw="",
            line_no=0,  # 0 = 新規追加（既存行なし）
        )
        model.damper_elements.append(new_elem)
        logger.debug(
            "追加: DVOD '%s' (md=%.1f, cd=%.1f, kb=%.1f) → RD %s(%d→%d)×%d",
            spec.def_name,
            spec.mass_kN_s2_m,
            spec.damping_kN_s_m,
            spec.spring_kN_m,
            rd_name,
            spec.node_i,
            spec.node_j,
            spec.quantity,
        )
        return True

    def _build_dvod_overrides(self, spec: DamperInsertSpec) -> Dict[str, str]:
        """DVOD 定義の add_damper_def_new 用 overrides を構築。

        add_damper_def_new の overrides キーは 1-indexed の values インデックス。
        values[0] = 定義名（自動設定）, values[1] 以降が SNAP フィールド。
        iRDT は DVOD の減衰モデル 3 (ダッシュポットと質量) で表現する。

        Parameters
        ----------
        spec : DamperInsertSpec
            - mass_kN_s2_m : 慣性質量 md (t = kN·s²/m)
            - damping_kN_s_m : 減衰係数 cd (C0, kN·s/m)
            - spring_kN_m  : 支持部材剛性 kb → DVOD の 取付け剛性
        """
        return {
            # k-DB は「使用しない」
            self._FIELD_KDB_TYPE:       "0",
            self._FIELD_KDB_COMPANY:    "0",
            self._FIELD_KDB_PRODUCT:    "0",
            self._FIELD_KDB_MODEL:      "",
            # 装置特性: 減衰モデル = 3 (ダッシュポットと質量)
            self._FIELD_DAMP_MODEL:     self._DVOD_DAMP_MODEL_DASHPOT_MASS,
            # 慣性質量 md
            self._FIELD_MASS:           f"{spec.mass_kN_s2_m}",
            # ダッシュポット特性: 線形弾性型 (EL1)
            self._FIELD_DASHPOT_TYPE:   self._DVOD_DASHPOT_LINEAR_EL1,
            # C0 (減衰係数)
            self._FIELD_C0:             f"{spec.damping_kN_s_m}",
            # 装置剛性は 0 (iRDT には装置自体の剛性は無い)
            self._FIELD_DEVICE_STIFF:   "0",
            # 取付け剛性 = 支持部材剛性 kb
            self._FIELD_MOUNT_STIFF:    f"{spec.spring_kN_m}",
            # 温度変動係数 τ は 1.0 固定
            self._FIELD_TAU_LOW:        "1.0",
            self._FIELD_TAU_HIGH:       "1.0",
        }

    def _create_new_case(
        self,
        base_case: Optional[AnalysisCase],
        output_s8i_path: str,
        specs: List[DamperInsertSpec],
        name: Optional[str],
    ) -> Optional[AnalysisCase]:
        """挿入後の新しい AnalysisCase を生成する。"""
        if base_case is None:
            return None

        type_tags = sorted({s.damper_type for s in specs})
        auto_name = name or f"{base_case.name}_{'_'.join(type_tags)}"

        damper_params: Dict[str, Any] = {}
        for s in specs:
            damper_params[s.def_name] = {
                "type": s.damper_type,
                "floor": s.floor_name,
                "node_i": s.node_i,
                "node_j": s.node_j,
                "quantity": s.quantity,
                "mass_kN_s2_m": s.mass_kN_s2_m,
                "spring_kN_m": s.spring_kN_m,
                "damping_kN_s_m": s.damping_kN_s_m,
                "stroke_m": s.stroke_m,
            }

        new_case = AnalysisCase(
            name=auto_name,
            model_path=output_s8i_path,
            snap_exe_path=base_case.snap_exe_path,
            output_dir=base_case.output_dir,
            parameters=dict(base_case.parameters),
            damper_params=damper_params,
            status=AnalysisCaseStatus.PENDING,
        )
        return new_case


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------

def create_injector() -> DamperInjector:
    """DamperInjector のファクトリ関数。"""
    return DamperInjector()
