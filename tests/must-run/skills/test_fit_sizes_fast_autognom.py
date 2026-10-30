"""Wiring / Stage-1 tests for fit_sizes Fast AutoGNOM (sphere H32; Stage 2 stubbed)."""

from __future__ import annotations

import os
import subprocess as _sp
import tempfile
from pathlib import Path

import numpy as np
import pytest

from autosaxs.core.shape_score import shape_tight_extent_score_dr
from autosaxs.core.tikhonov_pr import (
    DEFAULT_SPHERE_KERNEL,
    build_kernel,
    solve_tikhonov,
    sphere_form_factor_squared,
)
from autosaxs.core.utils import write_saxs
from autosaxs.skill.fit_sizes import fit_sizes
from autosaxs.skill.fit_sizes.optimize import (
    _candidate_from_numpy_dr,
    _search_shannon_alpha_dr_fast,
)


def _stub_atsas_installed(monkeypatch) -> None:
    monkeypatch.setattr(
        "autosaxs.skill.skill_wrap.ensure_atsas_installed",
        lambda: "3.2.1",
    )


def _fake_gnom_out_text(*, rmax: float = 6.0, alpha: float = 1.0) -> str:
    rows = []
    for r in np.linspace(0.0, float(rmax), 16):
        d = max(0.0, np.exp(-0.5 * ((r - 0.4 * rmax) / (0.15 * rmax + 1e-9)) ** 2))
        if r <= 0 or abs(r - rmax) < 1e-12:
            d = 0.0
        rows.append(f"{r:.6g}  {d:.6g}  0.01")
    return "\n".join(
        [
            "GNOM OUTPUT (fake polydisperse spheres)",
            "System Type: polydisperse spheres (job = 1)",
            "Total Estimate: 0.80",
            f"Real space range: 0.0 to {float(rmax):.6g}",
            f"Current ALPHA: {float(alpha):.6g}",
            "Points in real space: 99",
            "Angular range: 0.1 to 2.0",
            "   S   J EXP   ERROR   J REG   I REG",
            "0.1 1.0 0.1 1.0 1.0",
            "0.2 0.9 0.1 0.9 0.9",
            "####",
            "      R          P(R)      ERROR",
            *rows,
        ]
    )


def _write_fake_atsas_out(cmd, cwd=None) -> None:
    out_idx = cmd.index("-o")
    out_path = cmd[out_idx + 1]
    if cwd and not os.path.isabs(out_path):
        out_path = os.path.join(cwd, out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    rmax = 6.0
    alpha = 1.0
    for a in cmd:
        s = str(a)
        if s.startswith("--rmax="):
            try:
                rmax = float(s.split("=", 1)[1])
            except ValueError:
                pass
        if s.startswith("--alpha="):
            try:
                alpha = float(s.split("=", 1)[1])
            except ValueError:
                pass
    Path(out_path).write_text(_fake_gnom_out_text(rmax=rmax, alpha=alpha))


def _synthetic_sphere_iq(*, rmax: float = 6.0, nr: int = 81):
    q = np.linspace(0.1, 2.5, 90)
    r = np.linspace(0.0, rmax, nr)
    dv = np.exp(-0.5 * ((r - 0.4 * rmax) / (0.12 * rmax)) ** 2)
    dv[0] = dv[-1] = 0.0
    I = build_kernel(q, r, kind=DEFAULT_SPHERE_KERNEL) @ dv
    sigma = 0.04 * np.maximum(I, 1e-6) + 1e-5
    return q, I, sigma, r, dv


def test_sphere_form_factor_at_zero():
    assert sphere_form_factor_squared(np.asarray([0.0]))[0] == pytest.approx(1.0)


def test_candidate_from_numpy_dr_scores():
    q, I, sigma, r, dv = _synthetic_sphere_iq()
    sol = solve_tikhonov(
        q,
        I,
        sigma,
        dmax=6.0,
        alpha=1.0,
        nr=61,
        kernel=DEFAULT_SPHERE_KERNEL,
        use_h32=True,
    )
    cand = _candidate_from_numpy_dr(
        sol,
        rg_guinier_nm=1.5,
        alpha_gnom=1.0,
        rmax_nm=6.0,
        shape="spheres",
        system=1,
        rmin_nm=None,
    )
    assert cand["ok"] is True
    assert cand["engine"] == "numpy_h32_sphere"
    assert np.isfinite(cand["score"])
    assert cand["score"] == pytest.approx(shape_tight_extent_score_dr(cand))


def test_stage1_sphere_search_runs_with_stubbed_stage2(tmp_path, monkeypatch):
    """Stage 1 NumPy must succeed; Stage 2 ATSAS is stubbed to fake outs."""
    q, I, sigma, _r, _dv = _synthetic_sphere_iq()

    def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kwargs):
        assert cmd[0] == "gnom"
        assert any(str(a).startswith("--system=1") for a in cmd)
        _write_fake_atsas_out(cmd, cwd=cwd)
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("autosaxs.core.atsas_gnom.subprocess.run", _fake_run)

    # Write a dummy atsas dat path (runner only needs the path string for the stub).
    atsas = tmp_path / "syn_atsas.dat"
    write_saxs(str(atsas), q, I, sigma, {})

    best, trials, failures, meta = _search_shannon_alpha_dr_fast(
        atsas_dat_path=str(atsas),
        output_dir=str(tmp_path),
        q_nm=q,
        I=I,
        sigma=sigma,
        system=1,
        shape="spheres",
        rg_guinier_nm=1.8,
        rmin_nm=None,
        rad56_nm=None,
        first=1,
        last=None,
        nr=None,
    )
    assert best["ok"] is True
    assert best.get("alpha_map") == "identity_h32"
    assert float(best["rmax_nm"]) > 0
    assert float(best["alpha"]) > 0
    assert meta["engine"] == "fast_autognom_h32_sphere_then_gnom"
    assert meta["n_numpy_grid_ok"] > 0
    assert meta["n_gnom_evals"] >= 1
    assert any(t.get("engine") == "numpy_h32_sphere" for t in trials)


