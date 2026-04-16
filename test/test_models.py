"""
test/test_models.py
モデル層のユニットテスト。

AnalysisCase, Project, PerformanceCriteria, EarthquakeWaveCatalog
の各データモデルを包括的にテストします。
SNAP本体は不要（モック不要のピュアロジックテスト）。
"""

import json
import tempfile
import uuid
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# AnalysisCase
# ---------------------------------------------------------------------------
from app.models.analysis_case import AnalysisCase, AnalysisCaseStatus


class TestAnalysisCase:
    """AnalysisCase のユニットテスト。"""

    def test_default_creation(self):
        case = AnalysisCase()
        assert case.name == "新規ケース"
        assert case.status == AnalysisCaseStatus.PENDING
        assert case.model_path == ""
        assert case.result_summary == {}
        # UUID が生成されている
        uuid.UUID(case.id)  # 例外が出なければOK

    def test_unique_ids(self):
        c1 = AnalysisCase()
        c2 = AnalysisCase()
        assert c1.id != c2.id

    def test_to_dict_and_from_dict(self):
        case = AnalysisCase(
            name="テスト",
            model_path="/path/to/model.s8i",
            parameters={"DAMPING": 0.05},
            damper_params={"type": "oil", "Cd": 500},
            status=AnalysisCaseStatus.COMPLETED,
            notes="メモ",
        )
        d = case.to_dict()
        assert d["status"] == "completed"
        assert d["name"] == "テスト"

        restored = AnalysisCase.from_dict(d)
        assert restored.name == case.name
        assert restored.id == case.id
        assert restored.status == AnalysisCaseStatus.COMPLETED
        assert restored.parameters == {"DAMPING": 0.05}
        assert restored.damper_params == {"type": "oil", "Cd": 500}

    def test_is_runnable(self):
        case = AnalysisCase()
        # モデルパスなし → False
        assert case.is_runnable() is False
        assert case.is_runnable("SNAP.exe") is False

        case.model_path = "model.s8i"
        assert case.is_runnable("SNAP.exe") is True
        assert case.is_runnable() is False

        case.snap_exe_path = "SNAP.exe"
        assert case.is_runnable() is True

    def test_reset(self):
        case = AnalysisCase(
            status=AnalysisCaseStatus.COMPLETED,
            return_code=0,
            result_summary={"max_drift": 0.005},
        )
        case.reset()
        assert case.status == AnalysisCaseStatus.PENDING
        assert case.return_code is None
        assert case.result_summary == {}

    def test_get_status_label(self):
        for status, label in [
            (AnalysisCaseStatus.PENDING, "未実行"),
            (AnalysisCaseStatus.RUNNING, "実行中"),
            (AnalysisCaseStatus.COMPLETED, "完了"),
            (AnalysisCaseStatus.ERROR, "エラー"),
        ]:
            case = AnalysisCase(status=status)
            assert case.get_status_label() == label

    def test_clone(self):
        original = AnalysisCase(
            name="Original",
            model_path="model.s8i",
            parameters={"K": 100},
            status=AnalysisCaseStatus.COMPLETED,
            result_summary={"max_drift": 0.01},
        )
        clone = original.clone()
        assert clone.id != original.id
        assert clone.name == "Original (コピー)"
        assert clone.model_path == "model.s8i"
        assert clone.parameters == {"K": 100}
        assert clone.status == AnalysisCaseStatus.PENDING
        assert clone.result_summary == {}

    def test_status_enum_values(self):
        assert AnalysisCaseStatus.PENDING.value == "pending"
        assert AnalysisCaseStatus.RUNNING.value == "running"
        assert AnalysisCaseStatus.COMPLETED.value == "completed"
        assert AnalysisCaseStatus.ERROR.value == "error"


# ---------------------------------------------------------------------------
# PerformanceCriteria
# ---------------------------------------------------------------------------
from app.models.performance_criteria import PerformanceCriteria, CriterionItem


