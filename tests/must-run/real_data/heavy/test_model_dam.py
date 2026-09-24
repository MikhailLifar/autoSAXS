"""Heavy real-data: model_dam smoke (ihs27). Run only after light real-data passes."""

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


def test_model_dam_smoke_ihs27():
    """Artifact smoke only — DAMMIF Rg is too stochastic for regression gates."""
    if not os.path.isdir(H.REFERENCE_MONO_DIR):
        raise FileNotFoundError(
            f"Missing {H.REFERENCE_MONO_DIR}. "
            "Run setup_validation_data.py with PROTOCOL_MONO_2D set."
        )

    state = H.run_model_dam_heavy()
    dam_out = state["dam_out"]
    dam_yml = H._as_scalar(dam_out.get("output_subdir"))
    fits_path = os.path.join(str(dam_yml or ""), "dammif_fits.yml") if dam_yml else ""
    best_cif = H._as_scalar(dam_out.get("best_cif_path"))
    assert best_cif and os.path.lexists(str(best_cif)), f"missing best.cif ({best_cif})"
    assert os.path.isfile(fits_path), f"model_dam: missing {fits_path}"
    print(f"VALIDATION MODEL_DAM: SUCCESS key={state['model_dam_key']} fits={fits_path}")
