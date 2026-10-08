"""Unit tests for AutoGNOM shape_tight_extent scoring (no ATSAS)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from autosaxs.core.shape_score import (
    ALPHA_GRID_N,
    DMAX_GRID_N,
    PR_EXTENT_HI,
    alpha_log10_grid,
    extent_candidate_grid,
    pick_best_by_score,
    q_min_first_positive_nm,
    shape_features_from_distribution,
    shape_tight_extent_score,
    shape_tight_extent_score_dr,
)


def test_shannon_grid_bounds_and_alpha():
    grid, meta = extent_candidate_grid(q_min_nm=0.1, rg_guinier_nm=3.0, n=DMAX_GRID_N)
    assert len(grid) == DMAX_GRID_N
    assert grid[0] == pytest.approx(6.0)  # 2 * Rg
    assert grid[-1] == pytest.approx(math.pi / 0.1)
    assert meta["fallback"] is False
    a = alpha_log10_grid()
    assert len(a) == ALPHA_GRID_N
    assert a[0] == pytest.approx(-1.0)
    assert a[-1] == pytest.approx(2.5)


def test_shannon_fallback_when_up_below_lo():
    # Large Rg → 2Rg > π/q_min
    grid, meta = extent_candidate_grid(q_min_nm=1.0, rg_guinier_nm=5.0)
    assert meta["fallback"] is True
    assert grid[-1] > grid[0]


def test_q_min_first_positive():
    q = np.array([np.nan, -0.1, 0.0, 0.05, 0.1])
    assert q_min_first_positive_nm(q) == pytest.approx(0.05)


def test_shape_tight_penalizes_negatives_and_oversize():
    r = np.linspace(0.0, 10.0, 81)
    # Soft early peak, soft taper to zero
    p_nice = np.exp(-((r - 2.5) / 1.2) ** 2) * np.clip(1.0 - r / 10.0, 0.0, 1.0)
    nice = shape_features_from_distribution(r, p_nice, extent_nm=10.0)
    nice["d_over_rg"] = 10.0 / 2.8  # ~3.57 in band
    nice["chi2_med"] = 1.2

    p_neg = p_nice.copy()
    p_neg[20:30] = -0.2
    bad_neg = shape_features_from_distribution(r, p_neg, extent_nm=10.0)
    bad_neg["d_over_rg"] = nice["d_over_rg"]
    bad_neg["chi2_med"] = 1.2

    over = dict(nice)
    over["d_over_rg"] = 8.0  # above PR_EXTENT_HI=5

    assert shape_tight_extent_score(nice) > shape_tight_extent_score(bad_neg)
    assert shape_tight_extent_score(nice) > shape_tight_extent_score(over)
    assert PR_EXTENT_HI == 5.0


def test_dr_extent_band_allows_compact_sphere_scale():
    r = np.linspace(0.0, 5.0, 51)
    d = np.exp(-((r - 2.0) / 0.8) ** 2) * np.clip(1.0 - r / 5.0, 0.0, 1.0)
    feats = shape_features_from_distribution(r, d, extent_nm=5.0)
    feats["d_over_rg"] = 5.0 / 3.0  # ~1.67 — ok for DR band [1,4], too small for PR [3.2,5]
    feats["chi2_med"] = 1.0
    s_dr = shape_tight_extent_score_dr(feats)
    s_pr = shape_tight_extent_score(feats)
    assert math.isfinite(s_dr)
    # PR extent undersize penalty should make PR score worse than DR for this ρ
    assert s_dr > s_pr


def test_pick_best_by_score():
    trials = [
        {"ok": True, "dmax_nm": 8.0, "neg_frac": 0.0, "neg_mass": 0.0, "tail_mass": 0.03,
         "tail_ratio": 0.02, "p_end_norm": 0.01, "smoothness": 0.001, "n_sign_runs": 0,
         "n_modes": 1, "chi2_med": 1.0, "d_over_rg": 3.5},
        {"ok": True, "dmax_nm": 12.0, "neg_frac": 0.2, "neg_mass": 0.1, "tail_mass": 0.1,
         "tail_ratio": 0.2, "p_end_norm": 0.2, "smoothness": 0.01, "n_sign_runs": 5,
         "n_modes": 3, "chi2_med": 50.0, "d_over_rg": 3.5},
        {"ok": False, "dmax_nm": 9.0},
    ]
    best = pick_best_by_score(trials, shape_tight_extent_score)
    assert best is not None
    assert best["dmax_nm"] == 8.0
    assert "score" in best
