"""
app/services/damper_injector.py
SNAPモデル（.s8i）へのiRDT/iODダンパー自動挿入サービス。

指定した層にiRDT（慣性質量減衰型制振装置）またはiOD（大質量型オイルダンパー）の
ダンパー定義（DVMS要素）を自動的に挿入し、新しいSNAPケースとして保存します。
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

    DVMS キーワードを用いてダンパー定義を追加し、
    RD 要素（免制振装置配置）を指定ノード間に挿入します。

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

    # DVMS フィールドインデックス (values[0]=名前, values[N]=Nフィールド目)
    # add_damper_def_new の overrides: "N" → values[N]
    _FIELD_TYPE = "1"      # タイプ (0=標準)
    _FIELD_DIR = "2"       # 方向 (0=デフォルト)
    _FIELD_MASS = "3"      # 慣性質量 (kN·s²/m)
    _FIELD_SPRING = "4"    # 支持バネ剛性 (kN/m)
    _FIELD_DAMPING = "5"   # 減衰係数 (kN·s/m)
    _FIELD_STROKE = "6"    # ストローク (m)
    _DVMS_NUM_FIELDS = 20  # DVMS 定義のフィールド数（名前除く）

    # RD 種別コード: 1 = 粘性/オイル系（iRDT/iOD に適用）
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

        # DVMS 定義を追加（上書き or 新規）
        overrides = self._build_dvms_overrides(spec)
        new_def = model.add_damper_def_new(
            keyword="DVMS",
            new_name=spec.def_name,
            num_fields=self._DVMS_NUM_FIELDS,
            overrides=overrides,
        )
        if new_def is None:
            warnings.append(f"定義 '{spec.def_name}' の追加に失敗しました。")
            return False

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
            "追加: DVMS '%s' (m=%.1f, k=%.1f, c=%.1f) → RD %s(%d→%d)×%d",
            spec.def_name,
            spec.mass_kN_s2_m,
            spec.spring_kN_m,
            spec.damping_kN_s_m,
            rd_name,
            spec.node_i,
            spec.node_j,
            spec.quantity,
        )
        return True

    def _build_dvms_overrides(self, spec: DamperInsertSpec) -> Dict[str, str]:
        """DVMS 定義の add_damper_def_new 用 overrides を構築。

        add_damper_def_new の overrides キーは 1-indexed の values インデックス。
        values[0] = 定義名（自動設定）, values[1] から SNAP フィールド。
        """
        return {
            self._FIELD_TYPE:    "0",
            self._FIELD_DIR:     "0",
            self._FIELD_MASS:    str(spec.mass_kN_s2_m),
            self._FIELD_SPRING:  str(spec.spring_kN_m),
            self._FIELD_DAMPING: str(spec.damping_kN_s_m),
            self._FIELD_STROKE:  str(spec.stroke_m),
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
