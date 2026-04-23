"""app/services/damper_group_check.py
ダンパー装置グループ整合性と「停滞 (no-op) 最適化」検知の共通ヘルパ。

2026-04-22 bug fix のための共通化:
  - 伝達関数評価 (transfer_function_evaluator) と SNAP 汎用評価
    (snap_evaluator) の両方で、ユーザが選んだ damper_def_name が
    対象ケースの装置グループに含まれていないと、どんなにパラメータを
    変更しても応答が変わらない (無言の no-op) 状態になる。
  - さらに、非動的フィールド (温度変動係数・疲労曲線等) を最適化変数
    に選んでしまった場合や、範囲が狭すぎる場合も、結果として評価値が
    変化しない状況が生じる。
  - これらを「評価器レベル」で検知して警告するための共通ロジック。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ダンパー装置グループ整合性チェック
# ---------------------------------------------------------------------------

def warn_if_damper_group_mismatch(
    base_s8i_path: str,
    damper_def_name: str,
    *,
    target_case_no: Optional[int] = None,
    log_callback: Callable[[str], None],
) -> None:
    """対象ケースの装置グループと damper_def_name の整合性を検査して
    不整合ならログに警告を出す。

    SNAP では DYC.values[5] (ダンパーグループ名) と RD.values[0] が一致
    する装置のみが当該ケースで有効。グループが空、または ``damper_def_name``
    がグループ内の RD に出現しない場合は、どんなに DVOD/DSD 値を
    変更しても応答に反映されないため、最適化が無言で no-op になる。

    Parameters
    ----------
    base_s8i_path: str
        ベース .s8i ファイルのパス
    damper_def_name: str
        最適化対象のダンパー定義名。空文字なら検査スキップ。
    target_case_no: Optional[int]
        特定の DYC ケース番号 (1-based) を検査する場合に指定。
        ``None`` の場合は ``run_flag=1`` の全ケースをチェックし、
        どの一つにも damper_def が含まれていなければ警告する。
    log_callback: Callable[[str], None]
        警告/情報メッセージの出力先。必須。
    """
    if not damper_def_name:
        return
    try:
        from app.models.s8i_parser import parse_s8i
        model = parse_s8i(base_s8i_path)
    except Exception:
        logger.debug(
            "装置グループ検査の為の parse_s8i に失敗", exc_info=True,
        )
        return

    if target_case_no is not None:
        _check_single_case(
            model, damper_def_name, target_case_no, log_callback,
        )
    else:
        _check_all_run_cases(model, damper_def_name, log_callback)


def _check_single_case(
    model: Any,
    damper_def_name: str,
    target_case_no: int,
    log_callback: Callable[[str], None],
) -> None:
    case = model.get_dyc_case(target_case_no)
    if case is None:
        log_callback(
            f"  [WARN] ケース D{target_case_no} が .s8i に存在しません。"
        )
        return
    group = case.damper_group
    active_defs = model.active_damper_defs_for_case(target_case_no)
    if not group:
        log_callback(
            f"  [WARN] ケース D{target_case_no} ({case.name}) の"
            f" 装置グループが空欄です。装置 '{damper_def_name}' の"
            f" パラメータを変更しても応答に反映されません (SNAP: 装置未選択)。"
        )
        return
    if damper_def_name not in active_defs:
        log_callback(
            f"  [WARN] ケース D{target_case_no} ({case.name}) の"
            f" 装置グループ '{group}' には装置 '{damper_def_name}'"
            f" が含まれません。パラメータ変更は応答に反映されません。"
            f" このケースで有効な装置: {active_defs}"
        )
        return
    log_callback(
        f"  [INFO] 装置グループ整合性 OK: ケース D{target_case_no}"
        f" ({case.name}), グループ '{group}', 有効装置 {active_defs},"
        f" 最適化対象 '{damper_def_name}'"
    )


def _check_all_run_cases(
    model: Any,
    damper_def_name: str,
    log_callback: Callable[[str], None],
) -> None:
    """run_flag=1 の全ケースを横断的に検査。
    どのケースにも damper_def が含まれない場合に警告する。
    """
    run_cases = [dyc for dyc in model.dyc_cases if dyc.is_run]
    if not run_cases:
        log_callback(
            "  [WARN] run_flag=1 の DYC ケースが一つもありません。"
        )
        return

    cases_with_def: List[Tuple[int, str, str]] = []
    cases_empty: List[Tuple[int, str]] = []
    cases_missing: List[Tuple[int, str, str, List[str]]] = []

    for dyc in run_cases:
        case_no = dyc.case_no
        group = dyc.damper_group
        if not group:
            cases_empty.append((case_no, dyc.name))
            continue
        active = model.active_damper_defs_for_case(case_no)
        if damper_def_name in active:
            cases_with_def.append((case_no, dyc.name, group))
        else:
            cases_missing.append((case_no, dyc.name, group, list(active)))

    if cases_with_def:
        summary = ", ".join(
            f"D{no}({name})[{grp}]" for no, name, grp in cases_with_def
        )
        log_callback(
            f"  [INFO] 装置グループ整合性 OK: 装置 '{damper_def_name}' は"
            f" {len(cases_with_def)} ケース ({summary}) で有効。"
        )
        return

    # 全ケースで不整合 → 最適化は no-op になる
    detail_parts: List[str] = []
    if cases_empty:
        detail_parts.append(
            "グループ空欄: "
            + ", ".join(f"D{no}({name})" for no, name in cases_empty)
        )
    if cases_missing:
        detail_parts.append(
            "装置未所属: "
            + ", ".join(
                f"D{no}({name})[{grp}→{active}]"
                for no, name, grp, active in cases_missing
            )
        )
    log_callback(
        f"  [WARN] 装置 '{damper_def_name}' が実行対象 ({len(run_cases)}"
        " ケース) のいずれの装置グループにも含まれていません。"
        " パラメータ変更は応答に反映されません。"
        + (" (" + " / ".join(detail_parts) + ")" if detail_parts else "")
    )


# ---------------------------------------------------------------------------
# 停滞 (no-op) 最適化検知
# ---------------------------------------------------------------------------

class StagnationDetector:
    """評価ごとに目的関数値を受け取り、停滞を検知したら一度だけ警告する。

    TF モード (dB スケール) と非 TF モード (rad / m/s² / m など) の両方で
    動くよう、絶対許容差と相対許容差の OR で判定する。

    Parameters
    ----------
    log_callback: Callable[[str], None]
        警告出力先。
    min_evals: int, default 3
        警告判定に必要な「異なるパラメータでの成功評価」数。
    abs_tol: float, default 1e-8
        絶対許容差。max - min がこれ以下なら停滞と判定。
        TF (dB) では 0.01 dB 程度、非 TF ではより厳しい値が望ましいので
        呼び出し側で上書きする。
    rel_tol: float, default 1e-4
        相対許容差 (max-min) / max(|max|, |min|, eps) がこれ以下で停滞判定。
    warn_message: Optional[str]
        警告メッセージ。``{n}`` がプレースホルダとして展開される。
    """

    def __init__(
        self,
        log_callback: Callable[[str], None],
        *,
        min_evals: int = 3,
        abs_tol: float = 1e-8,
        rel_tol: float = 1e-4,
        warn_message: Optional[str] = None,
    ) -> None:
        self.log_callback = log_callback
        self.min_evals = min_evals
        self.abs_tol = abs_tol
        self.rel_tol = rel_tol
        self.warn_message = warn_message or (
            "  [WARN] 停滞検知: 異なるパラメータで {n} 回評価しても"
            " 目的関数値がほぼ変動していません。選択した最適化変数が"
            " 応答に効いていない可能性があります"
            " (例: 温度変動係数 / 疲労曲線 / 閾値未満の Fc 等)。"
            " ベースケースのダンパーグループ設定と、動的応答に影響する"
            " フィールド (C0 / α / K / Fy 等) が選択されているか確認してください。"
        )
        self._seen: List[Tuple[str, float]] = []
        self._warned: bool = False

    def record(self, cache_key: str, value: float) -> None:
        """新しい評価値を記録し、停滞を検知したら警告を出す。"""
        if self._warned:
            return
        if any(k == cache_key for k, _ in self._seen):
            return
        self._seen.append((cache_key, value))
        if len(self._seen) < self.min_evals:
            return
        vals = [v for _, v in self._seen]
        if self._is_stagnant(vals):
            self._warned = True
            self.log_callback(self.warn_message.format(n=len(self._seen)))

    def _is_stagnant(self, vals: Iterable[float]) -> bool:
        """絶対 or 相対の許容差以内なら停滞と判定。"""
        values = list(vals)
        if not values:
            return False
        lo = min(values)
        hi = max(values)
        diff = hi - lo
        if diff <= self.abs_tol:
            return True
        scale = max(abs(lo), abs(hi))
        if scale > 0 and diff / scale <= self.rel_tol:
            return True
        return False

    @property
    def detected(self) -> bool:
        return self._warned
