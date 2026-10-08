"""Light real-data smoke for ``process_polydisperse`` when poly fixtures exist."""

from __future__ import annotations

import glob
import importlib.util
import os
from pathlib import Path as _P

import pytest


def _load_H():
    _hp = _P(__file__).resolve().parents[1] / "_helpers.py"
    _spec = importlib.util.spec_from_file_location("autosaxs_real_data_helpers", _hp)
    assert _spec and _spec.loader
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


H = _load_H()


def _poly_subtracted_profiles() -> list[str]:
    if not os.path.isdir(H.POLY_SUBTRACTED_DIR):
        return []
    return sorted(
        p
        for p in glob.glob(os.path.join(H.POLY_SUBTRACTED_DIR, "sub_*.dat"))
        if "buffer" not in os.path.basename(p).lower()
    )


@pytest.mark.skipif(
    not os.path.isdir(getattr(H, "REFERENCE_POLY_DIR", "")),
    reason="validation/reference_poly missing; run setup_validation_data.py with PROTOCOL_POLY",
)
def test_process_polydisperse_smoke_on_subtracted():
    """
    End-to-end meta-skill on one regenerated poly subtracted curve (no MIXTURE).

    Relies on ``run_polydisperse_pipeline`` having produced subtracted .dat under
    ``validation/poly/subtracted/``, or builds that stage first.
    """
    from autosaxs.skill import process_polydisperse

    profiles = _poly_subtracted_profiles()
    if not profiles:
        # Build calib→integrate→subtract→… so meta-skill has a .dat input.
        run_state = H.run_polydisperse_pipeline()
        profiles = sorted(run_state["sub_by_key"].values())
    assert profiles, "No poly subtracted profiles available"

    profile = profiles[0]
    out_dir = os.path.join(H.POLY_DIR, "process_polydisperse_smoke")
    if os.path.isdir(out_dir):
        H._rmtree_force(out_dir)

    result = process_polydisperse(profile, out_dir, run_mixture=False, use_cache=False)

    assert result.get("basename")
    assert result.get("model_mixture_ran") is False
    assert "run_mixture=False" in str(result.get("model_mixture_skip_reason") or "")
    sizes = result.get("fit_sizes") or {}
    best = sizes.get("best_gnom_out_path")
    if isinstance(best, list):
        best = best[0] if best else None
    assert best and os.path.isfile(str(best)), "fit_sizes must produce a GNOM .out"
    report = result.get("report_pdf_path")
    if isinstance(report, list):
        report = report[0] if report else None
    assert report and os.path.isfile(str(report)), "report_individual PDF expected"
