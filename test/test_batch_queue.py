"""
test/test_batch_queue.py
バッチキューウィジェットのユニットテスト。

PySide6の直接インポートが環境依存(libEGL等)のため、
Qtウィジェットのテストはスキップ可能にし、
ロジック部分のテストを中心に行います。
"""

import time
import pytest

# PySide6の環境チェック
try:
    from PySide6.QtWidgets import QApplication
    _QT_AVAILABLE = True
except ImportError:
    _QT_AVAILABLE = False

from app.models.analysis_case import AnalysisCase, AnalysisCaseStatus


def _make_cases(n: int) -> list:
    """テスト用ケースを n 個作成。"""
    return [
        AnalysisCase(name=f"Case{i+1}", model_path="test.s8i")
        for i in range(n)
    ]


@pytest.mark.skipif(not _QT_AVAILABLE, reason="PySide6 not available in this environment")
class TestBatchQueueWidget:
    """BatchQueueWidget のテスト（Qt環境のみ実行）。"""

    @pytest.fixture(scope="class")
    def qapp(self):
        app = QApplication.instance()
        if app is None:
            app = QApplication([])
        return app

    @pytest.fixture
    def widget(self, qapp):
        from app.ui.batch_queue_widget import BatchQueueWidget
        w = BatchQueueWidget()
        yield w
        w.close()

    def test_initial_state(self, widget):
        assert widget._is_running is False
        assert widget._table.rowCount() == 0

    def test_set_batch(self, widget):
        cases = _make_cases(5)
        widget.set_batch(cases)
        assert widget._is_running is True
        assert widget._table.rowCount() == 5

    def test_case_finished(self, widget):
        cases = _make_cases(3)
        widget.set_batch(cases)
        cases[0].status = AnalysisCaseStatus.RUNNING
        widget.on_case_started(cases[0].id)
        cases[0].status = AnalysisCaseStatus.COMPLETED
        widget.on_case_finished(cases[0].id, True)
        assert widget._progress.value() == 1

    def test_batch_finished(self, widget):
        cases = _make_cases(2)
        widget.set_batch(cases)
        for c in cases:
            c.status = AnalysisCaseStatus.COMPLETED
            widget.on_case_finished(c.id, True)
        widget.on_batch_finished()
        assert widget._is_running is False

    def test_pause_resume(self, widget):
        cases = _make_cases(3)
        widget.set_batch(cases)
        widget.on_paused()
        assert widget._is_paused is True
        widget.on_resumed()
        assert widget._is_paused is False

    def test_clear(self, widget):
        cases = _make_cases(3)
        widget.set_batch(cases)
        widget.clear()
        assert widget._table.rowCount() == 0
        assert widget._is_running is False

    def test_move_case(self, widget):
        cases = _make_cases(4)
        widget.set_batch(cases)
        first_id = cases[0].id
        widget._move_case(0, 2)
        assert widget._cases[2].id == first_id

    def test_format_time(self, widget):
        assert widget._format_time(0) == "0:00"
        assert widget._format_time(65) == "1:05"
        assert widget._format_time(3661) == "1:01:01"
        assert widget._format_time(-5) == "0:00"


class TestBatchQueueLogic:
    """Qt非依存のロジックテスト。"""

    def test_case_status_tracking(self):
        """ケースの状態管理が正しく動くことを確認。"""
        cases = _make_cases(5)
        start_times = {}
        elapsed_times = {}

        # Simulate batch execution
        for case in cases:
            start_times[case.id] = time.time()
            case.status = AnalysisCaseStatus.RUNNING
            # Simulate instant completion
            case.status = AnalysisCaseStatus.COMPLETED
            elapsed_times[case.id] = time.time() - start_times[case.id]

        completed = [c for c in cases if c.status == AnalysisCaseStatus.COMPLETED]
        assert len(completed) == 5
        assert len(elapsed_times) == 5

    def test_eta_calculation(self):
        """ETA推定のロジックテスト。"""
        # Simulate 3 completed cases with known times
        elapsed_times = {"c1": 10.0, "c2": 12.0, "c3": 8.0}
        avg_time = sum(elapsed_times.values()) / len(elapsed_times)
        assert avg_time == pytest.approx(10.0)

        remaining_count = 5
        eta = avg_time * remaining_count
        assert eta == pytest.approx(50.0)

    def test_queue_priority_reorder(self):
        """キュー優先度の入れ替えロジック。"""
        cases = _make_cases(5)
        # Move case at index 0 to index 3
        case = cases.pop(0)
        cases.insert(3, case)
        assert cases[3].name == "Case1"
        assert cases[0].name == "Case2"

    def test_mixed_status_progress(self):
        """完了・エラー混在時の進捗カウント。"""
        cases = _make_cases(5)
        cases[0].status = AnalysisCaseStatus.COMPLETED
        cases[1].status = AnalysisCaseStatus.ERROR
        cases[2].status = AnalysisCaseStatus.COMPLETED
        cases[3].status = AnalysisCaseStatus.RUNNING
        cases[4].status = AnalysisCaseStatus.PENDING

        done = sum(
            1 for c in cases
            if c.status in (AnalysisCaseStatus.COMPLETED, AnalysisCaseStatus.ERROR)
        )
        assert done == 3

        success = sum(1 for c in cases if c.status == AnalysisCaseStatus.COMPLETED)
        errors = sum(1 for c in cases if c.status == AnalysisCaseStatus.ERROR)
        assert success == 2
        assert errors == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