class TestPerformanceCriteria:
    """PerformanceCriteria のユニットテスト。"""

    def test_default_criteria(self):
        pc = PerformanceCriteria()
        assert pc.name == "デフォルト基準"
        assert len(pc.items) == 7
        # max_drift はデフォルトで有効
        drift = next(it for it in pc.items if it.key == "max_drift")
        assert drift.enabled is True
        assert drift.limit_value == pytest.approx(1 / 200)

    def test_evaluate_pass(self):
        pc = PerformanceCriteria()
        result = {"max_drift": 0.003}  # 基準 1/200 = 0.005 以下
        verdicts = pc.evaluate(result)
        assert verdicts["max_drift"] is True

    def test_evaluate_fail(self):
        pc = PerformanceCriteria()
        result = {"max_drift": 0.01}  # 基準 1/200 = 0.005 を超過
        verdicts = pc.evaluate(result)
        assert verdicts["max_drift"] is False

    def test_evaluate_missing_key(self):
        pc = PerformanceCriteria()
        verdicts = pc.evaluate({})
        # max_drift は有効だが値がない → None
        assert verdicts["max_drift"] is None

    def test_evaluate_disabled_item(self):
        pc = PerformanceCriteria()
        # max_acc はデフォルトで無効
        acc = next(it for it in pc.items if it.key == "max_acc")
        assert acc.enabled is False
        verdicts = pc.evaluate({"max_acc": 999.0})
        assert verdicts["max_acc"] is None

    def test_is_all_pass(self):
        pc = PerformanceCriteria()
        # 全て有効な項目が合格
        result = {"max_drift": 0.001}
        assert pc.is_all_pass(result) is True

        # 不合格あり
        result = {"max_drift": 0.01}
        assert pc.is_all_pass(result) is False

    def test_is_all_pass_no_enabled(self):
        pc = PerformanceCriteria()
        for item in pc.items:
            item.enabled = False
        assert pc.is_all_pass({}) is None

    def test_get_summary_text(self):
        pc = PerformanceCriteria()
        result = {"max_drift": 0.003}
        text = pc.get_summary_text(result)
        assert "✓" in text
        assert "最大層間変形角" in text

    def test_serialization(self):
        pc = PerformanceCriteria(name="テスト基準")
        pc.items[0].limit_value = 0.01
        d = pc.to_dict()
        restored = PerformanceCriteria.from_dict(d)
        assert restored.name == "テスト基準"
        assert restored.items[0].limit_value == 0.01

    def test_criterion_item_serialization(self):
        item = CriterionItem(
            key="test", label="テスト", unit="m",
            enabled=True, limit_value=0.5, decimals=3
        )
        d = item.to_dict()
        restored = CriterionItem.from_dict(d)
        assert restored.key == "test"
        assert restored.limit_value == 0.5


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------
from app.models.project import Project


