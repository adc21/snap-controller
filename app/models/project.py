"""
app/models/project.py
プロジェクト管理データモデル。

.snapproj ファイル（JSON 形式）として保存・読込されます。
1つのプロジェクトは1つの SNAP 入力ファイル (.s8i) に対応し、
複数の AnalysisCase（ダンパー設定バリエーション）を束ねるコンテナです。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

from .analysis_case import AnalysisCase, AnalysisCaseStatus
from .performance_criteria import PerformanceCriteria
from .s8i_parser import S8iModel, parse_s8i


_PROJECT_VERSION = "2.0"


class Project:
    """
    snap-controller プロジェクト。

    Attributes
    ----------
    name : str
        プロジェクト名。
    snap_exe_path : str
        SNAP.exe のパス。
    s8i_path : str
        SNAP 入力ファイル (.s8i) のパス。1プロジェクトにつき1つ。
    s8i_model : S8iModel or None
        .s8i ファイルのパース結果。load_s8i() で読み込みます。
    cases : list of AnalysisCase
        解析ケースのリスト（ダンパー設定バリエーション）。
    file_path : Path or None
        現在保存されているプロジェクトファイルパス。
    modified : bool
        未保存変更があるかどうか。
    """

    def __init__(self, name: str = "新規プロジェクト") -> None:
        self.name: str = name
        self.snap_exe_path: str = ""
        self.snap_work_dir: str = ""
        self.s8i_path: str = ""
        self.s8i_model: Optional[S8iModel] = None
        self.cases: List[AnalysisCase] = []
        self.criteria: PerformanceCriteria = PerformanceCriteria()
        self.case_groups: Dict[str, List[str]] = {}  # group_name -> [case_id, ...]
        self.file_path: Optional[Path] = None
        self.modified: bool = False
        self.created_at: str = datetime.now().isoformat()
        self.updated_at: str = self.created_at
        # UX改善④新: 解析戦略メモ（次ラウンドに向けた気づきや方針を記録）
        self.strategy_notes: str = ""
        # DYD 履歴結果の出力指定オーバーライド
        self.dyd_history_overrides: Optional[Dict[int, int]] = None

    # ------------------------------------------------------------------
    # S8i file
    # ------------------------------------------------------------------

    def load_s8i(self, path: str) -> S8iModel:
        """
        .s8i ファイルを読み込んでパースします。

        Parameters
        ----------
        path : str
            .s8i ファイルのパス。

        Returns
        -------
        S8iModel
            パースされたモデルオブジェクト。
        """
        self.s8i_path = str(path)
        self.s8i_model = parse_s8i(path)
        if not self.name or self.name == "新規プロジェクト":
            self.name = self.s8i_model.title or Path(path).stem
        self._touch()
        return self.s8i_model

    @property
    def has_s8i(self) -> bool:
        """入力ファイルが読み込まれているか。"""
        return self.s8i_model is not None

    # ------------------------------------------------------------------
    # Case management
    # ------------------------------------------------------------------

    def add_case(self, case: Optional[AnalysisCase] = None) -> AnalysisCase:
        """新しいケースを追加して返します。

        解析時に SNAP 入力ファイル名へケース名を埋め込んで
        ケース固有の結果フォルダに書き出すため、**ケース名は
        プロジェクト内で一意である必要があります**。
        既存ケースと名前が重複する場合、自動で採番して一意化します。
        """
        if case is None:
            case = AnalysisCase()
            case.name = f"Case {len(self.cases) + 1}"
        # モデルファイルを自動設定
        if self.s8i_path and not case.model_path:
            case.model_path = self.s8i_path
        # 名前の一意化（他ケースと衝突しないように採番）
        case.name = self.ensure_unique_case_name(case.name, exclude_id=case.id)
        self.cases.append(case)
        self._touch()
        return case

    def ensure_unique_case_name(self, desired: str,
                                exclude_id: Optional[str] = None) -> str:
        """与えられた名前を、既存ケース名と衝突しない一意な名前に調整します。

        Parameters
        ----------
        desired : str
            希望する名前。
        exclude_id : Optional[str]
            除外するケース ID（改名対象ケース自身を衝突判定から外す用）。

        Returns
        -------
        str
            衝突しない名前。必要に応じて " (2)", " (3)" ... を付加します。
        """
        desired = (desired or "").strip() or "無名ケース"
        existing = {c.name for c in self.cases if c.id != exclude_id}
        if desired not in existing:
            return desired
        import re
        # 末尾 " (n)" があれば取り除いてベース名を作る
        m = re.match(r"^(.*?)(?:\s*\((\d+)\))?\s*$", desired)
        base = m.group(1).strip() if m else desired
        n = 2
        while True:
            candidate = f"{base} ({n})"
            if candidate not in existing:
                return candidate
            n += 1

    def remove_case(self, case_id: str) -> bool:
        """指定 ID のケースを削除します。存在した場合 True を返します。"""
        before = len(self.cases)
        self.cases = [c for c in self.cases if c.id != case_id]
        if len(self.cases) < before:
            self._touch()
            return True
        return False

    def get_case(self, case_id: str) -> Optional[AnalysisCase]:
        """ID でケースを取得します。見つからない場合は None を返します。"""
        return next((c for c in self.cases if c.id == case_id), None)

    def duplicate_case(self, case_id: str) -> Optional[AnalysisCase]:
        """
        ケースをコピーして追加します。

        UX改善①: 複製後のケース名を自動採番して重複を防ぎます。
        例: "Case A" → "Case A (コピー)"
            "Case A (コピー)" を再複製 → "Case A (コピー 2)"
            "Case A (コピー 2)" を再複製 → "Case A (コピー 3)"
        """
        original = self.get_case(case_id)
        if original is None:
            return None
        clone = original.clone()
        # UX改善①: 重複しない一意な名前を生成
        clone.name = self._unique_copy_name(original.name)
        self.cases.append(clone)
        self._touch()
        return clone

    def _unique_copy_name(self, original_name: str) -> str:
        """
        UX改善①: 既存ケース名と重複しない複製名を生成します。

        "XXX (コピー)" が既存ならば "XXX (コピー 2)"、
        それも存在すれば "XXX (コピー 3)" と順に番号を増やします。
        "(コピー)" サフィックスを取り除いたベース名を使って採番するため、
        何度複製しても "(コピー) (コピー)" のような二重サフィックスになりません。
        """
        import re
        existing_names = {c.name for c in self.cases}

        # ベース名を取得: 末尾の "(コピー N)" パターンを除去
        base = re.sub(r'\s*\(コピー(?:\s+\d+)?\)\s*$', '', original_name).strip()

        # "XXX (コピー)" が空きなら採用
        candidate = f"{base} (コピー)"
        if candidate not in existing_names:
            return candidate

        # 番号を増やしながら空きを探す
        n = 2
        while True:
            candidate = f"{base} (コピー {n})"
            if candidate not in existing_names:
                return candidate
            n += 1

    def get_completed_cases(self) -> List[AnalysisCase]:
        """完了済みケースのリストを返します。"""
        return [c for c in self.cases if c.status == AnalysisCaseStatus.COMPLETED]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Optional[str] = None) -> Path:
        """プロジェクトを .snapproj ファイルに保存します。"""
        if path:
            self.file_path = Path(path)
        if self.file_path is None:
            raise ValueError("保存先パスを指定してください。")

        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = datetime.now().isoformat()

        data = {
            "version": _PROJECT_VERSION,
            "name": self.name,
            "snap_exe_path": self.snap_exe_path,
            "snap_work_dir": self.snap_work_dir,
            "s8i_path": self.s8i_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "cases": [c.to_dict() for c in self.cases],
            "criteria": self.criteria.to_dict(),
            "case_groups": self.case_groups,
            # UX改善④新: 解析戦略メモ
            "strategy_notes": self.strategy_notes,
            # DYD 履歴結果の出力指定オーバーライド
            "dyd_history_overrides": (
                {str(k): v for k, v in self.dyd_history_overrides.items()}
                if self.dyd_history_overrides is not None else None
            ),
        }
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        self.modified = False
        return self.file_path

    @classmethod
    def load(cls, path: str) -> "Project":
        """.snapproj ファイルを読み込んでプロジェクトを返します。"""
        fp = Path(path)
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)

        proj = cls(name=data.get("name", fp.stem))
        proj.snap_exe_path = data.get("snap_exe_path", "")
        proj.snap_work_dir = data.get("snap_work_dir", "")
        proj.s8i_path = data.get("s8i_path", data.get("model_path", ""))
        proj.created_at = data.get("created_at", "")
        proj.updated_at = data.get("updated_at", "")
        proj.file_path = fp
        proj.modified = False

        # 目標性能基準
        criteria_data = data.get("criteria")
        if criteria_data:
            proj.criteria = PerformanceCriteria.from_dict(criteria_data)

        # ケースグループ
        proj.case_groups = data.get("case_groups", {})
        # UX改善④新: 解析戦略メモ
        proj.strategy_notes = data.get("strategy_notes", "")
        # DYD 履歴結果の出力指定オーバーライド
        dyd_raw = data.get("dyd_history_overrides")
        if dyd_raw is not None:
            proj.dyd_history_overrides = {int(k): v for k, v in dyd_raw.items()}

        for case_data in data.get("cases", []):
            proj.cases.append(AnalysisCase.from_dict(case_data))

        # .s8i ファイルが存在する場合は自動でパース
        if proj.s8i_path:
            try:
                proj.s8i_model = parse_s8i(proj.s8i_path)
            except Exception:
                logger.warning("s8iファイルのパースに失敗: %s", proj.s8i_path, exc_info=True)
                proj.s8i_model = None

        return proj

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _touch(self) -> None:
        """変更フラグを立てます。"""
        self.modified = True

    @property
    def title(self) -> str:
        """ウィンドウタイトル用の文字列を返します。"""
        mod = " *" if self.modified else ""
        path_part = f" — {self.file_path.name}" if self.file_path else ""
        return f"{self.name}{path_part}{mod}"
