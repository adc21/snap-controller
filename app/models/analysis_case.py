"""
app/models/analysis_case.py
解析ケースのデータモデル。

1つの解析ケース = 1回の SNAP 解析実行に対応します。
各ケースはモデルファイルパス、パラメータ設定、実行状態、結果を保持します。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class AnalysisCaseStatus(str, Enum):
    """解析ケースの実行状態。"""
    PENDING = "pending"       # 未実行
    RUNNING = "running"       # 実行中
    COMPLETED = "completed"   # 正常完了
    ERROR = "error"           # エラー終了


@dataclass
class AnalysisCase:
    """
    1つの解析ケースを表すデータクラス。

    Attributes
    ----------
    id : str
        ケースを一意に識別する UUID 文字列。
    name : str
        ケース名（ユーザーが設定する表示名）。
    model_path : str
        SNAP 入力ファイル (.s8i) のパス。
    snap_exe_path : str
        SNAP.exe のパス。
    output_dir : str
        解析結果の出力先ディレクトリ。省略時はモデルと同じディレクトリ。
    parameters : dict
        .s8i ファイルに上書きするパラメータ辞書。
        例: {"DAMPING": 0.05, "DT": 0.01}
    damper_params : dict
        制振・免震装置パラメータ辞書。
        例: {"type": "油圧ダンパー", "Cd": 500.0, "alpha": 0.4}
    status : AnalysisCaseStatus
        実行状態。
    return_code : int or None
        SNAP の終了コード（実行前は None）。
    notes : str
        ケースのメモ。
    result_summary : dict
        解析後に格納される主要結果のサマリー。
        例: {"max_drift": 0.012, "max_acc": 4.5}
    dyc_results : list of dict
        s8i 内の DYC サブケースごとの解析結果リスト。
        各要素は以下のキーを持つ辞書::

            {
                "case_no":       int,   # DYC 連番 (1始まり)
                "case_name":     str,   # DYC ケース名
                "run_flag":      int,   # 0=解析しない, 1=解析する
                "has_result":    bool,
                "result_data":   dict,  # Result.get_all() と同形式
                "result_summary": dict, # max values
            }
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = "新規ケース"
    model_path: str = ""
    snap_exe_path: str = ""
    output_dir: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    damper_params: Dict[str, Any] = field(default_factory=dict)
    extra_defs: List[Dict[str, Any]] = field(default_factory=list)
    # extra_defs の各要素:
    #   {
    #     "keyword":   "DVOD",          # SNAP キーワード
    #     "name":      "C1_heavy",       # ユーザーが付けた一意な定義名
    #     "base_name": "C1",             # コピー元の定義名
    #     "overrides": {"8": "800000"}   # 変更フィールド (1-indexed文字列→値)
    #   }
    status: AnalysisCaseStatus = AnalysisCaseStatus.PENDING
    return_code: Optional[int] = None
    notes: str = ""
    result_summary: Dict[str, Any] = field(default_factory=dict)
    dyc_results: List[Dict[str, Any]] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        """JSON 保存用の辞書に変換します。"""
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AnalysisCase":
        """辞書から AnalysisCase を復元します。"""
        data = dict(data)
        data["status"] = AnalysisCaseStatus(data.get("status", "pending"))
        # 旧バージョンとの互換性
        if "dyc_results" not in data:
            data["dyc_results"] = []
        if "extra_defs" not in data:
            data["extra_defs"] = []
        return cls(**data)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def is_runnable(self, snap_exe_path: str = "") -> bool:
        """解析を実行可能かどうかを返します。

        Parameters
        ----------
        snap_exe_path : str
            プロジェクトレベルの SNAP.exe パス。
        """
        exe = snap_exe_path or self.snap_exe_path
        return bool(self.model_path) and bool(exe)

    def reset(self) -> None:
        """実行状態と結果をリセットします。"""
        self.status = AnalysisCaseStatus.PENDING
        self.return_code = None
        self.result_summary = {}
        self.dyc_results = []

    def get_status_label(self) -> str:
        """表示用のステータス文字列を返します。"""
        labels = {
            AnalysisCaseStatus.PENDING: "未実行",
            AnalysisCaseStatus.RUNNING: "実行中",
            AnalysisCaseStatus.COMPLETED: "完了",
            AnalysisCaseStatus.ERROR: "エラー",
        }
        return labels.get(self.status, self.status.value)

    def clone(self) -> "AnalysisCase":
        """このケースのコピーを新しい ID で返します。"""
        d = self.to_dict()
        d["id"] = str(uuid.uuid4())
        d["name"] = f"{self.name} (コピー)"
        d["status"] = AnalysisCaseStatus.PENDING
        d["return_code"] = None
        d["result_summary"] = {}
        d["dyc_results"] = []
        return AnalysisCase.from_dict(d)
