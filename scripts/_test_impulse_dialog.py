"""UI smoke test: ImpulseResponseDialog end-to-end case creation flow."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

import sys
from pathlib import Path
sys.path.insert(0, r"C:/Users/keita/App/ADC/snap-controller")

import shutil
import tempfile

from PySide6.QtWidgets import QApplication
from app.models import AnalysisCase, Project
from app.ui.impulse_response_dialog import ImpulseResponseDialog


S8I = r"C:/Users/keita/App/ADC/snap-controller/example_model/example_3D/example_3D.s8i"

app = QApplication.instance() or QApplication(sys.argv)

tmpdir = Path(tempfile.mkdtemp(prefix="impulse_ui_"))
try:
    # ベースケース
    local_s8i = tmpdir / "example_3D.s8i"
    shutil.copy(S8I, local_s8i)

    proj = Project(name="Test")
    proj.snap_wave_dir = str(tmpdir / "wave")
    case = AnalysisCase(name="base", model_path=str(local_s8i))
    proj.cases.append(case)

    dlg = ImpulseResponseDialog(project=proj, base_case=case)
    app.processEvents()

    # パラメータを設定
    assert dlg._base_case_combo.count() == 1
    assert dlg._dyc_case_combo.count() > 0
    dlg._amax_spin.setValue(500.0)
    dlg._num_points_spin.setValue(4096)
    dlg._impulse_index_spin.setValue(5)

    # _build_spec の検証
    spec = dlg._build_spec()
    assert spec.amax == 500.0
    assert spec.num_points == 4096
    assert spec.impulse_index == 5
    assert spec.snap_wave_dir == proj.snap_wave_dir
    print("OK: _build_spec() 生成")
    print("  target_case_no:", spec.target_case_no)
    print("  amax:", spec.amax)
    print("  num_points:", spec.num_points)

    # _on_accept を発火して case が生成されるか
    dlg._on_accept()
    assert dlg.created_case is not None
    new_case = dlg.created_case
    print("OK: ケース生成")
    print("  name:", new_case.name)
    print("  model_path:", new_case.model_path)
    assert Path(new_case.model_path).exists()
    # wave ファイル
    wv_files = list(Path(proj.snap_wave_dir).glob("*.wv"))
    assert len(wv_files) == 1
    print("  wave file:", wv_files[0].name)

    # s8i に反映されているか
    from app.models.s8i_parser import parse_s8i
    model = parse_s8i(new_case.model_path)
    running = [c for c in model.dyc_cases if c.is_run]
    assert len(running) == 1, f"1 ケースだけが run_flag=1 のはず: {len(running)}"
    print("  running DYC:", running[0].case_no, running[0].name)
    print("  wave name in DYC:", running[0].values[19])

    print("\n✓ ImpulseResponseDialog 統合テスト完了")
finally:
    shutil.rmtree(tmpdir, ignore_errors=True)
