"""tests/test_damper_group_check.py
damper_group_check モジュール (装置グループ整合性と停滞検知) のテスト。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.damper_group_check import (
    StagnationDetector,
    warn_if_damper_group_mismatch,
)


# ---------------------------------------------------------------------------
# StagnationDetector
# ---------------------------------------------------------------------------

class TestStagnationDetector:
    def test_no_warn_below_min_evals(self):
        msgs: list[str] = []
        det = StagnationDetector(msgs.append, min_evals=3)
        det.record("k1", 1.0)
        det.record("k2", 1.0)
        assert not det.detected
        assert not msgs

    def test_warn_after_min_evals_identical(self):
        msgs: list[str] = []
        det = StagnationDetector(
            msgs.append, min_evals=3, abs_tol=1e-8, rel_tol=1e-4,
        )
        det.record("k1", 1.0)
        det.record("k2", 1.0)
        det.record("k3", 1.0)
        assert det.detected
        assert len(msgs) == 1
        assert "停滞検知" in msgs[0]

    def test_warn_message_includes_n(self):
        msgs: list[str] = []
        det = StagnationDetector(
            msgs.append, min_evals=3,
            warn_message="stagnated after {n} evals",
        )
        det.record("k1", 0.1)
        det.record("k2", 0.1)
        det.record("k3", 0.1)
        assert "stagnated after 3 evals" in msgs[0]

    def test_no_warn_when_values_vary(self):
        msgs: list[str] = []
        det = StagnationDetector(msgs.append, min_evals=3)
        det.record("k1", 1.0)
        det.record("k2", 2.0)
        det.record("k3", 3.0)
        assert not det.detected
        assert not msgs

    def test_abs_tolerance_triggers(self):
        """絶対許容差以下なら検知 (相対許容差が効かないスケールでも)。"""
        msgs: list[str] = []
        det = StagnationDetector(
            msgs.append, min_evals=3, abs_tol=1e-5, rel_tol=1e-10,
        )
        det.record("k1", 1e-8)
        det.record("k2", 2e-8)
        det.record("k3", 5e-9)
        # すべて絶対差 < 1e-5 → 停滞
        assert det.detected

    def test_rel_tolerance_triggers(self):
        """大きな値のスケールでも相対差で検知できる。"""
        msgs: list[str] = []
        det = StagnationDetector(
            msgs.append, min_evals=3, abs_tol=1e-12, rel_tol=1e-3,
        )
        det.record("k1", 100.0)
        det.record("k2", 100.05)
        det.record("k3", 100.02)
        # abs_diff=0.05, rel_diff≈5e-4 < 1e-3 → 停滞
        assert det.detected

    def test_rel_tolerance_does_not_trigger(self):
        """相対差が許容を超えれば検知されない。"""
        msgs: list[str] = []
        det = StagnationDetector(
            msgs.append, min_evals=3, abs_tol=1e-12, rel_tol=1e-3,
        )
        det.record("k1", 100.0)
        det.record("k2", 101.0)
        det.record("k3", 100.5)
        # abs_diff=1.0, rel_diff=1e-2 > 1e-3 → 通常
        assert not det.detected

    def test_duplicate_cache_key_not_counted(self):
        msgs: list[str] = []
        det = StagnationDetector(msgs.append, min_evals=3)
        for _ in range(10):
            det.record("k_same", 1.0)
        assert not det.detected

    def test_warn_only_once(self):
        msgs: list[str] = []
        det = StagnationDetector(msgs.append, min_evals=3)
        for i in range(10):
            det.record(f"k{i}", 1.0)
        assert det.detected
        assert len(msgs) == 1


# ---------------------------------------------------------------------------
# warn_if_damper_group_mismatch — all-cases mode (target_case_no=None)
# ---------------------------------------------------------------------------

def _write_s8i(path: Path, dyc_lines: list[str]) -> None:
    """DVOD/RD + 複数 DYC ケースを含む最小 .s8i を書き出す。"""
    content = (
        "DVOD / IOD,0,0,0,,3,100,0,14,0,0,0,0,0,0,0,0,0,0,0,1,0,1\n"
        "RD / IOD,1,2,1,IOD,0,0,0,0,0,1,0,0,1,,0,1,1,0,1\n"
    )
    content += "\n".join(dyc_lines) + "\n"
    path.write_text(content, encoding="shift_jis")


class TestWarnIfDamperGroupMismatchAllCases:
    """target_case_no=None (実行対象全ケース横断) モードのテスト。"""

    def test_skip_if_no_def_name(self, tmp_path):
        s8i = tmp_path / "m.s8i"
        _write_s8i(s8i, [
            "DYC / C1,1,2,0,0,,0,0,,D1,1,10,0,1,0,0,1,DL+LL,,WV,1",
        ])
        msgs: list[str] = []
        warn_if_damper_group_mismatch(
            base_s8i_path=str(s8i),
            damper_def_name="",
            target_case_no=None,
            log_callback=msgs.append,
        )
        assert not msgs

    def test_info_when_at_least_one_case_matches(self, tmp_path):
        """1 ケースでも装置グループに含まれれば INFO ログのみ。"""
        s8i = tmp_path / "m.s8i"
        _write_s8i(s8i, [
            "DYC / C1,1,2,0,0,IOD,0,0,,D1,1,10,0,1,0,0,1,DL+LL,,WV,1",
            "DYC / C2,1,2,0,0,,0,0,,D1,1,10,0,1,0,0,1,DL+LL,,WV,1",
        ])
        msgs: list[str] = []
        warn_if_damper_group_mismatch(
            base_s8i_path=str(s8i),
            damper_def_name="IOD",
            target_case_no=None,
            log_callback=msgs.append,
        )
        warns = [m for m in msgs if "[WARN]" in m]
        infos = [m for m in msgs if "[INFO]" in m]
        assert not warns, f"unexpected warn: {warns}"
        assert infos, f"info missing: {msgs}"
        assert "整合性 OK" in infos[0]

    def test_warn_when_no_case_matches_empty_groups(self, tmp_path):
        """全ケースのグループが空なら [WARN]。"""
        s8i = tmp_path / "m.s8i"
        _write_s8i(s8i, [
            "DYC / C1,1,2,0,0,,0,0,,D1,1,10,0,1,0,0,1,DL+LL,,WV,1",
            "DYC / C2,1,2,0,0,,0,0,,D1,1,10,0,1,0,0,1,DL+LL,,WV,1",
        ])
        msgs: list[str] = []
        warn_if_damper_group_mismatch(
            base_s8i_path=str(s8i),
            damper_def_name="IOD",
            target_case_no=None,
            log_callback=msgs.append,
        )
        warns = [m for m in msgs if "[WARN]" in m]
        assert warns, f"warn missing: {msgs}"
        assert "グループ空欄" in warns[0] or "含まれて" in warns[0]

    def test_warn_when_groups_do_not_contain_def(self, tmp_path):
        """全ケースのグループに def が含まれなければ [WARN]。"""
        s8i = tmp_path / "m.s8i"
        _write_s8i(s8i, [
            "DYC / C1,1,2,0,0,OTHER,0,0,,D1,1,10,0,1,0,0,1,DL+LL,,WV,1",
        ])
        msgs: list[str] = []
        warn_if_damper_group_mismatch(
            base_s8i_path=str(s8i),
            damper_def_name="IOD",
            target_case_no=None,
            log_callback=msgs.append,
        )
        warns = [m for m in msgs if "[WARN]" in m]
        assert warns, f"warn missing: {msgs}"
        assert "'IOD'" in warns[0]

    def test_warn_when_no_run_cases(self, tmp_path):
        """run_flag=0 のみなら [WARN]。"""
        s8i = tmp_path / "m.s8i"
        _write_s8i(s8i, [
            "DYC / C1,0,2,0,0,IOD,0,0,,D1,1,10,0,1,0,0,1,DL+LL,,WV,1",
        ])
        msgs: list[str] = []
        warn_if_damper_group_mismatch(
            base_s8i_path=str(s8i),
            damper_def_name="IOD",
            target_case_no=None,
            log_callback=msgs.append,
        )
        warns = [m for m in msgs if "[WARN]" in m]
        assert warns
        assert "run_flag=1" in warns[0] or "一つもありません" in warns[0]


# ---------------------------------------------------------------------------
# warn_if_damper_group_mismatch — single-case mode (target_case_no=N)
# ---------------------------------------------------------------------------

class TestWarnIfDamperGroupMismatchSingleCase:
    def test_info_when_match(self, tmp_path):
        s8i = tmp_path / "m.s8i"
        _write_s8i(s8i, [
            "DYC / C1,1,2,0,0,IOD,0,0,,D1,1,10,0,1,0,0,1,DL+LL,,WV,1",
        ])
        msgs: list[str] = []
        warn_if_damper_group_mismatch(
            base_s8i_path=str(s8i),
            damper_def_name="IOD",
            target_case_no=1,
            log_callback=msgs.append,
        )
        assert any("整合性 OK" in m for m in msgs)

    def test_warn_when_missing_case_no(self, tmp_path):
        s8i = tmp_path / "m.s8i"
        _write_s8i(s8i, [
            "DYC / C1,1,2,0,0,IOD,0,0,,D1,1,10,0,1,0,0,1,DL+LL,,WV,1",
        ])
        msgs: list[str] = []
        warn_if_damper_group_mismatch(
            base_s8i_path=str(s8i),
            damper_def_name="IOD",
            target_case_no=99,  # 存在しない
            log_callback=msgs.append,
        )
        warns = [m for m in msgs if "[WARN]" in m]
        assert warns
        assert "存在しません" in warns[0]