def test_fit_sizes_stage2_uses_stage1_rmax_alpha(monkeypatch):
    """Skill final ATSAS call uses Stage‑2 (Rmax, α) from Fast AutoGNOM."""
    _stub_atsas_installed(monkeypatch)
    gnom_calls = []

    def _fake_guinier(q_nm, I, sigma, atsas_dat_path=None):
        return {
            "chosen": True,
            "chosen_Rg": 1.8,
            "rg_min": 1.2,
            "rg_max": 2.4,
            "chosen_interval": (0.1, 0.4),
            "quality_class": "good",
        }

    def _fake_search(**kwargs):
        assert kwargs.get("system") == 1
        assert kwargs.get("I") is not None
        best = {
            "rmax_nm": 5.5,
            "alpha": 2.51,
            "score": -0.2,
            "ok": True,
            "engine": "fast_autognom_h32_sphere_then_gnom",
            "alpha_map": "identity_h32",
            "search_workdir": None,
        }
        return best, [best], [], {"engine": "fast_autognom_h32_sphere_then_gnom", "n_ok": 1}

    def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kwargs):
        exe = cmd[0] if cmd else ""
        assert exe == "gnom"
        gnom_calls.append(list(cmd))
        _write_fake_atsas_out(cmd, cwd=cwd)
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "autosaxs.skill.fit_sizes.optimize.run_guinier_analysis", _fake_guinier
    )
    monkeypatch.setattr(
        "autosaxs.skill.fit_sizes.sizes._search_shannon_alpha_dr", _fake_search
    )
    monkeypatch.setattr("autosaxs.core.atsas_gnom.subprocess.run", _fake_run)

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.08, 2.0, 60)
        I = np.exp(-(q**2) * 2.0) + 0.01
        sigma = 0.03 * I
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, sigma, {})
        out_dir = os.path.join(tmp, "sizes")
        fit_sizes(
            profile_path,
            output_dir=out_dir,
            first=None,
            rg_nm=None,
            minimal=True,
            use_cache=False,
        )
        assert gnom_calls, "final Stage-2 rewrite must invoke ATSAS gnom"
        final = gnom_calls[-1]
        joined = " ".join(str(a) for a in final)
        assert "--system=1" in joined
        assert "--rmax=5.5" in joined or any(str(a).startswith("--rmax=5.5") for a in final)
        assert any("2.51" in str(a) for a in final)
