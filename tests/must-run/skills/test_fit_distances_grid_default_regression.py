"""Regression: auto fit_distances must use ATSAS Shannon×α grid quality (remote-main)."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from autosaxs.core.gnom import parse_gnom_out
from autosaxs.core.gnom_quality import chi2_from_iq_table
from autosaxs.skill.fit_distances import fit_distances

_FIXTURE_CANDIDATES = [
    Path("/cursor/stores/self/artifacts/conference-hype-v3/plant-pack/subtracted/sub_ihs27_sample.dat"),
    Path("/cursor/stores/bc-2b169515-f772-4921-ac0c-85787ab57ccb/artifacts/conference-hype-v3/plant-pack/subtracted/sub_ihs27_sample.dat"),
]


def _fixture() -> Path:
    for p in _FIXTURE_CANDIDATES:
        if p.is_file():
            return p
    pytest.skip("ihs27 subtracted fixture not available in this environment")


@pytest.mark.atsas
def test_auto_path_matches_main_grid_i0_scale(tmp_path: Path) -> None:
    """Guards the H32/Fast AutoGNOM failure mode (I_fit << I_exp, collapsed I(0))."""
    dat = _fixture()
    out = fit_distances(str(dat), output_dir=str(tmp_path / "d"), minimal=True, use_cache=False)
    # Locate gnom_best.out
    outs = list(Path(tmp_path).rglob("gnom_best.out"))
    assert outs, out
    parsed = parse_gnom_out(outs[0].read_text(errors="replace"))
    iq = parsed["iq_table"]
    assert iq is not None
    _q, i_exp, _sig, i_fit = iq
    ratio = float(np.nanmax(i_fit) / max(float(np.nanmax(i_exp)), 1e-30))
    i0 = float(parsed.get("real_space_i0") or 0.0)
    chi2 = float(chi2_from_iq_table(iq))
    # Remote-main class: I(0)~259, ratio~1, χ²~1.4 on this fixture (first=auto/Guinier).
    assert ratio > 0.5, f"GNOM I(q) collapsed vs exp (ratio={ratio})"
    assert i0 > 50.0, f"collapsed real-space I(0)={i0}"
    assert chi2 < 50.0, f"unexpected χ²={chi2}"
