"""Guinier helpers and Shannon×α GNOM search for fit_distances.

Default auto path: two-stage Fast AutoGNOM
  1) global NumPy Tikhonov + ``shape_tight_extent`` (no ATSAS in the loop)
  2) local Nelder–Mead polish with ATSAS ``gnom`` as oracle (full 2D domain)

Opt-in ``full_grid=True`` restores the legacy 31×11 serial ATSAS grid.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize

from autosaxs.core.gnom import distribution_arrays, parse_gnom_out
from autosaxs.core.shape_score import (
    ALPHA_GRID_N,
    ALPHA_LOG10_HI,
    ALPHA_LOG10_LO,
    DMAX_GRID_N,
    alpha_log10_grid,
    extent_candidate_grid,
    pick_best_by_score,
    q_min_first_positive_nm,
    shape_features_from_distribution,
    shape_features_from_parsed,
    shape_tight_extent_score,
    shannon_extent_bounds,
)
from autosaxs.core.tikhonov_pr import (
    DEFAULT_NR,
    calibrate_alpha_scale,
    nr_for_dmax,
    solve_tikhonov,
)
from ..deps import EventBus, EventType
from ..fit_guinier.guinier import run_guinier_analysis
from .quality_io import _pr_metrics
from .runners import _run_gnom_pr_once

# Local GNOM polish budget (full-domain Nelder–Mead, not shrunk bounds).
_GNOM_NM_MAXITER = 45
_NUMPY_NM_MAXITER = 35
# Coarser NumPy global grid (continuous polish + GNOM NM follow); full 31×11 is legacy only.
_NUMPY_GRID_N_DMAX = 15
_NUMPY_GRID_N_ALPHA = 9
# How many top NumPy grid points to probe with GNOM before local polish.
_GNOM_PROBE_TOP_K = 5


def _candidate_from_out_text(
    out_text: str,
    *,
    rg_nm: float,
    first: Optional[int],
    last: Optional[int],
    smooth: Optional[float],
    out_path: str,
    rc: int,
    stderr: str,
    intermediate: bool,
    alpha: Optional[float] = None,
    extent_nm: Optional[float] = None,
) -> Dict[str, Any]:
    parsed = parse_gnom_out(out_text)
    total = parsed.get("total_estimate")
    suspicious = bool(parsed.get("suspicious"))
    rmax_nm = parsed.get("real_space_rmax")
    if rmax_nm is None and extent_nm is not None:
        rmax_nm = float(extent_nm)
    pr = parsed.get("distribution")

    diag: Dict[str, Any] = {"total_estimate": total}
    prm: Dict[str, Any] = {}
    arrays = distribution_arrays(pr)
    if arrays is None:
        diag["parse_pr_ok"] = False
    else:
        r, p, _err = arrays
        diag["parse_pr_ok"] = True
        p = np.asarray(p, dtype=float)
        if p.size == 0 or not np.any(np.isfinite(p)):
            diag["parse_pr_ok"] = False
        else:
            p_abs_max = float(np.nanmax(np.abs(p))) if np.any(np.isfinite(p)) else 0.0
            diag["p_abs_max"] = p_abs_max
            if np.isfinite(p_abs_max) and p_abs_max > 0:
                diag["neg_frac"] = float(np.mean(p < 0.0))
                tail_n = min(5, int(p.size))
                tail = p[-tail_n:]
                diag["tail_ratio"] = float(np.nanmean(np.abs(tail)) / (p_abs_max + 1e-12))
                if p.size >= 3:
                    d2 = np.diff(p, n=2)
                    diag["smoothness"] = float(np.nanmean(np.abs(d2)) / (p_abs_max + 1e-12))
                else:
                    diag["smoothness"] = 1.0
            prm = _pr_metrics(np.asarray(r, dtype=float), p)

    extent_for_feat = float(rmax_nm) if rmax_nm is not None else float(extent_nm or 0.0)
    feats = shape_features_from_parsed(
        parsed,
        extent_requested_nm=extent_for_feat if extent_for_feat > 0 else 1.0,
        rg_guinier_nm=float(rg_nm),
    )

    cand: Dict[str, Any] = {
        "rg_nm": float(rg_nm),
        "first": int(first) if first is not None else None,
        "last": int(last) if last is not None else None,
        "smooth": float(smooth) if smooth is not None else None,
        "alpha": float(alpha) if alpha is not None else feats.get("alpha"),
        "rmax_nm": rmax_nm,
        "suspicious": suspicious,
        "out_path": out_path,
        "intermediate": bool(intermediate),
        "ok": True,
        "returncode": int(rc),
        "stderr": stderr,
        **diag,
        **prm,
        **{k: v for k, v in feats.items() if k not in diag},
    }
    cand["score"] = shape_tight_extent_score(cand)
    return cand


def _guinier_from_profile(
    q_nm: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray],
    atsas_dat_path: str,
) -> Dict[str, Any]:
    """In-process fit_guinier (run_guinier_analysis) for Rg span and Guinier interval."""
    results = run_guinier_analysis(q_nm, I, sigma, atsas_dat_path=atsas_dat_path)
    if results.get("chosen") is None:
        raise RuntimeError(
            "fit_distances: fit_guinier (Guinier analysis) did not return a chosen result; "
            "cannot derive Rg span or --first."
        )
    ch_int = results.get("chosen_interval")
    return {
        "rg": results.get("chosen_Rg"),
        "rg_min": results.get("rg_min"),
        "rg_max": results.get("rg_max"),
        "q_min": ch_int[0] if ch_int else None,
        "q_max": ch_int[1] if ch_int else None,
        "chosen_interval": ch_int,
        "quality_class": results.get("quality_class"),
    }


def _q_to_first_point_1based(q_nm: np.ndarray, q_target: float) -> int:
    q_nm = np.asarray(q_nm, dtype=float)
    if not np.isfinite(q_target):
        raise ValueError("fit_distances: Guinier q_min is not finite")
    idx = int(np.argmin(np.abs(q_nm - float(q_target))))
    return idx + 1


def _slice_profile_window(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray],
    *,
    first: int,
    last: Optional[int],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """1-based inclusive ``first``/``last`` → arrays for the Tikhonov data term."""
    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    n = int(q.size)
    i0 = max(0, int(first) - 1)
    i1 = n if last is None else min(n, int(last))
    if i1 <= i0:
        raise ValueError(f"fit_distances: empty q-window first={first} last={last} n={n}")
    q_w = q[i0:i1]
    I_w = I[i0:i1]
    if sigma is None:
        # Match ``write_saxs_atsas_format`` (4% of I) so NumPy and ATSAS share weights.
        sig_w = 0.04 * np.maximum(np.abs(I_w), 1e-300)
    else:
        sig_w = np.asarray(sigma, dtype=float)[i0:i1]
        # Replace non-positive / non-finite σ; fall back to the same 4% convention.
        fallback = 0.04 * np.maximum(np.abs(I_w), 1e-300)
        bad = ~(np.isfinite(sig_w) & (sig_w > 0))
        if np.any(bad):
            sig_w = sig_w.copy()
            sig_w[bad] = fallback[bad]
    return q_w, I_w, sig_w


def _candidate_from_numpy_solve(
    sol: Any,
    *,
    rg_guinier_nm: float,
    alpha_gnom: float,
    dmax_nm: float,
) -> Dict[str, Any]:
    feats = shape_features_from_distribution(sol.r, sol.p, extent_nm=float(dmax_nm))
    rg = float(rg_guinier_nm)
    cand: Dict[str, Any] = {
        **feats,
        "chi2_med": float(sol.chi2_med),
        "alpha": float(alpha_gnom),
        "log10_alpha": float(np.log10(alpha_gnom)) if alpha_gnom > 0 else float("nan"),
        "dmax_nm": float(dmax_nm),
        "rmax_nm": float(dmax_nm),
        "rg_guinier_nm": rg,
        "d_over_rg": float(dmax_nm / rg) if rg > 0 else None,
        "ok": True,
        "engine": "numpy_tikhonov",
        "rg_pr_nm": float(sol.rg_pr),
        "i0_pr": float(sol.i0_pr),
    }
    cand["score"] = shape_tight_extent_score(cand)
    return cand


def _search_shannon_alpha_pr_full_grid(
    *,
    atsas_dat_path: str,
    output_dir: str,
    q_nm: np.ndarray,
    rg_guinier_nm: float,
    first: int,
    last: Optional[int],
    force_zero_rmin: str = "Y",
    force_zero_rmax: str = "Y",
    n_dmax: int = DMAX_GRID_N,
    n_alpha: int = ALPHA_GRID_N,
    I: Optional[np.ndarray] = None,
    sigma: Optional[np.ndarray] = None,
    event_bus: Optional[EventBus] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """Legacy 31×11 serial ATSAS ``gnom`` grid (opt-in via ``full_grid=True``)."""
    _ = I, sigma  # unused on the ATSAS-only grid path
    rg = float(rg_guinier_nm)
    if not (rg > 0 and np.isfinite(rg)):
        raise ValueError(f"fit_distances: invalid rg_guinier_nm={rg_guinier_nm}")

    q_min = q_min_first_positive_nm(q_nm)
    dmax_grid, grid_meta = extent_candidate_grid(
        q_min_nm=q_min,
        rg_guinier_nm=rg,
        n=int(n_dmax),
    )
    a_grid = alpha_log10_grid(n=int(n_alpha))
    grid_meta = dict(grid_meta)
    grid_meta["alpha_log10_lo"] = float(a_grid[0])
    grid_meta["alpha_log10_hi"] = float(a_grid[-1])
    grid_meta["alpha_grid_n"] = int(len(a_grid))
    grid_meta["score"] = "shape_tight_extent"
    grid_meta["engine"] = "gnom_pr_fixed_alpha_full_grid"

    n_total = int(len(dmax_grid) * len(a_grid))
    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"GNOM (fit_distances): Shannon×α full grid "
                    f"Dmax∈[{float(dmax_grid[0]):.4g}, {float(dmax_grid[-1]):.4g}] nm "
                    f"× log10(α)∈[{float(a_grid[0]):.3g}, {float(a_grid[-1]):.3g}] "
                    f"({n_total} ATSAS trials); pick by shape_tight_extent…"
                ),
            },
        )

    trials: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    work_dir = tempfile.mkdtemp(prefix="fit_distances_shannon_", dir=output_dir)
    try:
        done = 0
        for dmax in dmax_grid:
            for log_a in a_grid:
                alpha = float(10.0 ** float(log_a))
                out_name = f"dmax_{float(dmax):.6g}_log10a_{float(log_a):.6g}.out"
                out_path = os.path.join(work_dir, out_name)
                ok, rc, stderr, out_text = _run_gnom_pr_once(
                    atsas_dat_path=atsas_dat_path,
                    output_dir=work_dir,
                    rmax_nm=float(dmax),
                    first=int(first),
                    last=last,
                    alpha=alpha,
                    force_zero_rmin=force_zero_rmin,
                    force_zero_rmax=force_zero_rmax,
                    out_path=out_path,
                )
                done += 1
                if not ok or not out_text:
                    failures.append(
                        {
                            "dmax_nm": float(dmax),
                            "log10_alpha": float(log_a),
                            "alpha": alpha,
                            "ok": False,
                            "returncode": int(rc),
                            "stderr": stderr,
                        }
                    )
                    continue
                cand = _candidate_from_out_text(
                    out_text,
                    rg_nm=rg,
                    first=first,
                    last=last,
                    smooth=None,
                    out_path=out_path,
                    rc=rc,
                    stderr=stderr,
                    intermediate=True,
                    alpha=alpha,
                    extent_nm=float(dmax),
                )
                cand["dmax_nm"] = float(dmax)
                cand["log10_alpha"] = float(log_a)
                trials.append(cand)
                if event_bus and (done % 50 == 0 or done == n_total):
                    event_bus.publish(
                        EventType.MESSAGE,
                        {"text": f"GNOM (fit_distances): Shannon×α progress {done}/{n_total}…"},
                    )
    finally:
        pass

    best = pick_best_by_score(trials, shape_tight_extent_score)
    if best is None:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError(
            "fit_distances: Shannon×α GNOM search produced no successful trial "
            f"({len(failures)} failures)."
        )
    best["search_workdir"] = work_dir
    grid_meta["n_ok"] = len(trials)
    grid_meta["n_fail"] = len(failures)
    return best, trials, failures, grid_meta


def _eval_numpy_point(
    *,
    q_w: np.ndarray,
    I_w: np.ndarray,
    sig_w: np.ndarray,
    dmax: float,
    alpha_gnom: float,
    alpha_scale: float,
    nr_seed: int,
    dmax_seed: float,
    force_zero_rmin: bool,
    force_zero_rmax: bool,
    rg_guinier_nm: float,
) -> Optional[Dict[str, Any]]:
    try:
        # Mild NR scaling with Dmax (capped relative to seed) keeps the NumPy
        # landscape faithful without the cost of unbounded NR growth.
        nr = nr_for_dmax(float(dmax), nr_seed=int(nr_seed), dmax_seed_nm=float(dmax_seed))
        nr = int(min(max(nr, int(0.75 * nr_seed)), int(1.35 * nr_seed), 401))
        sol = solve_tikhonov(
            q_w,
            I_w,
            sig_w,
            dmax=float(dmax),
            alpha=float(alpha_gnom),
            nr=nr,
            force_zero_rmin=force_zero_rmin,
            force_zero_rmax=force_zero_rmax,
            alpha_scale=float(alpha_scale),
        )
    except Exception:
        return None
    return _candidate_from_numpy_solve(
        sol,
        rg_guinier_nm=rg_guinier_nm,
        alpha_gnom=float(alpha_gnom),
        dmax_nm=float(dmax),
    )


def _run_gnom_candidate(
    *,
    atsas_dat_path: str,
    work_dir: str,
    dmax: float,
    alpha: Optional[float],
    first: int,
    last: Optional[int],
    force_zero_rmin: str,
    force_zero_rmax: str,
    rg_guinier_nm: float,
    tag: str,
) -> Optional[Dict[str, Any]]:
    out_path = os.path.join(work_dir, f"{tag}.out")
    alpha_arg = float(alpha) if alpha is not None else None
    ok, rc, stderr, out_text = _run_gnom_pr_once(
        atsas_dat_path=atsas_dat_path,
        output_dir=work_dir,
        rmax_nm=float(dmax),
        first=int(first),
        last=last,
        alpha=alpha_arg,
        force_zero_rmin=force_zero_rmin,
        force_zero_rmax=force_zero_rmax,
        out_path=out_path,
    )
    if not ok or not out_text:
        return None
    cand = _candidate_from_out_text(
        out_text,
        rg_nm=float(rg_guinier_nm),
        first=first,
        last=last,
        smooth=None,
        out_path=out_path,
        rc=rc,
        stderr=stderr,
        intermediate=True,
        alpha=alpha_arg,
        extent_nm=float(dmax),
    )
    cand["dmax_nm"] = float(dmax)
    a_used = cand.get("alpha")
    try:
        a_f = float(a_used) if a_used is not None else float("nan")
    except (TypeError, ValueError):
        a_f = float("nan")
    cand["log10_alpha"] = float(np.log10(a_f)) if a_f > 0 and np.isfinite(a_f) else float("nan")
    cand["engine"] = "gnom_pr"
    return cand


def _nelder_mead_2d(
    *,
    x0: np.ndarray,
    bounds: Tuple[Tuple[float, float], Tuple[float, float]],
    objective: Callable[[np.ndarray], float],
    maxiter: int,
) -> Tuple[np.ndarray, float, int]:
    """
    Unconstrained Nelder–Mead in a mapped unit box.

    ``x`` is ``(Dmax, log10 α)``; soft-reject outside ``bounds`` with a large
    penalty so the algorithm stays on the Shannon / α domain without shrinking it.
    """
    (d_lo, d_hi), (la_lo, la_hi) = bounds
    n_eval = 0

    def mapped_obj(x: np.ndarray) -> float:
        nonlocal n_eval
        n_eval += 1
        d = float(x[0])
        la = float(x[1])
        if not (d_lo <= d <= d_hi and la_lo <= la <= la_hi):
            # Soft barrier: keep search full-domain but discourage walk-off
            pen = 0.0
            if d < d_lo:
                pen += (d_lo - d) ** 2
            elif d > d_hi:
                pen += (d - d_hi) ** 2
            if la < la_lo:
                pen += (la_lo - la) ** 2
            elif la > la_hi:
                pen += (la - la_hi) ** 2
            return 1e3 + 1e2 * pen
        return float(objective(np.asarray([d, la], dtype=float)))

    res = minimize(
        mapped_obj,
        np.asarray(x0, dtype=float),
        method="Nelder-Mead",
        options={"maxiter": int(maxiter), "xatol": 1e-3, "fatol": 1e-4, "adaptive": True},
    )
    x_best = np.asarray(res.x, dtype=float)
    # Clamp to domain for the returned point
    x_best[0] = float(np.clip(x_best[0], d_lo, d_hi))
    x_best[1] = float(np.clip(x_best[1], la_lo, la_hi))
    return x_best, float(res.fun), n_eval


def _search_shannon_alpha_pr_fast(
    *,
    atsas_dat_path: str,
    output_dir: str,
    q_nm: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray],
    rg_guinier_nm: float,
    first: int,
    last: Optional[int],
    force_zero_rmin: str = "Y",
    force_zero_rmax: str = "Y",
    event_bus: Optional[EventBus] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """
    Fast AutoGNOM: NumPy global search → local ATSAS GNOM Nelder–Mead polish.

    α is kept in **GNOM coordinates** throughout; NumPy uses a calibrated
    ``alpha_scale`` so the effective Tikhonov weight matches ATSAS at the seed.
    """
    rg = float(rg_guinier_nm)
    if not (rg > 0 and np.isfinite(rg)):
        raise ValueError(f"fit_distances: invalid rg_guinier_nm={rg_guinier_nm}")

    q_min = q_min_first_positive_nm(q_nm)
    d_lo, d_hi, extent_meta = shannon_extent_bounds(q_min_nm=q_min, rg_guinier_nm=rg)
    la_lo, la_hi = float(ALPHA_LOG10_LO), float(ALPHA_LOG10_HI)
    bounds = ((d_lo, d_hi), (la_lo, la_hi))

    q_w, I_w, sig_w = _slice_profile_window(
        q_nm, I, sigma, first=int(first), last=last
    )
    fz_rmin_b = str(force_zero_rmin).upper().startswith("Y")
    fz_rmax_b = str(force_zero_rmax).upper().startswith("Y")

    work_dir = tempfile.mkdtemp(prefix="fit_distances_fast_", dir=output_dir)
    trials: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    t0 = time.perf_counter()

    # --- Seed GNOM near expected extent (≈3.5 Rg) for NR + α-scale ---
    # Calibrate at two α anchors (low + mid of the search band) and take the
    # scale with the best p(r) correlation. A single mid-α seed can be a poor
    # match for curves whose GNOM optimum sits near the band edge.
    d_seed = float(np.clip(3.5 * rg, d_lo, d_hi))
    a_anchors = (
        float(10.0 ** la_lo),
        float(10.0 ** (0.5 * (la_lo + la_hi))),
    )
    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"GNOM (fit_distances): Fast AutoGNOM — seed at "
                    f"Dmax={d_seed:.4g} nm for α-scale (α∈{a_anchors})…"
                ),
            },
        )
    nr_seed = DEFAULT_NR
    alpha_scale, scale_corr, a_seed_f = 1.0, -1.0, a_anchors[0]
    seed_workdir_tag = None
    for i, a_try in enumerate(a_anchors):
        seed = _run_gnom_candidate(
            atsas_dat_path=atsas_dat_path,
            work_dir=work_dir,
            dmax=d_seed,
            alpha=float(a_try),
            first=first,
            last=last,
            force_zero_rmin=force_zero_rmin,
            force_zero_rmax=force_zero_rmax,
            rg_guinier_nm=rg,
            tag=f"seed_{i}",
        )
        if seed is None:
            continue
        seed_parsed = parse_gnom_out(seed["out_path"])
        nr_seed = int(seed_parsed.get("nr") or nr_seed)
        arrays = distribution_arrays(seed_parsed.get("distribution"))
        if arrays is None:
            continue
        r_ref, p_ref, _err = arrays
        sc, corr = calibrate_alpha_scale(
            q_w,
            I_w,
            sig_w,
            dmax=d_seed,
            alpha=float(a_try),
            r_ref=r_ref,
            p_ref=p_ref,
            nr=nr_seed,
            force_zero_rmin=fz_rmin_b,
            force_zero_rmax=fz_rmax_b,
        )
        if corr > scale_corr:
            alpha_scale, scale_corr, a_seed_f = float(sc), float(corr), float(a_try)
            seed_workdir_tag = seed.get("out_path")
    if scale_corr < 0:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError("fit_distances: Fast AutoGNOM seed GNOM / α-scale calibration failed.")
    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"GNOM (fit_distances): α_scale={alpha_scale:.4g} "
                    f"(p(r) corr={scale_corr:.6f} vs seed α={a_seed_f:.4g}); "
                    f"NumPy global search…"
                ),
            },
        )
    _ = seed_workdir_tag

    # --- Stage 1: NumPy global grid (same Shannon×α domain as legacy) ---
    dmax_grid, _ = extent_candidate_grid(
        q_min_nm=q_min, rg_guinier_nm=rg, n=_NUMPY_GRID_N_DMAX
    )
    a_grid = alpha_log10_grid(n=_NUMPY_GRID_N_ALPHA)
    numpy_trials: List[Dict[str, Any]] = []
    for dmax in dmax_grid:
        for log_a in a_grid:
            alpha = float(10.0 ** float(log_a))
            cand = _eval_numpy_point(
                q_w=q_w,
                I_w=I_w,
                sig_w=sig_w,
                dmax=float(dmax),
                alpha_gnom=alpha,
                alpha_scale=alpha_scale,
                nr_seed=nr_seed,
                dmax_seed=d_seed,
                force_zero_rmin=fz_rmin_b,
                force_zero_rmax=fz_rmax_b,
                rg_guinier_nm=rg,
            )
            if cand is None:
                failures.append(
                    {
                        "dmax_nm": float(dmax),
                        "log10_alpha": float(log_a),
                        "alpha": alpha,
                        "ok": False,
                        "engine": "numpy_tikhonov",
                    }
                )
                continue
            cand["log10_alpha"] = float(log_a)
            numpy_trials.append(cand)

    best_np = pick_best_by_score(numpy_trials, shape_tight_extent_score)
    if best_np is None:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError(
            "fit_distances: Fast AutoGNOM NumPy grid produced no successful trial "
            f"({len(failures)} failures)."
        )

    # NumPy continuous polish (still no ATSAS)
    def numpy_neg_score(x: np.ndarray) -> float:
        d, la = float(x[0]), float(x[1])
        cand = _eval_numpy_point(
            q_w=q_w,
            I_w=I_w,
            sig_w=sig_w,
            dmax=d,
            alpha_gnom=float(10.0 ** la),
            alpha_scale=alpha_scale,
            nr_seed=nr_seed,
            dmax_seed=d_seed,
            force_zero_rmin=fz_rmin_b,
            force_zero_rmax=fz_rmax_b,
            rg_guinier_nm=rg,
        )
        if cand is None:
            return 1e3
        return -float(cand["score"])

    x0_np = np.asarray(
        [float(best_np["dmax_nm"]), float(best_np["log10_alpha"])], dtype=float
    )
    x_np, _f_np, n_np_nm = _nelder_mead_2d(
        x0=x0_np,
        bounds=bounds,
        objective=numpy_neg_score,
        maxiter=_NUMPY_NM_MAXITER,
    )
    best_np_cont = _eval_numpy_point(
        q_w=q_w,
        I_w=I_w,
        sig_w=sig_w,
        dmax=float(x_np[0]),
        alpha_gnom=float(10.0 ** float(x_np[1])),
        alpha_scale=alpha_scale,
        nr_seed=nr_seed,
        dmax_seed=d_seed,
        force_zero_rmin=fz_rmin_b,
        force_zero_rmax=fz_rmax_b,
        rg_guinier_nm=rg,
    )
    if best_np_cont is not None and float(best_np_cont["score"]) >= float(best_np["score"]):
        stage1 = best_np_cont
        stage1["log10_alpha"] = float(x_np[1])
    else:
        stage1 = best_np

    t_numpy = time.perf_counter() - t0
    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"GNOM (fit_distances): NumPy stage done in {t_numpy:.2f}s — "
                    f"Dmax={float(stage1['dmax_nm']):.4g} nm, "
                    f"α={float(stage1['alpha']):.4g}, "
                    f"score={float(stage1['score']):.4g}; local GNOM polish…"
                ),
            },
        )

    # --- Stage 2: probe top NumPy points with GNOM, then local Nelder–Mead ---
    gnom_cache: Dict[Tuple[float, float], Dict[str, Any]] = {}
    gnom_evals = 0

    def gnom_neg_score(x: np.ndarray) -> float:
        nonlocal gnom_evals
        d = float(x[0])
        la = float(x[1])
        alpha = float(10.0 ** la)
        key = (round(d, 6), round(la, 6))
        if key in gnom_cache:
            return -float(gnom_cache[key]["score"])
        gnom_evals += 1
        cand = _run_gnom_candidate(
            atsas_dat_path=atsas_dat_path,
            work_dir=work_dir,
            dmax=d,
            alpha=alpha,
            first=first,
            last=last,
            force_zero_rmin=force_zero_rmin,
            force_zero_rmax=force_zero_rmax,
            rg_guinier_nm=rg,
            tag=f"nm_{gnom_evals:04d}",
        )
        if cand is None:
            failures.append(
                {
                    "dmax_nm": d,
                    "log10_alpha": la,
                    "alpha": alpha,
                    "ok": False,
                    "engine": "gnom_pr_nm",
                }
            )
            return 1e3
        gnom_cache[key] = cand
        trials.append(
            {
                "dmax_nm": cand.get("dmax_nm"),
                "log10_alpha": cand.get("log10_alpha"),
                "alpha": cand.get("alpha"),
                "score": cand.get("score"),
                "total_estimate": cand.get("total_estimate"),
                "neg_frac": cand.get("neg_frac"),
                "chi2_med": cand.get("chi2_med"),
                "d_over_rg": cand.get("d_over_rg"),
                "engine": "gnom_pr_nm",
                "intermediate": True,
            }
        )
        return -float(cand["score"])

    # Probe top-K NumPy grid points (+ continuous winner) so polish starts in a
    # GNOM-validated basin (α-scale residual can shift the NumPy argmax).
    probe_pool = sorted(numpy_trials, key=lambda c: -float(c.get("score", -1e300)))[
        :_GNOM_PROBE_TOP_K
    ]
    probe_pool.append(stage1)
    for t in probe_pool:
        try:
            d_p = float(t["dmax_nm"])
            la_p = float(t.get("log10_alpha", np.log10(float(t["alpha"]))))
        except (TypeError, ValueError, KeyError):
            continue
        if not (np.isfinite(d_p) and np.isfinite(la_p)):
            continue
        _ = gnom_neg_score(np.asarray([d_p, la_p], dtype=float))

    best_probe = pick_best_by_score(list(gnom_cache.values()), shape_tight_extent_score)
    if best_probe is None:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError("fit_distances: Fast AutoGNOM GNOM probe produced no successful trial.")
    x0_g = np.asarray(
        [
            float(best_probe["dmax_nm"]),
            float(best_probe.get("log10_alpha", np.log10(float(best_probe["alpha"])))),
        ],
        dtype=float,
    )
    x_g, _f_g, n_g_nm = _nelder_mead_2d(
        x0=x0_g,
        bounds=bounds,
        objective=gnom_neg_score,
        maxiter=_GNOM_NM_MAXITER,
    )
    # Ensure final point is evaluated / cached
    _ = gnom_neg_score(x_g)

    best_g = pick_best_by_score(list(gnom_cache.values()), shape_tight_extent_score)
    if best_g is None:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError(
            "fit_distances: Fast AutoGNOM GNOM polish produced no successful trial."
        )

    t_total = time.perf_counter() - t0
    best_g = dict(best_g)
    best_g["search_workdir"] = work_dir
    best_g["numpy_stage"] = {
        "dmax_nm": float(stage1["dmax_nm"]),
        "alpha": float(stage1["alpha"]),
        "score": float(stage1["score"]),
        "n_grid_ok": len(numpy_trials),
        "n_nm_evals": int(n_np_nm),
        "wall_s": float(t_numpy),
    }

    # Compact trial summary: top NumPy grid points + all GNOM polish evals
    numpy_sorted = sorted(numpy_trials, key=lambda c: -float(c.get("score", -1e300)))
    for t in numpy_sorted[:20]:
        trials.insert(
            0,
            {
                "dmax_nm": t.get("dmax_nm"),
                "log10_alpha": t.get("log10_alpha"),
                "alpha": t.get("alpha"),
                "score": t.get("score"),
                "chi2_med": t.get("chi2_med"),
                "d_over_rg": t.get("d_over_rg"),
                "engine": "numpy_tikhonov",
                "intermediate": True,
            },
        )

    grid_meta: Dict[str, Any] = {
        **extent_meta,
        "alpha_log10_lo": la_lo,
        "alpha_log10_hi": la_hi,
        "alpha_grid_n": int(len(a_grid)),
        "score": "shape_tight_extent",
        "engine": "fast_autognom_numpy_then_gnom_nm",
        "alpha_scale": float(alpha_scale),
        "alpha_scale_corr": float(scale_corr),
        "nr_seed": int(nr_seed),
        "dmax_seed_nm": float(d_seed),
        "n_ok": len(trials),
        "n_fail": len(failures),
        "n_numpy_grid_ok": len(numpy_trials),
        "n_gnom_evals": int(gnom_evals),
        "n_gnom_nm_iters": int(n_g_nm),
        "wall_s": float(t_total),
        "numpy_wall_s": float(t_numpy),
    }
    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"GNOM (fit_distances): Fast AutoGNOM done in {t_total:.2f}s "
                    f"({gnom_evals} GNOM evals) — "
                    f"Dmax={float(best_g['dmax_nm']):.4g} nm, "
                    f"α={float(best_g['alpha']):.4g}, "
                    f"score={float(best_g['score']):.4g}"
                ),
            },
        )
    return best_g, trials, failures, grid_meta


def _search_shannon_alpha_pr(
    *,
    atsas_dat_path: str,
    output_dir: str,
    q_nm: np.ndarray,
    rg_guinier_nm: float,
    first: int,
    last: Optional[int],
    force_zero_rmin: str = "Y",
    force_zero_rmax: str = "Y",
    n_dmax: int = DMAX_GRID_N,
    n_alpha: int = ALPHA_GRID_N,
    full_grid: bool = False,
    I: Optional[np.ndarray] = None,
    sigma: Optional[np.ndarray] = None,
    event_bus: Optional[EventBus] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """
    Joint Shannon-bound Dmax × log₁₀(α) monodisperse GNOM search.

    Default: Fast AutoGNOM (NumPy global + local ATSAS polish).
    ``full_grid=True``: legacy 31×11 serial ATSAS grid.

    Selection: ``shape_tight_extent``. Returns ``(best, trials, failures, grid_meta)``.
    """
    if full_grid:
        return _search_shannon_alpha_pr_full_grid(
            atsas_dat_path=atsas_dat_path,
            output_dir=output_dir,
            q_nm=q_nm,
            I=I,
            sigma=sigma,
            rg_guinier_nm=rg_guinier_nm,
            first=first,
            last=last,
            force_zero_rmin=force_zero_rmin,
            force_zero_rmax=force_zero_rmax,
            n_dmax=n_dmax,
            n_alpha=n_alpha,
            event_bus=event_bus,
        )
    if I is None:
        raise ValueError(
            "fit_distances: Fast AutoGNOM requires intensity array I "
            "(pass I= from the skill orchestration)."
        )
    return _search_shannon_alpha_pr_fast(
        atsas_dat_path=atsas_dat_path,
        output_dir=output_dir,
        q_nm=q_nm,
        I=I,
        sigma=sigma,
        rg_guinier_nm=rg_guinier_nm,
        first=first,
        last=last,
        force_zero_rmin=force_zero_rmin,
        force_zero_rmax=force_zero_rmax,
        event_bus=event_bus,
    )


# Back-compat alias name used by older tests / callers.
def _optimize_rg_nm(*_a, **_k):  # pragma: no cover - removed path
    raise RuntimeError(
        "fit_distances: Rg→DATGNOM optimization was replaced by Shannon×α GNOM + "
        "shape_tight_extent; use _search_shannon_alpha_pr."
    )
