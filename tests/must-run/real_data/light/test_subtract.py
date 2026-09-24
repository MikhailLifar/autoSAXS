"""Light real-data: calibrate → integrate → subtract vs reference sub_*.dat."""

from __future__ import annotations


import importlib.util
from pathlib import Path as _P

def _load_H():
    _hp = _P(__file__).resolve().parents[1] / "_helpers.py"
    _spec = importlib.util.spec_from_file_location("autosaxs_real_data_helpers", _hp)
    assert _spec and _spec.loader
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod

H = _load_H()


def test_calib_integration_subtraction_validation():
    H._reset_validation_plots_dir()
    H._reset_validation_plots_subdir("subtracted")
    old_sub = H._read_metrics_csv(H.METRICS_SUBTRACTED_CSV)
    old_sub_chi2 = H._read_metrics_csv(H.METRICS_SUBTRACTED_CHI2_CSV)
    H.run_calibration_integration_subtraction()
    results_sub, metrics_rows, chi2_rows = H.compare_and_plot_subtracted()
    assert len(results_sub) > 0, "No pipeline subtracted outputs could be matched to reference sub_*.dat"
    ok = H._compare_metrics(old_sub, metrics_rows, label="Subtracted")
    ok_chi2 = H._compare_metrics(old_sub_chi2, chi2_rows, label="Subtracted chi2")
    H._write_metrics_csv(H.METRICS_SUBTRACTED_CSV, metrics_rows)
    H._write_metrics_csv(H.METRICS_SUBTRACTED_CHI2_CSV, chi2_rows)
    with open(H.SUCCESS_TXT, "w") as f:
        f.write("SUCCESS\n" if ok and ok_chi2 else "FAIL\n")
    print(f"VALIDATION: {'SUCCESS' if ok and ok_chi2 else 'FAIL'} (subtracted)")
    assert ok and ok_chi2, "Subtracted metric regression detected (>1% increase)."
