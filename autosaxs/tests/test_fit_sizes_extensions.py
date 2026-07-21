"""Tests for fit_sizes preliminary-hint extensions."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from autosaxs.core.gnom import distribution_arrays, parse_gnom_out
from autosaxs.core.gnom_quality import analyze_rmax_validation, classify_stability
from autosaxs.core.utils import lognormal_pdf
from autosaxs.skill.fit_sizes.parametric import classify_dr_parametric, mixture_dist_hint
from autosaxs.skill.fit_sizes.quality_io import normalize_fit_sizes_single_sample
from autosaxs.skill.fit_sizes.runners import _run_gnom_once


def test_lognormal_pdf_peak():
    r = np.linspace(0.1, 10, 200)
    y = lognormal_pdf(r, mu=2.0, sigma=0.3)
    assert np.nanmax(y) > 0
    assert y[0] >= 0


def test_classify_dr_parametric_unimodal():
    r = np.linspace(0.5, 8, 80)
    d = np.exp(-0.5 * ((r - 3.0) / 0.8) ** 2)
    out = classify_dr_parametric(r, d, modality_class="unimodal_polydisperse", dr_n_peaks=1)
    assert out["parametric_family"] in ("gauss", "lognormal", "schultz")
    assert out["n_components_suggested"] == 1
    assert mixture_dist_hint(out["parametric_family"]) in ("Gauss", "Schultz")


def test_classify_stability_stable():
    rows = [
        {"ok": True, "peak_r_nm": 3.0, "pdi": 0.2, "total_estimate": 0.7},
        {"ok": True, "peak_r_nm": 3.1, "pdi": 0.21, "total_estimate": 0.72},
    ]
    assert classify_stability(ensemble_rows=rows, rmax_validation={"severity": "ok"}) == "stable"


def test_classify_stability_unstable_on_pathology():
    rows = [
        {"ok": True, "peak_r_nm": 3.0, "pdi": 0.2},
        {"ok": True, "peak_r_nm": 5.0, "pdi": 0.5},
    ]
    val = analyze_rmax_validation(
        best_parsed={"distribution": None},
        ensemble_rows=rows,
        force_zero_off_parsed=None,
        rmax_ref_nm=4.0,
    )
    val["severity"] = "failed"
    assert classify_stability(ensemble_rows=rows, rmax_validation=val) == "unstable"


def test_distribution_arrays_with_error_column():
    fixture = (
        "/home/mikl/KurchatovCoop/test_liveview/fit_distances/"
        "ihs27_95.9_sample/datgnom_rg_1.6861.out"
    )
    parsed = parse_gnom_out(fixture)
    arrays = distribution_arrays(parsed.get("distribution"))
    assert arrays is not None
    r, d, err = arrays
    assert len(r) >= 8
    assert err is not None
    assert np.any(np.isfinite(err))


@patch("autosaxs.skill.fit_sizes.runners.subprocess.run")
def test_run_gnom_once_force_zero_flags(mock_run: MagicMock, tmp_path):
    mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
    dat = tmp_path / "sample_atsas.dat"
    dat.write_text("0.1 1 0.1\n")
    out = tmp_path / "test.out"
    out.write_text("ok")
    ok, rc, stderr, text = _run_gnom_once(
        atsas_dat_path=str(dat),
        output_dir=str(tmp_path),
        system=1,
        rmin_nm=None,
        rmax_nm=5.0,
        rad56_nm=None,
        first=1,
        last=None,
        alpha=None,
        nr=None,
        out_path=str(out),
        force_zero_rmax="N",
    )
    assert ok
    assert rc == 0
    cmd = mock_run.call_args[0][0]
    assert "--force-zero-rmax=N" in cmd
    assert "--force-zero-rmin=Y" in cmd


def test_normalize_fit_sizes_single_sample_unwraps_scalars():
    raw = {
        "best_gnom_out_path": "/tmp/out.out",
        "n_components_suggested": [3],
        "d_avg_nm": [2.47],
        "dr_peak_positions_nm": ["1.5", "3.0"],
        "gnom_out_paths": ["/tmp/out.out"],
        "quality_rationale": ["low total estimate"],
    }
    out = normalize_fit_sizes_single_sample(raw)
    assert out["n_components_suggested"] == 3
    assert out["d_avg_nm"] == 2.47
    assert out["dr_peak_positions_nm"] == ["1.5", "3.0"]
    assert out["gnom_out_paths"] == ["/tmp/out.out"]
