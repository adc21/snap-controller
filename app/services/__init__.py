from .validation import (
    validate_case, validate_batch, validate_criteria,
    ValidationResult, ValidationMessage, ValidationLevel,
)

from .snap_evaluator import SnapEvaluator, create_snap_evaluator


def __getattr__(name: str):
    """遅延インポート: PySide6 依存モジュールをトップレベルでロードしない。"""
    if name == "AnalysisService":
        from .analysis_service import AnalysisService
        return AnalysisServi