"""
app/services/validation.py
入力バリデーションフレームワーク。

解析実行前にケースの設定が妥当かどうかを検証し、
エラー・警告・情報のリストを返します。

検証項目:
  - 必須フィールド（モデルパス、SNAP.exeパス）
  - ファイル存在チェック
  - ダンパーパラメータの値範囲チェック
  - ダンパー配置の整合性（節点存在、重複チェック）
  - 性能基準の合理性チェック
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.models.analysis_case import AnalysisCase
from app.models.s8i_parser import S8iModel
from app.models.performance_criteria import PerformanceCriteria


class ValidationLevel(str, Enum):
    """バリデーション結果のレベル。"""
    ERROR = "error"       # 解析不可（必ず修正が必要）
    WARNING = "warning"   # 解析可能だが推奨しない
    INFO = "info"         # 参考情報


@dataclass
class ValidationMessage:
    """バリデーション結果の1メッセージ。"""
    level: ValidationLevel
    category: str        # カテゴリ（"file", "parameter", "placement", etc.）
    message: str         # メッセージ本文
    field: str = ""      # 関連フィールド名
    suggestion: str = "" # 修正の提案

    @property
    def icon(self) -> str:
        icons = {
            ValidationLevel.ERROR: "❌",
            ValidationLevel.WARNING: "⚠️",
            ValidationLevel.INFO: "ℹ️",
        }
        return icons.get(self.level, "")

    def to_display(self) -> str:
        """表示用文字列。"""
        parts = [f"{self.icon} [{self.category}] {self.message}"]
        if self.suggestion:
            parts.append(f"  → {self.suggestion}")
        return "\n".join(parts)


@dataclass
class ValidationResult:
    """バリデーション全体の結果。"""
    messages: List[ValidationMessage] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(m.level == ValidationLevel.ERROR for m in self.messages)

    @property
    def has_warnings(self) -> bool:
        return any(m.level == ValidationLevel.WARNING for m in self.messages)

    @property
    def error_count(self) -> int:
        return sum(1 for m in self.messages if m.level == ValidationLevel.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for m in self.messages if m.level == ValidationLevel.WARNING)

    @property
    def info_count(self) -> int:
        return sum(1 for m in self.messages if m.level == ValidationLevel.INFO)

    @property
    def is_valid(self) -> bool:
        """エラーがなければ有効。"""
        return not self.has_errors

    def add(self, level: ValidationLevel, category: str, message: str,
            field_name: str = "", suggestion: str = "") -> None:
        self.messages.append(ValidationMessage(
            level=level, category=category, message=message,
            field=field_name, suggestion=suggestion,
        ))

    def error(self, category: str, message: str, **kwargs) -> None:
        self.add(ValidationLevel.ERROR, category, message, **kwargs)

    def warning(self, category: str, message: str, **kwargs) -> None:
        self.add(ValidationLevel.WARNING, category, message, **kwargs)

    def info(self, category: str, message: str, **kwargs) -> None:
        self.add(ValidationLevel.INFO, category, message, **kwargs)

    def get_display_text(self) -> str:
        """全メッセージの表示用テキスト。"""
        if not self.messages:
            return "✅ バリデーション OK: 問題は見つかりませんでした。"
        lines = []
        for m in sorted(self.messages, key=lambda x: x.level.value):
            lines.append(m.to_display())
        summary = f"エラー: {self.error_count}, 警告: {self.warning_count}, 情報: {self.info_count}"
        lines.insert(0, f"=== バリデーション結果 ({summary}) ===")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# バリデータ関数
# ---------------------------------------------------------------------------

def validate_case(
    case: AnalysisCase,
    snap_exe_path: str = "",
    s8i_model: Optional[S8iModel] = None,
) -> ValidationResult:
    """
    解析ケースの入力を検証します。

    Parameters
    ----------
    case : AnalysisCase
        検証する解析ケース。
    snap_exe_path : str
        プロジェクトレベルのSNAP.exeパス。
    s8i_model : S8iModel, optional
        パース済みの.s8iモデル（配置チェック用）。

    Returns
    -------
    ValidationResult
    """
    result = ValidationResult()

    # ---- 1. 必須フィールド ----
    _validate_required_fields(result, case, snap_exe_path)

    # ---- 2. ファイル存在チェック ----
    _validate_file_existence(result, case, snap_exe_path)

    # ---- 3. ダンパーパラメータチェック ----
    _validate_damper_params(result, case, s8i_model)

    # ---- 4. ダンパー配置チェック ----
    _validate_damper_placement(result, case, s8i_model)

    # ---- 5. 情報メッセージ ----
    _add_info_messages(result, case, s8i_model)

    return result


def validate_batch(
    cases: List[AnalysisCase],
    snap_exe_path: str = "",
    s8i_model: Optional[S8iModel] = None,
) -> Dict[str, ValidationResult]:
    """
    複数ケースを一括検証します。

    Returns
    -------
    dict
        {case_id: ValidationResult}
    """
    results = {}
    for case in cases:
        results[case.id] = validate_case(case, snap_exe_path, s8i_model)
    return results


def validate_criteria(criteria: PerformanceCriteria) -> ValidationResult:
    """
    性能基準の妥当性を検証します。
    """
    result = ValidationResult()

    for item in criteria.items:
        if not item.enabled:
            continue
        if item.limit_value is None or item.limit_value <= 0:
            result.warning(
                "基準",
                f"「{item.label}」の目標値が未設定または0以下です。",
                field_name=item.key,
                suggestion="正の値を設定してください。",
            )

    # 層間変形角の合理性チェック
    drift_item = next(
        (it for it in criteria.items if it.key == "max_drift" and it.enabled),
        None,
    )
    if drift_item and drift_item.limit_value:
        if drift_item.limit_value > 0.02:
            result.warning(
                "基準",
                f"層間変形角の目標値 {drift_item.limit_value} が大きすぎます。"
                "通常、耐震設計では 1/200 (0.005) ～ 1/100 (0.01) が目標です。",
                field_name="max_drift",
                suggestion="1/200 (0.005) ～ 1/100 (0.01) の範囲を検討してください。",
            )
        elif drift_item.limit_value < 0.001:
            result.warning(
                "基準",
                f"層間変形角の目標値 {drift_item.limit_value} が非常に厳しい値です。",
                field_name="max_drift",
                suggestion="免震構造でも 1/500 (0.002) 程度が一般的です。",
            )

    # 加速度の合理性チェック
    acc_item = next(
        (it for it in criteria.items if it.key == "max_acc" and it.enabled),
        None,
    )
    if acc_item and acc_item.limit_value:
        if acc_item.limit_value > 20.0:
            result.warning(
                "基準",
                f"最大応答加速度の目標値 {acc_item.limit_value} m/s² が非常に大きいです。",
                field_name="max_acc",
            )

    return result


# ---------------------------------------------------------------------------
# 内部バリデーション関数
# ---------------------------------------------------------------------------

def _validate_required_fields(
    result: ValidationResult,
    case: AnalysisCase,
    snap_exe_path: str,
) -> None:
    """必須フィールドの存在チェック。"""
    if not case.model_path:
        result.error(
            "ファイル",
            "入力ファイル (.s8i) が指定されていません。",
            field_name="model_path",
            suggestion="プロジェクトで .s8i ファイルを読み込んでください。",
        )

    exe = snap_exe_path or case.snap_exe_path
    if not exe:
        result.error(
            "ファイル",
            "SNAP.exe のパスが指定されていません。",
            field_name="snap_exe_path",
            suggestion="設定 → アプリケーション設定で SNAP.exe を指定してください。",
        )

    if not case.name or case.name.strip() == "":
        result.warning(
            "基本",
            "ケース名が空です。",
            field_name="name",
            suggestion="識別しやすいケース名を設定することを推奨します。",
        )


def _validate_file_existence(
    result: ValidationResult,
    case: AnalysisCase,
    snap_exe_path: str,
) -> None:
    """ファイルの存在チェック。"""
    if case.model_path:
        p = Path(case.model_path)
        if not p.exists():
            result.error(
                "ファイル",
                f"入力ファイルが見つかりません: {case.model_path}",
                field_name="model_path",
                suggestion="ファイルパスを確認するか、別のファイルを指定してください。",
            )
        elif not p.suffix.lower() in (".s8i", ".dat", ".inp"):
            result.warning(
                "ファイル",
                f"入力ファイルの拡張子が .s8i 以外です: {p.suffix}",
                field_name="model_path",
            )

    exe = snap_exe_path or case.snap_exe_path
    if exe:
        p = Path(exe)
        if not p.exists():
            result.error(
                "ファイル",
                f"SNAP.exe が見つかりません: {exe}",
                field_name="snap_exe_path",
                suggestion="SNAP のインストールディレクトリを確認してください。",
            )

    if case.output_dir:
        out = Path(case.output_dir)
        if not out.exists():
            result.info(
                "ファイル",
                f"出力ディレクトリが存在しません（自動作成されます）: {case.output_dir}",
                field_name="output_dir",
            )


def _validate_damper_params(
    result: ValidationResult,
    case: AnalysisCase,
    s8i_model: Optional[S8iModel],
) -> None:
    """ダンパーパラメータの値チェック。"""
    if not case.damper_params:
        return

    for def_name, overrides in case.damper_params.items():
        if not isinstance(overrides, dict):
            continue

        for idx_str, val in overrides.items():
            # 数値変換可能かチェック
            try:
                float_val = float(val)
            except (ValueError, TypeError):
                result.error(
                    "パラメータ",
                    f"ダンパー「{def_name}」のフィールド {idx_str} の値 "
                    f"'{val}' は数値として解釈できません。",
                    field_name=f"damper_params.{def_name}.{idx_str}",
                )
                continue

            # 負の値チェック（物理量として通常ありえないもの）
            if int(idx_str) in (7, 8, 9, 10) and float_val < 0:
                result.warning(
                    "パラメータ",
                    f"ダンパー「{def_name}」のフィールド {idx_str} の値 "
                    f"{float_val} が負です。",
                    field_name=f"damper_params.{def_name}.{idx_str}",
                    suggestion="物理量として正の値が期待されます。",
                )

            # ゼロチェック（減衰係数、剛性がゼロ）
            if int(idx_str) in (8,) and float_val == 0:
                result.warning(
                    "パラメータ",
                    f"ダンパー「{def_name}」の主要パラメータ（フィールド {idx_str}）"
                    f"がゼロです。ダンパーが無効になっている可能性があります。",
                    field_name=f"damper_params.{def_name}.{idx_str}",
                )


def _validate_damper_placement(
    result: ValidationResult,
    case: AnalysisCase,
    s8i_model: Optional[S8iModel],
) -> None:
    """ダンパー配置の整合性チェック。"""
    rd_overrides = case.parameters.get("_rd_overrides", {})
    if not rd_overrides or not s8i_model:
        return

    node_ids = {n.id for n in s8i_model.nodes} if s8i_model.nodes else set()

    for idx_str, changes in rd_overrides.items():
        if not isinstance(changes, dict):
            continue

        # 節点存在チェック
        for node_key in ("node_i", "node_j"):
            if node_key in changes:
                node_id = changes[node_key]
                if node_ids and node_id not in node_ids:
                    result.error(
                        "配置",
                        f"RD要素 #{idx_str} の{node_key}={node_id} は"
                        f"モデルに存在しない節点です。",
                        field_name=f"_rd_overrides.{idx_str}.{node_key}",
                        suggestion=f"有効な節点番号を指定してください。"
                                   f"（モデル: {len(node_ids)}節点）",
                    )

        # 基数チェック
        if "quantity" in changes:
            qty = changes["quantity"]
            if isinstance(qty, int):
                if qty <= 0:
                    result.error(
                        "配置",
                        f"RD要素 #{idx_str} の基数が {qty} です。"
                        f"1以上の整数を指定してください。",
                        field_name=f"_rd_overrides.{idx_str}.quantity",
                    )
                elif qty > 20:
                    result.warning(
                        "配置",
                        f"RD要素 #{idx_str} の基数が {qty} と非常に多いです。",
                        field_name=f"_rd_overrides.{idx_str}.quantity",
                        suggestion="通常は1～4程度です。入力値を確認してください。",
                    )

        # 同一節点チェック
        node_i = changes.get("node_i")
        node_j = changes.get("node_j")
        if node_i is not None and node_j is not None and node_i == node_j:
            result.error(
                "配置",
                f"RD要素 #{idx_str} の節点Iと節点Jが同じ値 ({node_i}) です。",
                field_name=f"_rd_overrides.{idx_str}",
                suggestion="ダンパーは異なる2点間に配置する必要があります。",
            )


def _add_info_messages(
    result: ValidationResult,
    case: AnalysisCase,
    s8i_model: Optional[S8iModel],
) -> None:
    """参考情報メッセージ。"""
    if s8i_model:
        result.info(
            "モデル",
            f"モデル情報: {s8i_model.num_nodes}節点, "
            f"{s8i_model.num_floors}層, "
            f"ダンパー定義{len(s8i_model.damper_defs)}種, "
            f"免制振装置{s8i_model.num_dampers}箇所",
        )

    param_count = len(case.damper_params) if case.damper_params else 0
    rd_count = len(case.parameters.get("_rd_overrides", {}))
    if param_count or rd_count:
        result.info(
            "変更",
            f"ケース「{case.name}」: パラメータ変更 {param_count}定義, "
            f"配置変更 {rd_count}箇所",
        )
