"""Light real-data: monodisperse Guinier → Kratky → fit_distances (no model_dam)."""

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


def test_monodisperse_pipeline_validation():
    if not os.path.isdir(H.REFERENCE_MONO_DIR):
        raise FileNotFoundError(
            f"Missing {H.REFERENCE_MONO_DIR}. "
            "Run setup_validation_data.py with PROTOCOL_MONO_2D set."
        )

    old_g = H._read_metrics_csv(H.METRICS_MONO_GUINIER_CSV)
    old_k = H._read_metrics_csv(H.METRICS_MONO_KRATKY_CSV)
    old_r = H._read_metrics_csv(H.METRICS_MONO_FD_REFINE_CSV)

    run_state = H.run_monodisperse_skills()
    ok_cmp, failures, rows = H.compare_monodisperse_to_reference(run_state)

    ok_reg = True
    ok_reg = H._compare_metrics(old_g, rows["guinier"], label="Mono Guinier") and ok_reg
    ok_reg = H._compare_metrics(old_k, rows["kratky"], label="Mono Kratky") and ok_reg
    ok_reg = H._compare_metrics(old_r, rows["refine"], label="Mono fit_distances refine") and ok_reg

    H._write_metrics_csv(H.METRICS_MONO_GUINIER_CSV, rows["guinier"])
    H._write_metrics_csv(H.METRICS_MONO_KRATKY_CSV, rows["kratky"])
    H._write_metrics_csv(H.METRICS_MONO_FD_REFINE_CSV, rows["refine"])

    ok_all = ok_cmp and ok_reg
    with open(H.SUCCESS_MONO_TXT, "w") as f:
        f.write("SUCCESS\n" if ok_all else "FAIL\n")
    print(f"VALIDATION MONO: {'SUCCESS' if ok_all else 'FAIL'}")
    print(f"  samples smoked (DATGNOM): {len(run_state['smoke_out_by_stem'])}")
    print(f"  refine keys: {sorted(run_state['refine_out_by_key'])}")
    for msg in failures:
        print(f"  FAIL: {msg}")
    assert ok_all, "Monodisperse validation failed:\n" + "\n".join(failures)