class TestProject:
    """Project のユニットテスト。"""

    def test_default_creation(self):
        proj = Project()
        assert proj.name == "新規プロジェクト"
        assert proj.cases == []
        assert proj.modified is False

    def test_add_case(self):
        proj = Project()
        case = proj.add_case()
        assert len(proj.cases) == 1
        assert case.name == "Case 1"
        assert proj.modified is True

    def test_add_case_with_s8i(self):
        proj = Project()
        proj.s8i_path = "/path/to/model.s8i"
        case = proj.add_case()
        assert case.model_path == "/path/to/model.s8i"

    def test_remove_case(self):
        proj = Project()
        case = proj.add_case()
        removed = proj.remove_case(case.id)
        assert removed is True
        assert len(proj.cases) == 0

    def test_remove_nonexistent_case(self):
        proj = Project()
        removed = proj.remove_case("nonexistent-id")
        assert removed is False

    def test_get_case(self):
        proj = Project()
        case = proj.add_case()
        found = proj.get_case(case.id)
        assert found is not None
        assert found.id == case.id

        assert proj.get_case("nonexistent") is None

    def test_duplicate_case(self):
        proj = Project()
        case = proj.add_case()
        case.parameters = {"K": 100}
        clone = proj.duplicate_case(case.id)
        assert clone is not None
        assert len(proj.cases) == 2
        assert clone.id != case.id
        assert clone.parameters == {"K": 100}

    def test_duplicate_nonexistent(self):
        proj = Project()
        assert proj.duplicate_case("nonexistent") is None

    def test_get_completed_cases(self):
        proj = Project()
        c1 = proj.add_case()
        c2 = proj.add_case()
        c3 = proj.add_case()
        c1.status = AnalysisCaseStatus.COMPLETED
        c3.status = AnalysisCaseStatus.COMPLETED
        completed = proj.get_completed_cases()
        assert len(completed) == 2

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            proj = Project(name="テストPJ")
            proj.snap_exe_path = "C:/SNAP/SNAP.exe"
            proj.s8i_path = "C:/models/test.s8i"
            case = proj.add_case()
            case.parameters = {"DAMPING": 0.05}
            case.status = AnalysisCaseStatus.COMPLETED
            case.result_summary = {"max_drift": 0.004}

            save_path = str(Path(tmpdir) / "test.snapproj")
            proj.save(save_path)

            loaded = Project.load(save_path)
            assert loaded.name == "テストPJ"
            assert loaded.snap_exe_path == "C:/SNAP/SNAP.exe"
            assert len(loaded.cases) == 1
            assert loaded.cases[0].parameters == {"DAMPING": 0.05}
            assert loaded.cases[0].status == AnalysisCaseStatus.COMPLETED
            assert loaded.modified is False

    def test_save_requires_path(self):
        proj = Project()
        with pytest.raises(ValueError):
            proj.save()

    def test_title_property(self):
        proj = Project(name="MyProject")
        assert "MyProject" in proj.title
        proj.modified = True
        assert "*" in proj.title

    def test_has_s8i(self):
        proj = Project()
        assert proj.has_s8i is False

    def test_case_groups(self):
        proj = Project()
        c1 = proj.add_case()
        c2 = proj.add_case()
        proj.case_groups["Group A"] = [c1.id, c2.id]
        assert len(proj.case_groups) == 1
        assert len(proj.case_groups["Group A"]) == 2


# ---------------------------------------------------------------------------
# EarthquakeWaveCatalog
# ---------------------------------------------------------------------------
from app.models.earthquake_wave import (
    EarthquakeWave, EarthquakeWaveCatalog, get_wave_catalog,
)


