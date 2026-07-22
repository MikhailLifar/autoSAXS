"""Guinier/rmax optimization and GNOM candidate helpers for fit_sizes."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from autosaxs.core.gnom import candidate_score, distribution_arrays, parse_gnom_out

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
    cand: Dict[str, Any] = {
        "shape": shape,
        "system": int(system),
        "rmin_nm": rmin_nm,
        "rmax_nm": float(rmax_nm),
        "rad56_nm": rad56_nm,
        "first": int(first) if first is not None else None,
        "last": int(last) if last is not None else None,
        "alpha": alpha,
        "nr": nr,
        "suspicious": suspicious,
        "out_path": out_path,
        "intermediate": bool(intermediate),
        "ok": True,
        "returncode": int(rc),
        "stderr": stderr,
        **diag,
    }
    cand["score"] = candidate_score(cand)
    return cand


def _trial_better(
    sc: float,
    susp: bool,
    best_score: float,
    best_rmax: Optional[float],
    best_suspicious: bool,
) -> bool:
    if best_rmax is None:
        return True
    if susp and not best_suspicious:
        return False
    if not susp and best_suspicious:
        return True
    return sc > best_score


def _optimize_rmax_nm(
    *,
    atsas_dat_path: str,
    output_dir: str,
    system: int,
    shape: str,
    rg_max_nm: float,
    rmin_nm: Optional[float],
    rad56_nm: Optional[float],
    first: Optional[int],
    last: Optional[int],
    alpha: Optional[float],
    nr: Optional[int],
    eval_tmp_path: str,
    timeout_s: float = 30.0,
    event_bus: Optional[EventBus] = None,
) -> Tuple[float, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Bounded 1D search for rmax in (rmax_lo, 3 * rg_max_nm], maximizing TE − neg_frac."""
    from scipy.optimize import minimize_scalar

    rg_max_nm = float(rg_max_nm)
    if rg_max_nm <= 0 or not np.isfinite(rg_max_nm):
        raise ValueError(f"fit_sizes: invalid rg_max from fit_guinier: {rg_max_nm}")

    rmax_lo = 1e-6
    rmax_hi = 3.0 * rg_max_nm
    t0 = time.monotonic()
    trials: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    best_score = float("-inf")
    best_rmax: Optional[float] = None
    best_suspicious = False

    def objective(rmax: float) -> float:
        nonlocal best_score, best_rmax, best_suspicious
        if time.monotonic() - t0 > timeout_s:
            return 1e10
        rm = float(max(rmax_lo, min(float(rmax), rmax_hi)))
        ok, rc, stderr, out_text = _run_gnom_once(
            atsas_dat_path=atsas_dat_path,
            output_dir=output_dir,
            system=system,
            rmin_nm=rmin_nm,
            rmax_nm=rm,
            rad56_nm=rad56_nm,
            first=first,
            last=last,
            alpha=alpha,
            nr=nr,
            out_path=eval_tmp_path,
        )
        if not ok:
            failures.append(
                {
                    "rmax_nm": rm,
                    "ok": False,
                    "returncode": int(rc),
                    "stderr": stderr,
                }
            )
            if event_bus:
                event_bus.publish(
                    EventType.MESSAGE,
                    {"text": f"GNOM (fit_sizes): rmax trial failed at rmax={rm:.4g} nm (rc={rc})."},
                )
            return 1e10
        cand = _candidate_from_gnom_out(
            out_text,
            shape=shape,
            system=system,
            rmax_nm=rm,
            rmin_nm=rmin_nm,
            rad56_nm=rad56_nm,
            first=first,
            last=last,
            alpha=alpha,
            nr=nr,
            out_path="",
            rc=rc,
            stderr=stderr,
            intermediate=True,
        )
        trials.append(cand)
        sc = float(cand["score"])
        susp = _is_suspicious_candidate(cand)
        if _trial_better(sc, susp, best_score, best_rmax, best_suspicious):
            best_score = sc
            best_rmax = rm
            best_suspicious = susp
        return -sc

    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"GNOM (fit_sizes): optimizing rmax in [{rmax_lo:.4g}, {rmax_hi:.4g}] nm "
                    f"(30 s max)…"
                ),
            },
        )

    try:
        minimize_scalar(
            objective,
            bounds=(rmax_lo, rmax_hi),
            method="bounded",
            options={"maxiter": 40},
        )
    except Exception:
        pass

    if best_rmax is None:
        raise RuntimeError(
            "fit_sizes: rmax optimization produced no successful GNOM trial within 30 s."
        )
    return float(best_rmax), trials, failures
