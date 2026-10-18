"""Unit tests for NumPy Tikhonov P(r) solver (no ATSAS)."""

from __future__ import annotations

import numpy as np
import pytest

from autosaxs.core.tikhonov_pr import (
    calibrate_alpha_scale,
    nr_for_dmax,
    rg_i0_from_pr,
    solve_tikhonov,
)


def _synthetic_sphere_pr(*, dmax: float = 5.0, nr: int = 101):
    """Approximate p(r) for a solid sphere of diameter dmax (peak near mid-r)."""
    r = np.linspace(0.0, dmax, nr)
    # Classic sphere pair-distance shape ~ r² (d−r)² (2d+r) for r<d
    d = dmax
    p = np.zeros_like(r)
    m = (r > 0) & (r < d)
    x = r[m]
    p[m] = (x**2) * ((d - x) ** 2) * (2.0 * d + x)
    p[0] = 0.0
    p[-1] = 0.0
    return r, p


def _forward_iq(q, r, p):
    from autosaxs.core.tikhonov_pr import build_kernel

    K = build_kernel(q, r, kind="4pi_sinc", quad="trapz")
    return K @ p


def test_solve_tikhonov_recovers_smooth_pr_shape():
    dmax = 5.0
    r_true, p_true = _synthetic_sphere_pr(dmax=dmax, nr=81)
    q = np.linspace(0.15, 3.0, 120)
    I = _forward_iq(q, r_true, p_true)
    sigma = 0.01 * np.maximum(I, 1e-6) + 1e-4
    # Mild noise — solver recovers the known shape family
    rng = np.random.default_rng(0)
    I_noisy = I + rng.normal(0.0, 0.25, size=I.shape) * sigma

    sol = solve_tikhonov(
        q,
        I_noisy,
        sigma,
        dmax=dmax,
        alpha=0.1,
        nr=81,
        alpha_scale=1.0,
    )
    assert sol.p.shape == r_true.shape
    assert sol.p[0] == pytest.approx(0.0, abs=1e-10)
    assert sol.p[-1] == pytest.approx(0.0, abs=1e-10)
    a = sol.p - sol.p.mean()
    b = p_true - p_true.mean()
    corr = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-30))
    assert corr > 0.90
    assert np.isfinite(sol.chi2_med) and sol.chi2_med > 0
    assert np.isfinite(sol.rg_pr) and sol.rg_pr > 0


def test_calibrate_alpha_scale_finds_near_true_scale():
    dmax = 5.0
    r_true, p_true = _synthetic_sphere_pr(dmax=dmax, nr=61)
    q = np.linspace(0.2, 2.5, 80)
    I = _forward_iq(q, r_true, p_true)
    sigma = 0.03 * np.maximum(I, 1e-6) + 1e-4
    true_scale = 0.1
    # Reference "GNOM" p is NumPy at alpha=10 with scale=true_scale
    ref = solve_tikhonov(q, I, sigma, dmax=dmax, alpha=10.0, nr=61, alpha_scale=true_scale)
    best_s, corr = calibrate_alpha_scale(
        q,
        I,
        sigma,
        dmax=dmax,
        alpha=10.0,
        r_ref=ref.r,
        p_ref=ref.p,
        nr=61,
        scales=np.logspace(-3, 1, 25),
    )
    assert corr > 0.999
    assert best_s == pytest.approx(true_scale, rel=0.2)


def test_nr_for_dmax_scales_and_clamps():
    assert nr_for_dmax(10.0, nr_seed=200, dmax_seed_nm=5.0) == 400
    assert nr_for_dmax(0.1, nr_seed=200, dmax_seed_nm=5.0) == 51
    assert nr_for_dmax(100.0, nr_seed=200, dmax_seed_nm=5.0) == 501


def test_rg_i0_from_pr_sphere_like():
    r, p = _synthetic_sphere_pr(dmax=6.0, nr=121)
    rg, i0 = rg_i0_from_pr(r, p)
    assert np.isfinite(rg) and rg > 0
    assert np.isfinite(i0) and i0 > 0
    # Sphere diameter D=6 → R=3 → Rg = R√(3/5) ≈ 2.32
    assert 1.5 < rg < 3.0
