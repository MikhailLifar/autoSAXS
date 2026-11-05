"""Guinier helpers and Shannon×α GNOM search for fit_distances."""

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
    alpha_log10_grid,
    extent_candidate_grid,
    pick_best_by_score,
    q_min_first_positive_nm,
    shape_features_from_parsed,
    shape_tight_extent_score,
)

from ..deps import EventBus, EventType
from ..fit_guinier.guinier import run_guinier_analysis
from .quality_io import _pr_metrics
from .runners import _run_gnom_pr_once


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
    event_bus: Optional[EventBus] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    """
    Joint Shannon-bound Dmax × log₁₀(α) monodisperse GNOM search.

    Selection: ``shape_tight_extent`` (soft taper + non-neg + low wiggle − χ² − extent).
    Returns ``(best_candidate, trials, failures, grid_meta)``.
    """
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
    grid_meta["engine"] = "gnom_pr_fixed_alpha"

    n_total = int(len(dmax_grid) * len(a_grid))
    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"GNOM (fit_distances): Shannon×α search "
                    f"Dmax∈[{float(dmax_grid[0]):.4g}, {float(dmax_grid[-1]):.4g}] nm "
                    f"× log10(α)∈[{float(a_grid[0]):.3g}, {float(a_grid[-1]):.3g}] "
                    f"({n_total} trials); pick by shape_tight_extent…"
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
        # Keep outs only for diagnostics via trial paths until best is copied;
        # wipe the scratch dir after we copy the winner in the caller if needed.
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


# Back-compat alias name used by older tests / callers.
def _optimize_rg_nm(*_a, **_k):  # pragma: no cover - removed path
    raise RuntimeError(
        "fit_distances: Rg→DATGNOM optimization was replaced by Shannon×α GNOM + "
        "shape_tight_extent; use _search_shannon_alpha_pr."
    )