class TestEarthquakeWaveCatalog:
    """EarthquakeWaveCatalog のユニットテスト。"""

    def test_builtin_waves(self):
        catalog = EarthquakeWaveCatalog()
        waves = catalog.all_waves
        assert len(waves) >= 13  # 最低13波は組み込み

    def test_get_by_id(self):
        catalog = EarthquakeWaveCatalog()
        wave = catalog.get_by_id("el_centro_ns")
        assert wave is not None
        assert "El Centro" in wave.name

    def test_get_by_id_not_found(self):
        catalog = EarthquakeWaveCatalog()
        assert catalog.get_by_id("nonexistent") is None

    def test_get_by_category(self):
        catalog = EarthquakeWaveCatalog()
        observed = catalog.get_by_category("observed")
        assert len(observed) >= 6
        for w in observed:
            assert w.category == "observed"

    def test_search(self):
        catalog = EarthquakeWaveCatalog()
        results = catalog.search("神戸")
        assert len(results) >= 2
        for r in results:
            assert "神戸" in r.name or "神戸" in r.description

    def test_add_custom(self):
        catalog = EarthquakeWaveCatalog()
        before = len(catalog.all_waves)
        custom = EarthquakeWave(
            id="custom_test",
            name="テスト地震波",
            category="custom",
        )
        catalog.add_custom(custom)
        assert len(catalog.all_waves) == before + 1
        assert catalog.get_by_id("custom_test") is not None
        assert catalog.get_by_id("custom_test").is_builtin is False

    def test_remove_custom(self):
        catalog = EarthquakeWaveCatalog()
        custom = EarthquakeWave(id="custom_rm", name="削除テスト", category="custom")
        catalog.add_custom(custom)
        assert catalog.remove_custom("custom_rm") is True
        assert catalog.get_by_id("custom_rm") is None

    def test_remove_builtin_fails(self):
        catalog = EarthquakeWaveCatalog()
        assert catalog.remove_custom("el_centro_ns") is False
        assert catalog.get_by_id("el_centro_ns") is not None

    def test_save_and_load_custom(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            catalog = EarthquakeWaveCatalog()
            custom = EarthquakeWave(id="cst1", name="保存テスト", category="custom")
            catalog.add_custom(custom)

            path = str(Path(tmpdir) / "custom_waves.json")
            catalog.save_custom(path)

            catalog2 = EarthquakeWaveCatalog()
            loaded = catalog2.load_custom(path)
            assert loaded == 1
            assert catalog2.get_by_id("cst1") is not None

    def test_get_categories(self):
        catalog = EarthquakeWaveCatalog()
        cats = catalog.get_categories()
        assert len(cats) >= 3
        assert all("key" in c and "label" in c for c in cats)

    def test_wave_serialization(self):
        wave = EarthquakeWave(
            id="test", name="テスト", category="observed",
            max_acc=500.0, duration=30.0, dt=0.02,
        )
        d = wave.to_dict()
        restored = EarthquakeWave.from_dict(d)
        assert restored.id == "test"
        assert restored.max_acc == 500.0

    def test_global_catalog_singleton(self):
        c1 = get_wave_catalog()
        c2 = get_wave_catalog()
        assert c1 is c2


# ---------------------------------------------------------------------------
# Validation Service
# ---------------------------------------------------------------------------
from app.services.validation import (
    validate_case, validate_batch, validate_criteria,
    ValidationResult, ValidationMessage, ValidationLevel,
)


class TestValidation:
    """バリデーションサービスのユニットテスト。"""

    def test_empty_case_has_errors(self):
        case = AnalysisCase()
        result = validate_case(case)
        assert result.has_errors is True
        assert result.error_count >= 2  # model_path, snap_exe_path

    def test_valid_case_paths_exist(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # ダミーファイルを作成
            model_path = Path(tmpdir) / "test.s8i"
            model_path.write_text("dummy", encoding="utf-8")
            exe_path = Path(tmpdir) / "SNAP.exe"
            exe_path.write_text("dummy", encoding="utf-8")

            case = AnalysisCase(
                name="Valid",
                model_path=str(model_path),
            )
            result = validate_case(case, snap_exe_path=str(exe_path))
            assert result.has_errors is False

    def test_missing_model_file_error(self):
        case = AnalysisCase(model_path="/nonexistent/model.s8i")
        result = validate_case(case, snap_exe_path="/nonexistent/SNAP.exe")
        errors = [m for m in result.messages if m.level == ValidationLevel.ERROR]
        assert len(errors) >= 1

    def test_validate_batch(self):
        c1 = AnalysisCase(name="C1")
        c2 = AnalysisCase(name="C2")
        results = validate_batch([c1, c2])
        assert len(results) == 2
        assert c1.id in results
        assert c2.id in results

    def test_validate_criteria_ok(self):
        pc = PerformanceCriteria()
        result = validate_criteria(pc)
        assert result.has_errors is False

    def test_validate_criteria_large_drift(self):
        pc = PerformanceCriteria()
        drift = next(it for it in pc.items if it.key == "max_drift")
        drift.limit_value = 0.05  # 非常に大きい
        result = validate_criteria(pc)
        assert result.has_warnings is True

    def test_validate_criteria_small_drift(self):
        pc = PerformanceCriteria()
        drift = next(it for it in pc.items if it.key == "max_drift")
        drift.limit_value = 0.0005  # 非常に小さい
        result = validate_criteria(pc)
        assert result.has_warnings is True

    def test_validation_result_properties(self):
        vr = ValidationResult()
        assert vr.is_valid is True
        assert vr.error_count == 0

        vr.error("test", "Error message")
        assert vr.is_valid is False
        assert vr.error_count == 1

        vr.warning("test", "Warning message")
        assert vr.warning_count == 1

        vr.info("test", "Info message")
        assert vr.info_count == 1

    def test_validation_display_text(self):
        vr = ValidationResult()
        text = vr.get_display_text()
        assert "OK" in text

        vr.error("test", "Error!")
        text = vr.get_display_text()
        assert "エラー: 1" in text


# ---------------------------------------------------------------------------
# Optimizer
# ---------------------------------------------------------------------------
from app.services.optimizer import (
    ParameterRange, OptimizationConfig, OptimizationResult,
    OptimizationCandidate, _mock_evaluate,
)


class TestOptimizer:
    """最適化エンジンのユニットテスト。"""

    def test_parameter_range_discrete(self):
        pr = ParameterRange(key="Cd", min_val=100, max_val=500, step=100)
        values = pr.discrete_values()
        assert values == [100, 200, 300, 400, 500]

    def test_parameter_range_continuous(self):
        pr = ParameterRange(key="alpha", min_val=0.0, max_val=1.0, step=0)
        values = pr.discrete_values()
        assert len(values) == 20
        assert values[0] == pytest.approx(0.0)
        assert values[-1] == pytest.approx(1.0)

    def test_parameter_range_integer(self):
        pr = ParameterRange(key="N", min_val=1, max_val=5, step=1, is_integer=True)
        values = pr.discrete_values()
        assert all(isinstance(v, int) for v in values)
        assert values == [1, 2, 3, 4, 5]

    def test_parameter_range_random(self):
        pr = ParameterRange(key="Cd", min_val=100, max_val=500, step=0)
        for _ in range(100):
            val = pr.random_value()
            assert 100 <= val <= 500

    def test_parameter_range_random_integer(self):
        pr = ParameterRange(key="N", min_val=1, max_val=5, step=0, is_integer=True)
        for _ in range(100):
            val = pr.random_value()
            assert val == round(val)
            assert 1 <= val <= 5

    def test_mock_evaluate(self):
        params = {"Cd": 300, "alpha": 0.4, "Qd": 200, "K": 50000}
        base = {"max_drift": 0.005, "max_acc": 3.0}
        result = _mock_evaluate(params, base, "max_drift")
        assert "max_drift" in result
        assert "max_acc" in result
        assert "max_disp" in result
        assert result["max_drift"] > 0

    def test_optimization_result_properties(self):
        candidates = [
            OptimizationCandidate(params={"Cd": 100}, objective_value=0.01, is_feasible=True),
            OptimizationCandidate(params={"Cd": 200}, objective_value=0.005, is_feasible=True),
            OptimizationCandidate(params={"Cd": 300}, objective_value=0.008, is_feasible=False),
        ]
        result = OptimizationResult(
            best=candidates[1],
            all_candidates=candidates,
        )
        assert len(result.feasible_candidates) == 2
        assert result.ranked_candidates[0].objective_value == 0.005

    def test_optimization_result_summary(self):
        config = OptimizationConfig(
            objective_key="max_drift",
            objective_label="最大層間変形角",
            method="grid",
            damper_type="オイルダンパー",
        )
        best = OptimizationCandidate(
            params={"Cd": 200},
            objective_value=0.003,
            response_values={"max_drift": 0.003},
        )
        result = OptimizationResult(
            best=best,
            all_candidates=[best],
            config=config,
            elapsed_sec=1.5,
        )
        text = result.get_summary_text()
        assert "最良解" in text
        assert "0.003" in text


# ---------------------------------------------------------------------------
# Result (controller)
# ---------------------------------------------------------------------------
from controller.result import Result


class TestResult:
    """Result パーサーのユニットテスト。"""

    def test_mock_result(self):
        res = Result.from_mock(floors=5)
        assert len(res.max_disp) == 5
        assert len(res.max_vel) == 5
        assert len(res.max_acc) == 5
        assert len(res.max_story_disp) == 5
        assert len(res.max_story_drift) == 5
        assert len(res.shear_coeff) == 5
        assert len(res.max_otm) == 5

    def test_get_all(self):
        res = Result.from_mock(floors=3)
        all_data = res.get_all()
        assert len(all_data) == 7
        assert "max_disp" in all_data

    def test_get_floor_count(self):
        res = Result.from_mock(floors=10)
        assert res.get_floor_count() == 10

    def test_to_dataframe(self):
        res = Result.from_mock(floors=5)
        try:
            df = res.to_dataframe()
            assert len(df) == 5
            assert "Floor" in df.columns
        except ImportError:
            pytest.skip("pandas not installed")

    def test_empty_result(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            res = Result(tmpdir)
            assert res.max_disp == {}
            assert res.get_floor_count() == 0

    def test_nonexistent_dir(self):
        res = Result("/nonexistent/path/12345")
        assert res.max_disp == {}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
