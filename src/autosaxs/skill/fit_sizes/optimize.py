"""Guinier helpers and Shannon×α GNOM search for fit_sizes.

Default auto path (spheres / GNOM system=1): Fast AutoGNOM
  1) Stage 1 — H32 NumPy Tikhonov with the sphere volume-distribution
     operator over Shannon-bound ``(Rmax, α)`` + ``shape_tight_extent_dr``
     (no ATSAS in the search loop; optional NumPy Nelder–Mead polish)
  2) Stage 2 — probe top-K NumPy winners with ATSAS ``gnom --system=1``,
     optional short GNOM Nelder–Mead polish; pick by ``shape_tight_extent_dr``

Skill orchestration then rewrites stable ``gnom_best.out`` at the Stage‑2
``(Rmax, α)`` and runs the close-fits / force-zero-off ensemble (unless
``minimal``).
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from autosaxs.core.gnom import distribution_arrays, parse_gnom_out
from autosaxs.core.shape_score import (
    ALPHA_GRID_N,
    ALPHA_LOG10_HI,
    ALPHA_LOG10_LO,
    DMAX_GRID_N,
    DR_RMAX_LO_RG_MULT,
    alpha_log10_grid,
    extent_candidate_grid,
    pick_best_by_score,
    q_min_first_positive_nm,
    shape_features_from_distribution,
    shape_features_from_parsed,
    shape_tight_extent_score_dr,
    shannon_extent_bounds,
)
from autosaxs.core.tikhonov_pr import (
    DEFAULT_NR,
    DEFAULT_SPHERE_KERNEL,
    nr_for_dmax,
    solve_tikhonov,
)
from ..deps import EventBus, EventType
from ..fit_guinier.guinier import run_guinier_analysis
from ..gnom_fit_common import nelder_mead_2d, slice_profile_window
from .runners import _run_gnom_once

# Stage-1 NumPy grid + optional continuous polish (no ATSAS).
_NUMPY_NM_MAXITER = 35
_NUMPY_GRID_N_RMAX = 15
_NUMPY_GRID_N_ALPHA = 9
# Stage-2 ATSAS probe + optional local polish.
_GNOM_PROBE_TOP_K = 5
_GNOM_NM_MAXITER = 45


def _is_suspicious_candidate(c: Dict[str, Any]) -> bool:
    return bool(c.get("suspicious"))


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
            "fit_sizes: fit_guinier (Guinier analysis) did not return a chosen result; "
            "cannot derive rmax span or --first."
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
        raise ValueError("fit_sizes: Guinier q_min is not finite")
    idx = int(np.argmin(np.abs(q_nm - float(q_target))))
    return idx + 1


def _candidate_from_gnom_out(
    out_text: str,
    *,
    shape: str,
    system: int,
    rmax_nm: float,
    rmin_nm: Optional[float],
    rad56_nm: Optional[float],
    first: Optional[int],
    last: Optional[int],
    alpha: Optional[float],
    nr: Optional[int],
    out_path: str,
    rc: int,
    stderr: str,
    intermediate: bool,
    rg_guinier_nm: Optional[float] = None,
) -> Dict[str, Any]:
    parsed = parse_gnom_out(out_text)
    total = parsed.get("total_estimate")
    suspicious = bool(parsed.get("suspicious"))
    dr = parsed.get("distribution")
    diag: Dict[str, Any] = {
        "total_estimate": total,
        "parse_dr_ok": dr is not None,
    }
    if dr is not None:
        arrays = distribution_arrays(dr)
        if arrays is not None:
            _r, d, _err = arrays
            d_arr = np.asarray(d, dtype=float)
            if d_arr.size > 0 and np.any(np.isfinite(d_arr)):
                diag["neg_frac"] = float(np.mean(d_arr < 0.0))

    feats = shape_features_from_parsed(
        parsed,
        extent_requested_nm=float(rmax_nm),
        rg_guinier_nm=rg_guinier_nm,
    )
    cand: Dict[str, Any] = {
        "shape": shape,
        "system": int(system),
        "rmin_nm": rmin_nm,
        "rmax_nm": float(rmax_nm),
        "rad56_nm": rad56_nm,
        "first": int(first) if first is not None else None,
        "last": int(last) if last is not None else None,
        "alpha": alpha if alpha is not None else feats.get("alpha"),
        "nr": nr,
        "suspicious": suspicious,
        "out_path": out_path,
        "intermediate": bool(intermediate),
        "ok": True,
        "returncode": int(rc),
        "stderr": stderr,
        **diag,
        **{k: v for k, v in feats.items() if k not in diag},
    }
    cand["score"] = shape_tight_extent_score_dr(cand)
    return cand


def _candidate_from_numpy_dr(
    sol: Any,
    *,
    rg_guinier_nm: float,
    alpha_gnom: float,
    rmax_nm: float,
    shape: str,
    system: int,
    rmin_nm: Optional[float],
) -> Dict[str, Any]:
    feats = shape_features_from_distribution(sol.r, sol.p, extent_nm=float(rmax_nm))
    rg = float(rg_guinier_nm)
    cand: Dict[str, Any] = {
        **feats,
        "chi2_med": float(sol.chi2_med),
        "alpha": float(alpha_gnom),
        "log10_alpha": float(np.log10(alpha_gnom)) if alpha_gnom > 0 else float("nan"),
        "rmax_nm": float(rmax_nm),
        "rmin_nm": rmin_nm,
        "shape": shape,
        "system": int(system),
        "rg_guinier_nm": rg,
        "d_over_rg": float(rmax_nm / rg) if rg > 0 else None,
        "ok": True,
        "engine": "numpy_h32_sphere",
        "parse_dr_ok": True,
        "neg_frac": float(feats.get("neg_frac", 0.0)),
    }
    cand["score"] = shape_tight_extent_score_dr(cand)
    return cand


def _eval_numpy_dr_point(
    *,
    q_w: np.ndarray,
    I_w: np.ndarray,
    sig_w: np.ndarray,
    rmax: float,
    alpha_gnom: float,
    nr_seed: int,
    rmax_seed: float,
    rmin_nm: Optional[float],
    force_zero_rmin: bool,
    force_zero_rmax: bool,
    rg_guinier_nm: float,
    shape: str,
    system: int,
    nr_user: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """H32 sphere Tikhonov evaluate one ``(Rmax, α_GNOM)`` — no ATSAS."""
    try:
        if nr_user is not None and int(nr_user) >= 3:
            nr = int(nr_user)
        else:
            nr = nr_for_dmax(
                float(rmax), nr_seed=int(nr_seed), dmax_seed_nm=float(rmax_seed)
            )
            nr = int(min(max(nr, int(0.75 * nr_seed)), int(1.35 * nr_seed), 401))
        r_lo = float(rmin_nm) if rmin_nm is not None and np.isfinite(float(rmin_nm)) else 0.0
        if not (float(rmax) > r_lo):
            return None
        sol = solve_tikhonov(
            q_w,
            I_w,
            sig_w,
            dmax=float(rmax),
            alpha=float(alpha_gnom),
            nr=nr,
            rmin=r_lo,
            force_zero_rmin=force_zero_rmin,
            force_zero_rmax=force_zero_rmax,
            kernel=DEFAULT_SPHERE_KERNEL,
            use_h32=True,
            alpha_scale=1.0,
        )
    except Exception:
        return None
    return _candidate_from_numpy_dr(
        sol,
        rg_guinier_nm=rg_guinier_nm,
        alpha_gnom=float(alpha_gnom),
        rmax_nm=float(rmax),
        shape=shape,
        system=system,
        rmin_nm=rmin_nm,
    )


def _run_gnom_dr_candidate(
    *,
    atsas_dat_path: str,
    work_dir: str,
    system: int,
    shape: str,
    rmax: float,
    alpha: float,
    rmin_nm: Optional[float],
    rad56_nm: Optional[float],
    first: Optional[int],
    last: Optional[int],
    nr: Optional[int],
    force_zero_rmin: str,
    force_zero_rmax: str,
    rg_guinier_nm: float,
    tag: str,
) -> Optional[Dict[str, Any]]:
    out_path = os.path.join(work_dir, f"{tag}.out")
    ok, rc, stderr, out_text = _run_gnom_once(
        atsas_dat_path=atsas_dat_path,
        output_dir=work_dir,
        system=system,
        rmin_nm=rmin_nm,
        rmax_nm=float(rmax),
        rad56_nm=rad56_nm,
        first=first,
        last=last,
        alpha=float(alpha),
        nr=nr,
        out_path=out_path,
        force_zero_rmin=force_zero_rmin,
        force_zero_rmax=force_zero_rmax,
    )
    if not ok or not out_text:
        return None
    cand = _candidate_from_gnom_out(
        out_text,
        shape=shape,
        system=system,
        rmax_nm=float(rmax),
        rmin_nm=rmin_nm,
        rad56_nm=rad56_nm,
        first=first,
        last=last,
        alpha=float(alpha),
        nr=nr,
        out_path=out_path,
        rc=rc,
        stderr=stderr,
        intermediate=True,
        rg_guinier_nm=rg_guinier_nm,
    )
    cand["log10_alpha"] = float(np.log10(alpha)) if alpha > 0 else float("nan")
    cand["engine"] = "gnom_system1"
    return cand


def _pick_dr_best(trials: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    ok_trials = [t for t in trials if not _is_suspicious_candidate(t)] or list(trials)
    return pick_best_by_score(
        ok_trials,
        shape_tight_extent_score_dr,
        prefer_larger_extent=True,
        tie_eps=0.05,
        extent_key="rmax_nm",
    )


def _search_shannon_alpha_dr_fast(
    *,
    atsas_dat_path: str,
    output_dir: str,
    q_nm: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray],
    system: int,
    shape: str,
    rg_guinier_nm: float,
    rmin_nm: Optional[float],
    rad56_nm: Optional[float],
    first: Optional[int],
    last: Optional[int],
    nr: Optional[int],
    force_zero_rmin: str = "Y",
    force_zero_rmax: str = "Y",
    fixed_alpha: Optional[float] = None,
    event_bus: Optional[EventBus] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """
    Fast AutoGNOM for spheres: NumPy Stage 1 → ATSAS top-K (+ polish) Stage 2.

    α is in GNOM-printed coordinates via H32 packaging (identity map into Stage 2).
    """
    rg = float(rg_guinier_nm)
    if not (rg > 0 and np.isfinite(rg)):
        raise ValueError(f"fit_sizes: invalid rg_guinier_nm={rg_guinier_nm}")
    if int(system) != 1:
        raise ValueError(
            f"fit_sizes: Fast AutoGNOM sphere operator requires system=1; got {system}"
        )

    q_min = q_min_first_positive_nm(q_nm)
    r_lo, r_hi, extent_meta = shannon_extent_bounds(
        q_min_nm=q_min,
        rg_guinier_nm=rg,
        lo_rg_mult=DR_RMAX_LO_RG_MULT,
    )
    if rmin_nm is not None and np.isfinite(float(rmin_nm)):
        rmin_f = float(rmin_nm)
        if rmin_f >= r_hi:
            raise RuntimeError(
                f"fit_sizes: Shannon Rmax domain empty after rmin_nm={rmin_f} filter."
            )
        r_lo = max(r_lo, rmin_f + 1e-9)
        extent_meta = dict(extent_meta)
        extent_meta["rmin_floor_nm"] = rmin_f
        extent_meta["extent_lo_nm"] = float(r_lo)

    alpha_fixed = None
    if fixed_alpha is not None and np.isfinite(float(fixed_alpha)) and float(fixed_alpha) > 0:
        alpha_fixed = float(fixed_alpha)
        la_lo = la_hi = float(np.log10(alpha_fixed))
    else:
        la_lo, la_hi = float(ALPHA_LOG10_LO), float(ALPHA_LOG10_HI)
    bounds = ((float(r_lo), float(r_hi)), (la_lo, la_hi))

    first_i = int(first) if first is not None else 1
    q_w, I_w, sig_w = slice_profile_window(
        q_nm, I, sigma, first=first_i, last=last, skill_id="fit_sizes"
    )
    fz_rmin_b = str(force_zero_rmin).upper().startswith("Y")
    fz_rmax_b = str(force_zero_rmax).upper().startswith("Y")

    # Seed NR at a mid-band extent (~2×Rg) inside the Shannon window.
    r_seed = float(np.clip(2.0 * rg, r_lo, r_hi))
    nr_seed = int(nr) if nr is not None and int(nr) >= 3 else int(DEFAULT_NR)

    work_dir = tempfile.mkdtemp(prefix="fit_sizes_fast_", dir=output_dir)
    trials: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    t0 = time.perf_counter()

    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"GNOM (fit_sizes): Fast AutoGNOM Stage 1 — H32 sphere Tikhonov "
                    f"over Rmax∈[{r_lo:.4g}, {r_hi:.4g}] nm × "
                    f"log10(α)∈[{la_lo:.3g}, {la_hi:.3g}] "
                    f"(nr_seed={nr_seed} at R≈{r_seed:.4g} nm; no ATSAS)…"
                ),
            },
        )

    rmax_grid, _ = extent_candidate_grid(
        q_min_nm=q_min,
        rg_guinier_nm=rg,
        n=_NUMPY_GRID_N_RMAX,
        lo_rg_mult=DR_RMAX_LO_RG_MULT,
    )
    rmax_grid = rmax_grid[(rmax_grid > r_lo - 1e-15) & (rmax_grid < r_hi + 1e-15)]
    if rmax_grid.size == 0:
        rmax_grid = np.linspace(r_lo, r_hi, _NUMPY_GRID_N_RMAX)
    if alpha_fixed is not None:
        a_grid = np.asarray([float(np.log10(alpha_fixed))], dtype=float)
    else:
        a_grid = alpha_log10_grid(n=_NUMPY_GRID_N_ALPHA)

    numpy_trials: List[Dict[str, Any]] = []
    for rmax in rmax_grid:
        for log_a in a_grid:
            alpha = float(10.0 ** float(log_a))
            cand = _eval_numpy_dr_point(
                q_w=q_w,
                I_w=I_w,
                sig_w=sig_w,
                rmax=float(rmax),
                alpha_gnom=alpha,
                nr_seed=nr_seed,
                rmax_seed=r_seed,
                rmin_nm=rmin_nm,
                force_zero_rmin=fz_rmin_b,
                force_zero_rmax=fz_rmax_b,
                rg_guinier_nm=rg,
                shape=shape,
                system=system,
                nr_user=nr,
            )
            if cand is None:
                failures.append(
                    {
                        "rmax_nm": float(rmax),
                        "log10_alpha": float(log_a),
                        "alpha": alpha,
                        "ok": False,
                        "engine": "numpy_h32_sphere",
                    }
                )
                continue
            cand["log10_alpha"] = float(log_a)
            numpy_trials.append(cand)

    best_np = _pick_dr_best(numpy_trials)
    if best_np is None:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError(
            "fit_sizes: Fast AutoGNOM H32 NumPy grid produced no successful trial "
            f"({len(failures)} failures)."
        )

    def numpy_neg_score(x: np.ndarray) -> float:
        rmax_x, la = float(x[0]), float(x[1])
        cand = _eval_numpy_dr_point(
            q_w=q_w,
            I_w=I_w,
            sig_w=sig_w,
            rmax=rmax_x,
            alpha_gnom=float(10.0 ** la),
            nr_seed=nr_seed,
            rmax_seed=r_seed,
            rmin_nm=rmin_nm,
            force_zero_rmin=fz_rmin_b,
            force_zero_rmax=fz_rmax_b,
            rg_guinier_nm=rg,
            shape=shape,
            system=system,
            nr_user=nr,
        )
        if cand is None:
            return 1e3
        return -float(cand["score"])

    x0_np = np.asarray(
        [float(best_np["rmax_nm"]), float(best_np["log10_alpha"])], dtype=float
    )
    x_np, _f_np, n_np_nm = nelder_mead_2d(
        x0=x0_np,
        bounds=bounds,
        objective=numpy_neg_score,
        maxiter=_NUMPY_NM_MAXITER,
    )
    best_np_cont = _eval_numpy_dr_point(
        q_w=q_w,
        I_w=I_w,
        sig_w=sig_w,
        rmax=float(x_np[0]),
        alpha_gnom=float(10.0 ** float(x_np[1])),
        nr_seed=nr_seed,
        rmax_seed=r_seed,
        rmin_nm=rmin_nm,
        force_zero_rmin=fz_rmin_b,
        force_zero_rmax=fz_rmax_b,
        rg_guinier_nm=rg,
        shape=shape,
        system=system,
        nr_user=nr,
    )
    if best_np_cont is not None and float(best_np_cont["score"]) >= float(best_np["score"]):
        stage1 = best_np_cont
        stage1["log10_alpha"] = float(x_np[1])
    else:
        stage1 = dict(best_np)

    t_numpy = time.perf_counter() - t0
    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"GNOM (fit_sizes): Stage 1 done in {t_numpy:.2f}s — "
                    f"Rmax={float(stage1['rmax_nm']):.4g} nm, "
                    f"α={float(stage1['alpha']):.4g}; ATSAS top-K probe…"
                ),
            },
        )

    # --- Stage 2: probe top NumPy points with GNOM, then local Nelder–Mead ---
    gnom_cache: Dict[Tuple[float, float], Dict[str, Any]] = {}
    gnom_evals = 0

    def gnom_neg_score(x: np.ndarray) -> float:
        nonlocal gnom_evals
        rmax_x = float(x[0])
        la = float(x[1])
        alpha = float(10.0 ** la)
        key = (round(rmax_x, 6), round(la, 6))
        if key in gnom_cache:
            return -float(gnom_cache[key]["score"])
        gnom_evals += 1
        cand = _run_gnom_dr_candidate(
            atsas_dat_path=atsas_dat_path,
            work_dir=work_dir,
            system=system,
            shape=shape,
            rmax=rmax_x,
            alpha=alpha,
            rmin_nm=rmin_nm,
            rad56_nm=rad56_nm,
            first=first,
            last=last,
            nr=nr,
            force_zero_rmin=force_zero_rmin,
            force_zero_rmax=force_zero_rmax,
            rg_guinier_nm=rg,
            tag=f"nm_{gnom_evals:04d}",
        )
        if cand is None:
            failures.append(
                {
                    "rmax_nm": rmax_x,
                    "log10_alpha": la,
                    "alpha": alpha,
                    "ok": False,
                    "engine": "gnom_system1_nm",
                }
            )
            return 1e3
        gnom_cache[key] = cand
        trials.append(
            {
                "rmax_nm": cand.get("rmax_nm"),
                "log10_alpha": cand.get("log10_alpha"),
                "alpha": cand.get("alpha"),
                "score": cand.get("score"),
                "total_estimate": cand.get("total_estimate"),
                "neg_frac": cand.get("neg_frac"),
                "chi2_med": cand.get("chi2_med"),
                "d_over_rg": cand.get("d_over_rg"),
                "engine": "gnom_system1_nm",
                "intermediate": True,
            }
        )
        return -float(cand["score"])

    probe_pool = sorted(numpy_trials, key=lambda c: -float(c.get("score", -1e300)))[
        :_GNOM_PROBE_TOP_K
    ]
    probe_pool.append(stage1)
    for t in probe_pool:
        try:
            r_p = float(t["rmax_nm"])
            la_p = float(t.get("log10_alpha", np.log10(float(t["alpha"]))))
        except (TypeError, ValueError, KeyError):
            continue
        if not (np.isfinite(r_p) and np.isfinite(la_p)):
            continue
        _ = gnom_neg_score(np.asarray([r_p, la_p], dtype=float))

    best_probe = _pick_dr_best(list(gnom_cache.values()))
    if best_probe is None:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError(
            "fit_sizes: Fast AutoGNOM GNOM probe produced no successful trial."
        )

    # Optional short GNOM polish (skip when α is user-fixed — 1D in Rmax only).
    if alpha_fixed is None:
        x0_g = np.asarray(
            [
                float(best_probe["rmax_nm"]),
                float(
                    best_probe.get(
                        "log10_alpha", np.log10(float(best_probe["alpha"]))
                    )
                ),
            ],
            dtype=float,
        )
        x_g, _f_g, n_g_nm = nelder_mead_2d(
            x0=x0_g,
            bounds=bounds,
            objective=gnom_neg_score,
            maxiter=_GNOM_NM_MAXITER,
        )
        _ = gnom_neg_score(x_g)
    else:
        # 1D Rmax polish at fixed α
        def gnom_neg_rmax_only(x: np.ndarray) -> float:
            return gnom_neg_score(np.asarray([float(x[0]), la_lo], dtype=float))

        x0_1d = np.asarray([float(best_probe["rmax_nm"]), la_lo], dtype=float)
        x_g, _f_g, n_g_nm = nelder_mead_2d(
            x0=x0_1d,
            bounds=((r_lo, r_hi), (la_lo, la_hi)),
            objective=gnom_neg_rmax_only,
            maxiter=max(15, _GNOM_NM_MAXITER // 2),
        )
        _ = gnom_neg_score(np.asarray([float(x_g[0]), la_lo], dtype=float))

    best_g = _pick_dr_best(list(gnom_cache.values()))
    if best_g is None:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError(
            "fit_sizes: Fast AutoGNOM GNOM polish produced no successful trial."
        )

    t_total = time.perf_counter() - t0
    best_g = dict(best_g)
    best_g["search_workdir"] = work_dir
    best_g["engine"] = "fast_autognom_h32_sphere_then_gnom"
    best_g["alpha_map"] = "identity_h32"
    best_g["numpy_stage"] = {
        "rmax_nm": float(stage1["rmax_nm"]),
        "alpha": float(stage1["alpha"]),
        "score": float(stage1["score"]),
        "n_grid_ok": len(numpy_trials),
        "n_nm_evals": int(n_np_nm),
        "wall_s": float(t_numpy),
    }

    numpy_sorted = sorted(numpy_trials, key=lambda c: -float(c.get("score", -1e300)))
    for t in numpy_sorted[:20]:
        trials.insert(
            0,
            {
                "rmax_nm": t.get("rmax_nm"),
                "log10_alpha": t.get("log10_alpha"),
                "alpha": t.get("alpha"),
                "score": t.get("score"),
                "chi2_med": t.get("chi2_med"),
                "d_over_rg": t.get("d_over_rg"),
                "engine": "numpy_h32_sphere",
                "intermediate": True,
            },
        )

    grid_meta: Dict[str, Any] = {
        **extent_meta,
        "alpha_log10_lo": la_lo,
        "alpha_log10_hi": la_hi,
        "alpha_grid_n": int(len(a_grid)),
        "score": "shape_tight_extent_dr",
        "engine": "fast_autognom_h32_sphere_then_gnom",
        "alpha_map": "identity_h32",
        "nr_seed": int(nr_seed),
        "rmax_seed_nm": float(r_seed),
        "n_ok": len(trials),
        "n_fail": len(failures),
        "n_numpy_grid_ok": len(numpy_trials),
        "n_gnom_evals": int(gnom_evals),
        "n_gnom_nm_iters": int(n_g_nm),
        "wall_s": float(t_total),
        "numpy_wall_s": float(t_numpy),
    }
    if alpha_fixed is not None:
        grid_meta["alpha_fixed"] = float(alpha_fixed)
    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"GNOM (fit_sizes): Fast AutoGNOM done in {t_total:.2f}s "
                    f"({gnom_evals} GNOM evals) — "
                    f"Rmax={float(best_g['rmax_nm']):.4g} nm, "
                    f"α={float(best_g['alpha']):.4g}, "
                    f"score={float(best_g['score']):.4g}"
                ),
            },
        )
    return best_g, trials, failures, grid_meta


def _search_shannon_alpha_dr_atsas_grid(
    *,
    atsas_dat_path: str,
    output_dir: str,
    q_nm: np.ndarray,
    system: int,
    shape: str,
    rg_guinier_nm: float,
    rmin_nm: Optional[float],
    rad56_nm: Optional[float],
    first: Optional[int],
    last: Optional[int],
    nr: Optional[int],
    force_zero_rmin: str = "Y",
    force_zero_rmax: str = "Y",
    fixed_alpha: Optional[float] = None,
    n_rmax: int = DMAX_GRID_N,
    n_alpha: int = ALPHA_GRID_N,
    event_bus: Optional[EventBus] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """ATSAS-only Shannon×α grid for non-sphere systems (e.g. deprecated rods)."""
    rg = float(rg_guinier_nm)
    if not (rg > 0 and np.isfinite(rg)):
        raise ValueError(f"fit_sizes: invalid rg_guinier_nm={rg_guinier_nm}")

    q_min = q_min_first_positive_nm(q_nm)
    rmax_grid, grid_meta = extent_candidate_grid(
        q_min_nm=q_min,
        rg_guinier_nm=rg,
        n=int(n_rmax),
        lo_rg_mult=DR_RMAX_LO_RG_MULT,
    )
    if rmin_nm is not None and np.isfinite(float(rmin_nm)):
        rmin_f = float(rmin_nm)
        rmax_grid = rmax_grid[rmax_grid > rmin_f]
        if rmax_grid.size == 0:
            raise RuntimeError(
                f"fit_sizes: Shannon Rmax grid empty after rmin_nm={rmin_f} filter."
            )
        grid_meta = dict(grid_meta)
        grid_meta["rmin_floor_nm"] = rmin_f
        grid_meta["grid_lo"] = float(rmax_grid[0])
        grid_meta["grid_hi"] = float(rmax_grid[-1])
        grid_meta["n"] = int(rmax_grid.size)

    if fixed_alpha is not None and np.isfinite(float(fixed_alpha)) and float(fixed_alpha) > 0:
        a_grid = np.asarray([float(np.log10(float(fixed_alpha)))], dtype=float)
        grid_meta = dict(grid_meta)
        grid_meta["alpha_fixed"] = float(fixed_alpha)
    else:
        a_grid = alpha_log10_grid(n=int(n_alpha))
        grid_meta = dict(grid_meta)
    grid_meta["alpha_log10_lo"] = float(a_grid[0])
    grid_meta["alpha_log10_hi"] = float(a_grid[-1])
    grid_meta["alpha_grid_n"] = int(len(a_grid))
    grid_meta["score"] = "shape_tight_extent_dr"
    grid_meta["engine"] = "gnom_system_fixed_alpha"

    n_total = int(len(rmax_grid) * len(a_grid))
    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"GNOM (fit_sizes): Shannon×α ATSAS search (system={system}) "
                    f"Rmax∈[{float(rmax_grid[0]):.4g}, {float(rmax_grid[-1]):.4g}] nm "
                    f"× log10(α) ({n_total} trials); pick by shape_tight_extent_dr…"
                ),
            },
        )

    trials: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    work_dir = tempfile.mkdtemp(prefix="fit_sizes_shannon_", dir=output_dir)
    done = 0
    for rmax in rmax_grid:
        for log_a in a_grid:
            alpha = float(10.0 ** float(log_a))
            out_name = f"rmax_{float(rmax):.6g}_log10a_{float(log_a):.6g}.out"
            out_path = os.path.join(work_dir, out_name)
            ok, rc, stderr, out_text = _run_gnom_once(
                atsas_dat_path=atsas_dat_path,
                output_dir=work_dir,
                system=system,
                rmin_nm=rmin_nm,
                rmax_nm=float(rmax),
                rad56_nm=rad56_nm,
                first=first,
                last=last,
                alpha=alpha,
                nr=nr,
                out_path=out_path,
                force_zero_rmin=force_zero_rmin,
                force_zero_rmax=force_zero_rmax,
            )
            done += 1
            if not ok or not out_text:
                failures.append(
                    {
                        "rmax_nm": float(rmax),
                        "log10_alpha": float(log_a),
                        "alpha": alpha,
                        "ok": False,
                        "returncode": int(rc),
                        "stderr": stderr,
                    }
                )
                continue
            cand = _candidate_from_gnom_out(
                out_text,
                shape=shape,
                system=system,
                rmax_nm=float(rmax),
                rmin_nm=rmin_nm,
                rad56_nm=rad56_nm,
                first=first,
                last=last,
                alpha=alpha,
                nr=nr,
                out_path=out_path,
                rc=rc,
                stderr=stderr,
                intermediate=True,
                rg_guinier_nm=rg,
            )
            cand["log10_alpha"] = float(log_a)
            trials.append(cand)
            if event_bus and (done % 50 == 0 or done == n_total):
                event_bus.publish(
                    EventType.MESSAGE,
                    {"text": f"GNOM (fit_sizes): Shannon×α progress {done}/{n_total}…"},
                )

    best = _pick_dr_best(trials)
    if best is None:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise RuntimeError(
            "fit_sizes: Shannon×α GNOM search produced no successful trial "
            f"({len(failures)} failures)."
        )
    best["search_workdir"] = work_dir
    grid_meta["n_ok"] = len(trials)
    grid_meta["n_fail"] = len(failures)
    return best, trials, failures, grid_meta


def _search_shannon_alpha_dr(
    *,
    atsas_dat_path: str,
    output_dir: str,
    q_nm: np.ndarray,
    system: int,
    shape: str,
    rg_guinier_nm: float,
    rmin_nm: Optional[float],
    rad56_nm: Optional[float],
    first: Optional[int],
    last: Optional[int],
    nr: Optional[int],
    force_zero_rmin: str = "Y",
    force_zero_rmax: str = "Y",
    fixed_alpha: Optional[float] = None,
    n_rmax: int = DMAX_GRID_N,
    n_alpha: int = ALPHA_GRID_N,
    event_bus: Optional[EventBus] = None,
    I: Optional[np.ndarray] = None,
    sigma: Optional[np.ndarray] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """
    Joint Shannon-bound Rmax × log₁₀(α) polydisperse GNOM search.

    Default for spheres (system=1): Fast AutoGNOM (NumPy Stage 1 → ATSAS Stage 2).
    Non-sphere systems keep an ATSAS Shannon×α grid (no NumPy sphere operator).
    """
    if int(system) == 1:
        if I is None:
            raise ValueError(
                "fit_sizes: Fast AutoGNOM requires intensity array I for Stage 1"
            )
        return _search_shannon_alpha_dr_fast(
            atsas_dat_path=atsas_dat_path,
            output_dir=output_dir,
            q_nm=q_nm,
            I=I,
            sigma=sigma,
            system=system,
            shape=shape,
            rg_guinier_nm=rg_guinier_nm,
            rmin_nm=rmin_nm,
            rad56_nm=rad56_nm,
            first=first,
            last=last,
            nr=nr,
            force_zero_rmin=force_zero_rmin,
            force_zero_rmax=force_zero_rmax,
            fixed_alpha=fixed_alpha,
            event_bus=event_bus,
        )
    return _search_shannon_alpha_dr_atsas_grid(
        atsas_dat_path=atsas_dat_path,
        output_dir=output_dir,
        q_nm=q_nm,
        system=system,
        shape=shape,
        rg_guinier_nm=rg_guinier_nm,
        rmin_nm=rmin_nm,
        rad56_nm=rad56_nm,
        first=first,
        last=last,
        nr=nr,
        force_zero_rmin=force_zero_rmin,
        force_zero_rmax=force_zero_rmax,
        fixed_alpha=fixed_alpha,
        n_rmax=n_rmax,
        n_alpha=n_alpha,
        event_bus=event_bus,
    )


def _optimize_rmax_nm(*_a, **_k):  # pragma: no cover - removed path
    raise RuntimeError(
        "fit_sizes: 1D rmax optimization was replaced by Shannon×α GNOM + "
        "shape_tight_extent_dr; use _search_shannon_alpha_dr."
    )
