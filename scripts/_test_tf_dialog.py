"""UI smoke test: instantiate UnifiedOptimizerDialog and toggle TF mode."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import sys
sys.path.insert(0, r"C:/Users/keita/App/ADC/snap-controller")

from PySide6.QtWidgets import QApplication
from app.models import AnalysisCase, Project
from app.models.performance_criteria import PerformanceCriteria
from app.ui.unified_optimizer_dialog import UnifiedOptimizerDialog, _TF_OBJECTIVE_KEY

S8I = r"C:/Users/keita/App/ADC/snap-controller/example_model/example_3D/example_3D.s8i"

app = QApplication.instance() or QApplication(sys.argv)

proj = Project(name="TF_Test")
proj.load_s8i(S8I)
proj.snap_wave_dir = r"D:/Kakemoto/kozosystem/SNAPV8/wave"

case = AnalysisCase()
case.name = "base"
case.model_path = S8I
case.snap_exe_path = r"D:/Kakemoto/kozosystem/SNAPV8/SNAP.exe"
proj.cases.append(case)

dlg = UnifiedOptimizerDialog(
    base_case=case,
    criteria=PerformanceCriteria(),
    snap_exe_path=case.snap_exe_path,
    snap_work_dir=r"D:/Kakemoto/kozosystem/SNAPV8/work",
    project=proj,
)

# Verify default state: TF hidden, obj2 enabled-togglable
assert not dlg._tf_group.isVisible(), "TF panel should be hidden initially"
assert dlg._obj2_enabled.isEnabled(), "obj2 should be enabled normally"

# Switch to TF mode
for i in range(dlg._obj1_combo.count()):
    if dlg._obj1_combo.itemData(i) == _TF_OBJECTIVE_KEY:
        dlg._obj1_combo.setCurrentIndex(i)
        break

# Process events so Qt signals fire
app.processEvents()

# Show to make visibility resolve (offscreen doesn't paint but it still sets the flags)
dlg.show()
app.processEvents()

assert dlg._tf_group.isVisible(), "TF panel should be visible in TF mode"
assert not dlg._obj2_enabled.isEnabled(), "obj2 should be disabled in TF mode"
assert not dlg._constraints_group.isEnabled(), "constraints should be disabled in TF mode"
assert dlg._tf_base_case_combo.count() == 8, f"Expected 8 DYC cases, got {dlg._tf_base_case_combo.count()}"

# Verify wave dir got wired from project
assert dlg._tf_wave_dir_edit.text() == proj.snap_wave_dir

print("OK: TF mode toggles UI state correctly")
print("  TF group visible:", dlg._tf_group.isVisible())
print("  Base case count:", dlg._tf_base_case_combo.count())
print("  obj2 enabled:", dlg._obj2_enabled.isEnabled())
print("  constraints enabled:", dlg._constraints_group.isEnabled())
print("  wave dir:", dlg._tf_wave_dir_edit.text())

# Test build_config in TF mode
config = dlg._build_config([])
assert config.objective_key == _TF_OBJECTIVE_KEY
assert config.constraints == {}
assert config.criteria is None, "criteria should be None in TF mode"
print("  config.objective_key:", config.objective_key)
print("  config.constraints:", config.constraints)
print("  config.criteria:", config.criteria)

# Switch back to normal mode
dlg._obj1_combo.setCurrentIndex(0)  # max_drift
app.processEvents()
assert not dlg._tf_group.isVisible()
assert dlg._obj2_enabled.isEnabled()
assert dlg._constraints_group.isEnabled()
print("  After switching back to normal mode: OK")
