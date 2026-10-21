"""Wiring / Stage-1 tests for Fast AutoGNOM H32 search (no ATSAS required)."""

from __future__ import annotations

import os
import subprocess as _sp
import tempfile
from pathlib import Path

import numpy as np
import pytest

from autosaxs.core.shape_score import shape_tight_extent_score
from autosaxs.core.tikhonov_pr import build_kernel, solve_tikhonov
from autosaxs.core.utils import write_saxs
from autosaxs.skill.fit_distances import fit_distances
from autosaxs.skill.fit_distances.optimize import (
    _candidate_from_numpy_solve,
    _search_shannon_alpha_pr,
)


def _stub_atsas_installed(monkeypatch) -> None:
    monkeypatch.setattr(
        "autosaxs.skill.skill_wrap.ensure_atsas_installed",
        lambda: "3.2.1",
    )


def _fake_datgnom_out_text(*, rmax: float = 8.0) -> str:
    rows = []
    for r in np.linspace(0.0, float(rmax), 16):
        p = max(0.0, 1.0 - (r / max(rmax, 1e-9)) ** 2)
        rows.append(f"{r:.6g}  {p:.6g}  0.01")
    return "\n".join(
        [
            "GNOM OUTPUT (fake)",
            "Total Estimate: 0.85",
            f"Real space range: 0.0 to {float(rmax):.6g}",
            "Current ALPHA: 1.0",
            "Points in real space: 99",
            "Angular range: 0.1 to 2.0",
            "Real space Rg: 2.0",
            "Real space I(0): 10.0",
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
    rmax = 8.0
    for a in cmd:
        s = str(a)
        if s.startswith("--rmax="):
            try:
                rmax = float(s.split("=", 1)[1])
            except ValueError:
                pass
    Path(out_path).write_text(_fake_datgnom_out_text(rmax=rmax))


def test_candidate_from_numpy_solve_scores():
    q = np.linspace(0.2, 2.0, 60)
    r = np.linspace(0.0, 5.0, 51)
    p = np.exp(-((r - 2.0) / 0.8) ** 2)
    p[0] = 0.0
    p[-1] = 0.0
    I = build_kernel(q, r) @ p
    sigma = 0.05 * I + 1e-3
    sol = solve_tikhonov(q, I, sigma, dmax=5.0, alpha=1.0, nr=51, use_h32=True)
    cand = _candidate_from_numpy_solve(sol, rg_guinier_nm=1.5, alpha_gnom=1.0, dmax_nm=5.0)
    assert cand["ok"] is True
    assert cand["engine"] == "numpy_tikhonov"
    assert np.isfinite(cand["score"])
    assert cand["score"] == pytest.approx(shape_tight_extent_score(cand))


def test_stage1_h32_search_no_atsas(tmp_path):
    """Stage 1 picks (Dmax, α) with H32 NumPy only — must not need ATSAS."""
    q = np.linspace(0.15, 2.5, 80)
    r = np.linspace(0.0, 6.0, 61)
    p = np.exp(-((r - 2.2) / 0.9) ** 2)
    p[0] = p[-1] = 0.0
    I = build_kernel(q, r) @ p
    sigma = 0.04 * np.maximum(I, 1e-6) + 1e-4
    best, trials, failures, meta = _search_shannon_alpha_pr(
        output_dir=str(tmp_path),
        q_nm=q,
        I=I,
        sigma=sigma,
        rg_guinier_nm=2.0,
        first=1,
        last=None,
    )
    assert best["ok"] is True
    assert best["engine"] == "numpy_h32"
    assert best.get("alpha_map") == "identity_h32"
    assert float(best["dmax_nm"]) > 0
    assert float(best["alpha"]) > 0
    assert meta["engine"] == "fast_autognom_h32"
    assert meta["alpha_map"] == "identity_h32"
    assert meta["n_numpy_grid_ok"] > 0
    assert len(trials) > 0


def test_fit_distances_stage2_uses_stage1_alpha(monkeypatch):
    """Skill Stage 2 ATSAS call uses Stage‑1 (Dmax, α); identity map."""
    _stub_atsas_installed(monkeypatch)
    gnom_calls = []

    def _fake_guinier(q_nm, I, sigma, atsas_dat_path=None):
        return {
            "chosen": True,
            "chosen_Rg": 2.0,
            "rg_min": 1.5,
            "rg_max": 2.5,
            "chosen_interval": (0.1, 0.5),
            "quality_class": "good",
        }

    def _fake_search(**kwargs):
        assert "full_grid" not in kwargs
        assert "atsas_dat_path" not in kwargs
        best = {
            "dmax_nm": 7.5,
            "rmax_nm": 7.5,
            "alpha": 3.16,
            "score": -0.1,
            "ok": True,
            "engine": "numpy_h32",
            "alpha_map": "identity_h32",
            "search_workdir": None,
        }
        return best, [best], [], {"engine": "fast_autognom_h32", "n_ok": 1, "n_fail": 0}

    def _fake_run(cmd, cwd=None, capture_output=None, text=None, timeout=None, **kwargs):
        exe = cmd[0] if cmd else ""
        assert exe in ("datgnom", "gnom")
        gnom_calls.append(list(cmd))
        _write_fake_atsas_out(cmd, cwd=cwd)
        return _sp.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "autosaxs.skill.fit_distances.optimize.run_guinier_analysis", _fake_guinier
    )
    monkeypatch.setattr(
        "autosaxs.skill.fit_distances.distances._search_shannon_alpha_pr", _fake_search
    )
    monkeypatch.setattr("autosaxs.skill.fit_distances.runners.subprocess.run", _fake_run)
    monkeypatch.setattr("autosaxs.core.atsas_gnom.subprocess.run", _fake_run)

    with tempfile.TemporaryDirectory() as tmp:
        q = np.linspace(0.05, 2.0, 60)
        I = np.exp(-(q**2)) + 0.01
        sigma = 0.02 * I
        profile_path = os.path.join(tmp, "profile.dat")
        write_saxs(profile_path, q, I, sigma, {})
        out_dir = os.path.join(tmp, "distances")
        fit_distances(
            profile_path,
            output_dir=out_dir,
            first=None,
            rg_nm=None,
            minimal=True,
            use_cache=False,
        )
        assert gnom_calls, "Stage 2 must invoke ATSAS gnom"
        # Final Stage-2 solve should carry Stage-1 Dmax and α
        final = gnom_calls[-1]
        joined = " ".join(str(a) for a in final)
        assert "--rmax=7.5" in joined or any(
            str(a).startswith("--rmax=7.5") for a in final
        )
        assert any("3.16" in str(a) for a in final)
