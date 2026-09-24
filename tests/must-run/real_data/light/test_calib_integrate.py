"""Light real-data: calibrate → integrate vs reference .chi."""

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


def test_calib_integration_validation():
    H._reset_validation_plots_dir()
    H._reset_validation_plots_subdir("integrated")
    old_int = H._read_metrics_csv(H.METRICS_INTEGRATED_CSV)
    old_int_chi2 = H._read_metrics_csv(H.METRICS_INTEGRATED_CHI2_CSV)
    H.run_calibration_integration_subtraction(run_subtraction=False)
    results_int, metrics_rows, chi2_rows = H.compare_and_plot_integrated()
    assert len(results_int) > 0, "No pipeline outputs could be matched to reference .chi files"
    ok = H._compare_metrics(old_int, metrics_rows, label="Integrated")
    ok_chi2 = H._compare_metrics(old_int_chi2, chi2_rows, label="Integrated chi2")
    H._write_metrics_csv(H.METRICS_INTEGRATED_CSV, metrics_rows)
    H._write_metrics_csv(H.METRICS_INTEGRATED_CHI2_CSV, chi2_rows)
    with open(H.SUCCESS_TXT, "w") as f:
        f.write("SUCCESS\n" if ok and ok_chi2 else "FAIL\n")
    print(f"VALIDATION: {'SUCCESS' if ok and ok_chi2 else 'FAIL'} (integrated)")
    assert ok and ok_chi2, "Integrated metric regression detected (>1% increase)."
