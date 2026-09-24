"""Light real-data: Pt_NPs polydisperse Guinier + fit_sizes vs reference_poly (no MIXTURE)."""

from __future__ import annotations

import os


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


def test_polydisperse_pipeline_validation():
    if not os.path.isdir(H.REFERENCE_POLY_DIR):
        raise FileNotFoundError(
            f"Missing {H.REFERENCE_POLY_DIR}. "
            "Run setup_validation_data.py with PROTOCOL_POLY set."
        )

    old_g = H._read_metrics_csv(H.METRICS_POLY_GUINIER_CSV)
    old_s = H._read_metrics_csv(H.METRICS_POLY_FIT_SIZES_CSV)

    run_state = H.run_polydisperse_pipeline()
    ok_cmp, failures, rows = H.compare_polydisperse_to_reference(run_state)

    ok_reg = True
    ok_reg = H._compare_metrics(old_g, rows["guinier"], label="Poly Guinier") and ok_reg
    ok_reg = H._compare_metrics(old_s, rows["fit_sizes"], label="Poly fit_sizes") and ok_reg

    H._write_metrics_csv(H.METRICS_POLY_GUINIER_CSV, rows["guinier"])
    H._write_metrics_csv(H.METRICS_POLY_FIT_SIZES_CSV, rows["fit_sizes"])

    ok_all = ok_cmp and ok_reg
    with open(H.SUCCESS_POLY_TXT, "w") as f:
        f.write("SUCCESS\n" if ok_all else "FAIL\n")
    print(f"VALIDATION POLY: {'SUCCESS' if ok_all else 'FAIL'}")
    print(f"  keys: {sorted(run_state['sizes_out_by_key'])}")
    for msg in failures:
        print(f"  FAIL: {msg}")
    assert ok_all, "Polydisperse validation failed:\n" + "\n".join(failures)
