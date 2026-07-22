"""Guinier/Rg optimization and DATGNOM candidate helpers for fit_distances."""

from __future__ import annotations

import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from autosaxs.core.gnom import candidate_score, distribution_arrays, parse_gnom_out

from ..deps import EventBus, EventType
from ..fit_guinier.guinier import run_guinier_analysis
from .quality_io import _pr_metrics
from .runners import _run_datgnom_once


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
) -> Dict[str, Any]:
    parsed = parse_gnom_out(out_text)
    total = parsed.get("total_estimate")
    suspicious = bool(parsed.get("suspicious"))
    rmax_nm = parsed.get("real_space_rmax")
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

    cand: Dict[str, Any] = {
        "rg_nm": float(rg_nm),
        "first": int(first) if first is not None else None,
        "last": int(last) if last is not None else None,
        "smooth": float(smooth) if smooth is not None else None,
        "rmax_nm": rmax_nm,
        "suspicious": suspicious,
        "out_path": out_path,
        "intermediate": bool(intermediate),
        "ok": True,
        "returncode": int(rc),
        "stderr": stderr,
        **diag,
        **prm,
    }
    cand["score"] = candidate_score(cand)
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


def _optimize_rg_nm(
    *,
    atsas_dat_path: str,
    output_dir: str,
    rg_max_nm: float,
    first: int,
    last: Optional[int],
    smooth: float,
    eval_tmp_path: str,
    timeout_s: float = 30.0,
    event_bus: Optional[EventBus] = None,
) -> Tuple[float, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
  1D bounded search for Rg in (rg_lo, 1.5 * rg_max_nm], maximizing TE − neg_frac per DATGNOM trial.
    """
    from scipy.optimize import minimize_scalar

    rg_max_nm = float(rg_max_nm)
    if rg_max_nm <= 0 or not np.isfinite(rg_max_nm):
        raise ValueError(f"fit_distances: invalid rg_max from fit_guinier: {rg_max_nm}")

    rg_lo = 1e-6
    rg_hi = 1.5 * rg_max_nm
    t0 = time.monotonic()
    trials: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    best_score = float("-inf")
    best_rg: Optional[float] = None

    def objective(rg: float) -> float:
        nonlocal best_score, best_rg
        if time.monotonic() - t0 > timeout_s:
            return 1e10
        rg_v = float(max(rg_lo, min(float(rg), rg_hi)))
        ok, rc, stderr, out_text = _run_datgnom_once(
            atsas_dat_path=atsas_dat_path,
            output_dir=output_dir,
            rg_nm=rg_v,
            first=int(first),
            last=last,
            smooth=float(smooth),
            out_path=eval_tmp_path,
        )
        if not ok:
            failures.append(
                {
                    "rg_nm": rg_v,
                    "first": int(first),
                    "last": last,
                    "smooth": float(smooth),
                    "ok": False,
                    "returncode": int(rc),
                    "stderr": stderr,
                }
            )
            if event_bus:
                event_bus.publish(
                    EventType.MESSAGE,
                    {"text": f"DATGNOM (fit_distances): Rg trial failed at rg={rg_v:.4g} nm (rc={rc})."},
                )
            return 1e10
        cand = _candidate_from_out_text(
            out_text,
            rg_nm=rg_v,
            first=first,
            last=last,
            smooth=smooth,
            out_path="",
            rc=rc,
            stderr=stderr,
            intermediate=True,
        )
        trials.append(cand)
        sc = float(cand["score"])
        if sc > best_score:
            best_score = sc
            best_rg = rg_v
        return -sc

    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"DATGNOM (fit_distances): optimizing Rg in [{rg_lo:.4g}, {rg_hi:.4g}] nm "
                    f"(30 s max)…"
                ),
            },
        )

    try:
        minimize_scalar(
            objective,
            bounds=(rg_lo, rg_hi),
            method="bounded",
            options={"maxiter": 40},
        )
    except Exception:
        pass

    if best_rg is None:
        raise RuntimeError(
            "fit_distances: Rg optimization produced no successful DATGNOM trial within 30 s."
        )
    return float(best_rg), trials, failures
