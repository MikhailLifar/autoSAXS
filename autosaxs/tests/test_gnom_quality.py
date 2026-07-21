"""Tests for GNOM post-hoc quality metrics."""

from __future__ import annotations

import math

import numpy as np
import pytest

from autosaxs.core.gnom import parse_gnom_out
from autosaxs.core.gnom_quality import (
    analyze_dr_quality,
    analyze_pr_quality,
    classify_shannon,
    delta_rg_pct,
    dr_distribution_moments,
    rg_from_pr,
    shannon_s_min,
    shannon_tip,
)


def test_rg_from_pr_uniform_sphere_like():
    r = np.linspace(0, 10, 101)
    p = np.exp(-((r - 5) ** 2) / 2.0)
    rg = rg_from_pr(r, p)
    assert rg is not None
    assert 3.0 < rg < 7.0


def test_shannon_classification_and_tip():
    s_min = shannon_s_min(0.35, 3.96)
    assert s_min is not None
    assert classify_shannon(s_min) == "stable"
    tip = shannon_tip(s_min, "stable")
    assert "Rigid, monodisperse proteins" in tip
    assert "Flexible coils" in tip


def test_delta_rg_pct():
    assert delta_rg_pct(1.65, 1.473) == pytest.approx(10.73, rel=0.01)


def test_analyze_pr_quality_from_parsed_out():
    out_text = "\n".join(
        [
            "Angular range: 0.3495 to 4.8413",
            "Real space range: 0.0000 to 3.9600",
            "Real space Rg: 1.473E+00",
            "Real space I(0): 2.352E+02",
            "Total Estimate = 0.565",
            "",
            "R      P(R)    Error",
            "0.0    0.0     0.0",
            "1.0    0.5     0.0",
            "2.0    1.0     0.0",
            "3.0    0.8     0.0",
            "4.0    0.2     0.0",
            "5.0    0.0     0.0",
            "6.0    0.0     0.0",
            "7.0    0.0     0.0",
        ]
    )
    parsed = parse_gnom_out(out_text)
    q = np.linspace(0.05, 5.0, 100)
    quality = analyze_pr_quality(
        parsed,
        atsas_fit_ok=True,
        rg_guinier_nm=1.65,
        q_nm=q,
        first_pt_1based=10,
        suspicious=False,
    )
    assert quality["pr_quality_class"] in ("high_quality", "acceptable", "failed")
    assert quality["total_estimate"] == pytest.approx(0.565)
    assert quality["dmax_nm"] == pytest.approx(3.96)
    assert quality["q_min_fit_nm"] == pytest.approx(0.3495)
    assert quality["shannon_s_min"] == pytest.approx((0.3495 * 3.96) / math.pi, rel=0.01)
    assert quality["shannon_tip"]


def test_dr_distribution_moments_and_modality():
    r = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    d = np.array([0.0, 0.1, 0.5, 1.0, 0.5, 0.1, 0.0, 0.0])
    moments = dr_distribution_moments(r, d)
    assert moments["d_avg_nm"] is not None
    assert moments["pdi"] is not None
    out_text = "\n".join(
        [
            "Total Estimate = 0.75",
            "",
            "R      D(R)    Err",
            "0.0    0.0     0.0",
            "1.0    0.1     0.0",
            "2.0    0.5     0.0",
            "3.0    1.0     0.0",
            "4.0    0.5     0.0",
            "5.0    0.1     0.0",
            "6.0    0.0     0.0",
            "7.0    0.0     0.0",
        ]
    )
    quality = analyze_dr_quality(
        parse_gnom_out(out_text),
        atsas_fit_ok=True,
        rg_guinier_nm=2.0,
        shape="spheres",
        neg_frac=0.0,
    )
    assert quality["sizes_quality_class"] == "high_quality"
    assert quality["modality_class"] in ("monodisperse", "unimodal_polydisperse")
    assert quality["d_avg_nm"] is not None
    assert "shannon_s_min" in quality
    assert "shannon_class" in quality
    assert "shannon_tip" in quality


def test_analyze_dr_quality_shannon_with_q_nm():
    out_text = "\n".join(
        [
            "Angular range: 0.3495 to 4.8413",
            "Real space range: 0.0000 to 3.9600",
            "Total Estimate = 0.700",
            "",
            "R      P(R)    Error",
            "0.0    0.0     0.0",
            "1.0    0.5     0.0",
            "2.0    1.0     0.0",
            "3.0    0.5     0.0",
            "4.0    0.0     0.0",
        ]
    )
    q = np.linspace(0.05, 5.0, 100)
    quality = analyze_dr_quality(
        parse_gnom_out(out_text),
        atsas_fit_ok=True,
        rg_guinier_nm=None,
        shape="spheres",
        neg_frac=0.0,
        q_nm=q,
        first_pt_1based=1,
    )
    assert quality["dmax_nm"] == pytest.approx(3.96)
    assert quality["shannon_s_min"] is not None
    assert quality["shannon_class"] != "unknown"
