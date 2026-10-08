"""Guinier helpers and Shannon×α GNOM search for fit_sizes."""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from autosaxs.core.gnom import distribution_arrays, parse_gnom_out
from autosaxs.core.shape_score import (
    ALPHA_GRID_N,
    DMAX_GRID_N,
    DR_RMAX_LO_RG_MULT,
    alpha_log10_grid,
    extent_candidate_grid,
    pick_best_by_score,
    q_min_first_positive_nm,
    shape_features_from_parsed,
    shape_tight_extent_score_dr,
)

from ..deps import EventBus, EventType
from ..fit_guinier.guinier import run_guinier_analysis
from .runners import _run_gnom_once


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
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """
    Joint Shannon-bound Rmax × log₁₀(α) polydisperse GNOM search.

    Selection: ``shape_tight_extent_score_dr`` (same soft-taper family; extent
    band adapted for size distributions via ``Rmax/Rg``).
    """
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
    # Honour explicit rmin as a floor on the search grid.
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
                    f"GNOM (fit_sizes): Shannon×α search "
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

    # Prefer non-suspicious; among near-top scores prefer larger Rmax (anti-stump).
    ok_trials = [t for t in trials if not _is_suspicious_candidate(t)] or list(trials)
    best = pick_best_by_score(
        ok_trials,
        shape_tight_extent_score_dr,
        prefer_larger_extent=True,
        tie_eps=0.05,
        extent_key="rmax_nm",
    )
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


def _optimize_rmax_nm(*_a, **_k):  # pragma: no cover - removed path
    raise RuntimeError(
        "fit_sizes: 1D rmax optimization was replaced by Shannon×α GNOM + "
        "shape_tight_extent_dr; use _search_shannon_alpha_dr."
    )
