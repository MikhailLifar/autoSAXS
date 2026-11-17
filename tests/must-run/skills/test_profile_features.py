"""Portable unit tests for profile_features + passport field helpers (synthetic curves)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPOS = Path(__file__).resolve().parents[3]
_SRC = _REPOS / "src"
for _p in (_SRC, _REPOS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from autosaxs.core.profile_features import (
    assess_subtraction_negativity,
    classify_p0_solvent,
    detect_iq_peaks,
    format_p0_passport_value,
    p0_from_distribution,
    p0_solvent_from_distribution,
    powerlaw_exponent,
)
from autosaxs.core.utils import subtraction_correctness
from autosaxs.skill.fit_distances.quality_io import _merge_p0_solvent_into_quality
from autosaxs.core.gnom_quality import apply_sizes_extended_quality


def test_assess_subtraction_negativity_i_plus_sigma():
    q = np.array([0.1, 0.5, 1.0, 2.0, 3.0], dtype=float)
    I = np.array([10.0, 5.0, 1.0, -0.5, -2.0], dtype=float)
    sigma = np.array([0.1, 0.1, 0.1, 0.2, 0.1], dtype=float)
    out = assess_subtraction_negativity(I, sigma, q)
    assert out["correctness"] == "over-subtracted"
    # points 3 and 4: -0.5+0.2=-0.3<0; -2+0.1<0 → frac 2/5
    assert out["frac_neg"] == pytest.approx(0.4)
    assert out["q_first_neg_nm"] == pytest.approx(2.0)
    assert subtraction_correctness(I, sigma) == "over-subtracted"


def test_assess_subtraction_negativity_clean_and_no_sigma():
    q = np.linspace(0.1, 3.0, 20)
    I = np.exp(-q)
    sigma = 0.01 * np.ones_like(I)
    out = assess_subtraction_negativity(I, sigma, q)
    assert out["correctness"] == "correct"
    assert out["frac_neg"] == 0.0
    assert out["q_first_neg_nm"] is None
    # Without sigma: legacy correctness stays correct even if I dips negative.
    I_neg = I.copy()
    I_neg[-1] = -1.0
    assert subtraction_correctness(I_neg, None) == "correct"
    out2 = assess_subtraction_negativity(I_neg, None, q)
    assert out2["correctness"] == "correct"
    assert out2["frac_neg"] > 0
    assert out2["q_first_neg_nm"] == pytest.approx(float(q[-1]))


def test_powerlaw_exponent_porod_like():
    q = np.linspace(1.0, 4.0, 40)
    I = 3.0 * q ** (-4.0)
    alpha = powerlaw_exponent(q, I, high_q_fraction=1.0)
    assert alpha is not None
    assert alpha == pytest.approx(-4.0, abs=0.05)


def test_powerlaw_exponent_rod_like():
    q = np.linspace(0.5, 3.0, 50)
    I = 2.0 * q ** (-1.0)
    alpha = powerlaw_exponent(q, I, q_min_nm=0.5, q_max_nm=3.0)
    assert alpha is not None
    assert alpha == pytest.approx(-1.0, abs=0.05)


def test_detect_iq_peaks_synthetic_bragg():
    q = np.linspace(0.2, 3.0, 400)
    I = 0.2 + 0.02 * q
    # Spinozzi-like pins near 1.5 nm^-1
    I = I + 1.5 * np.exp(-0.5 * ((q - 1.50) / 0.03) ** 2)
    I = I + 0.8 * np.exp(-0.5 * ((q - 2.10) / 0.03) ** 2)
    peaks = detect_iq_peaks(q, I, min_prominence_frac=0.05, min_distance_points=5)
    assert len(peaks) >= 2
    qs = [p["q_nm"] for p in peaks]
    assert any(abs(x - 1.50) < 0.05 for x in qs)
    assert any(abs(x - 2.10) < 0.05 for x in qs)


def test_p0_solvent_classification_jacques():
    r = np.linspace(0.0, 10.0, 51)
    # Positive P(0) → under-subtracted
    p_under = 0.2 + np.exp(-((r - 3.0) / 1.5) ** 2)
    under = p0_solvent_from_distribution(r, p_under)
    assert under["p0"] is not None and under["p0"] > 0
    assert under["p0_classification"] == "under_subtracted"
    # Negative P(0) → over-subtracted
    p_over = -0.25 + np.exp(-((r - 3.0) / 1.5) ** 2)
    over = p0_solvent_from_distribution(r, p_over)
    assert over["p0"] is not None and over["p0"] < 0
    assert over["p0_classification"] == "over_subtracted"
    # Near zero at r=0
    p_ok = 0.0 * r + np.exp(-((r - 3.0) / 1.5) ** 2)
    p_ok[0] = 0.0
    ok = p0_solvent_from_distribution(r, p_ok)
    assert ok["p0_classification"] == "ok"


def test_p0_from_distribution_extrapolates():
    r = np.array([0.5, 1.0, 1.5], dtype=float)
    p = np.array([1.0, 2.0, 3.0], dtype=float)  # extrapolates to 0.0 at r=0
    assert p0_from_distribution(r, p) == pytest.approx(0.0)
    assert classify_p0_solvent(None)["p0_classification"] == "unknown"


def test_format_p0_passport_value_single_row_rule():
    assert format_p0_passport_value(0.012, "ok") == "0.012"
    assert format_p0_passport_value(0.012, "unknown") == "0.012"
    assert format_p0_passport_value(0.012, None) == "0.012"
    assert format_p0_passport_value(0.012, "over_subtracted") == "0.012 (over_subtracted)"
    assert format_p0_passport_value(0.012, "under_subtracted") == "0.012 (under_subtracted)"
    assert format_p0_passport_value(None, "over_subtracted") is None
    assert (
        format_p0_passport_value(0.012, "over_subtracted", number_str="0.01")
        == "0.01 (over_subtracted)"
    )


def test_merge_p0_into_fit_distances_quality():
    quality = {"user_tips": [], "overall_status": "ACCEPTABLE"}
    out = _merge_p0_solvent_into_quality(
        quality,
        p0=0.12,
        p0_classification="under_subtracted",
        force_zero_rmin_off_out_path="/tmp/gnom_force_zero_rmin_off.out",
    )
    assert out["p0"] == pytest.approx(0.12)
    assert out["p0_classification"] == "under_subtracted"
    assert "under-subtracted" in " ".join(out["user_tips"]).lower()


def test_apply_sizes_extended_quality_powerlaw_and_p0():
    base = {
        "sizes_quality_class": "high_quality",
        "overall_status": "HIGH QUALITY",
        "user_tips": [],
        "quality_rationale": [],
        "modality_class": "unimodal_polydisperse",
        "total_estimate": 0.8,
        "pdi": 0.1,
        "d_avg_nm": 5.0,
        "d_std_nm": 0.5,
    }
    ens = {
        "stability_class": "stable",
        "p0": -0.15,
        "p0_classification": "over_subtracted",
        "force_zero_rmin_off_out_path": "ensemble/gnom_force_zero_rmin_off.out",
        "rmax_validation": None,
    }
    out = apply_sizes_extended_quality(
        base,
        parametric={},
        ensemble_info=ens,
        shape="spheres",
        powerlaw_exponent=-1.1,
    )
    assert out["p0_classification"] == "over_subtracted"
    assert out["powerlaw_exponent"] == pytest.approx(-1.1)
    tips = " ".join(out["user_tips"]).lower()
    assert "over-subtracted" in tips
    assert "power-law" in tips or "−1" in tips or "-1" in tips
