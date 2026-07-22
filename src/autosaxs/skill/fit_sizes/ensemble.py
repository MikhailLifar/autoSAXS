"""Rmax close-fit ensemble and force-zero-off probe for fit_sizes."""

from __future__ import annotations

import csv
import os
from typing import Any, Dict, List, Optional

import numpy as np

from autosaxs.core.gnom import candidate_score, distribution_arrays, parse_gnom_out
from autosaxs.core.gnom_quality import (
    _find_local_maxima,
    analyze_rmax_validation,
    classify_stability,
    dr_distribution_moments,
)

from ..deps import EventBus, EventType
from .runners import _run_gnom_once

_CLOSE_FIT_RMAX_FACTORS = (0.90, 0.95, 1.00, 1.05, 1.10)
_FORCE_ZERO_OFF_RMAX_FACTOR = 1.5


def _row_from_out(
    *,
    role: str,
    rmax_factor: float,
    rmax_nm: float,
    force_zero_rmax: str,
    ok: bool,
    rc: int,
    stderr: str,
    out_path: str,
    out_text: str,
    rmax_ref_nm: Optional[float] = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "role": role,
        "rmax_factor": float(rmax_factor),
        "rmax_nm": float(rmax_nm),
        "force_zero_rmax": force_zero_rmax,
        "ok": bool(ok),
        "returncode": int(rc),
        "stderr": stderr,
        "out_path": out_path if ok else "",
        "total_estimate": None,
        "neg_frac": None,
        "peak_r_nm": None,
        "pdi": None,
        "score": None,
        "rmax_ref_nm": rmax_ref_nm,
    }
    if not ok:
        return row
    parsed = parse_gnom_out(out_text)
    arrays = distribution_arrays(parsed.get("distribution"))
    neg_frac = None
    peak_r = None
    pdi = None
    if arrays is not None:
        rr, dd, _ee = arrays
        dd = np.asarray(dd, dtype=float)
        if dd.size and np.any(np.isfinite(dd)):
            neg_frac = float(np.mean(dd < 0.0))
        peaks = _find_local_maxima(np.asarray(rr, dtype=float), dd)
        if peaks:
            peak_r = float(peaks[0])
        moments = dr_distribution_moments(np.asarray(rr, dtype=float), dd)
        pdi = moments.get("pdi")
    te = parsed.get("total_estimate")
    row["total_estimate"] = te
    row["neg_frac"] = neg_frac
    row["peak_r_nm"] = peak_r
    row["pdi"] = pdi
    row["score"] = candidate_score({"total_estimate": te, "neg_frac": neg_frac})
    row["suspicious"] = bool(parsed.get("suspicious"))
    return row


def run_rmax_ensemble(
    *,
    atsas_dat_path: str,
    sample_output_dir: str,
    best_rmax_nm: float,
    system: int,
    shape: str,
    rmin_nm: Optional[float],
    rad56_nm: Optional[float],
    first: Optional[int],
    last: Optional[int],
    alpha: Optional[float],
    nr: Optional[int],
    best_parsed: Dict[str, Any],
    event_bus: Optional[EventBus],
) -> Dict[str, Any]:
    """Persist Rmax±10% close fits and a force-zero-off validation .out."""
    ensemble_dir = os.path.join(sample_output_dir, "ensemble")
    close_fits_dir = os.path.join(ensemble_dir, "close_fits")
    os.makedirs(close_fits_dir, exist_ok=True)

    rows: List[Dict[str, Any]] = []
    close_fit_out_paths: List[str] = []

    for fac in _CLOSE_FIT_RMAX_FACTORS:
        rmax = float(best_rmax_nm) * float(fac)
        out_name = f"gnom_rmax_{rmax:.4f}.out"
        out_path = os.path.join(close_fits_dir, out_name)
        ok, rc, stderr, out_text = _run_gnom_once(
            atsas_dat_path=atsas_dat_path,
            output_dir=close_fits_dir,
            system=system,
            rmin_nm=rmin_nm,
            rmax_nm=rmax,
            rad56_nm=rad56_nm,
            first=first,
            last=last,
            alpha=alpha,
            nr=nr,
            out_path=out_path,
            force_zero_rmax="Y",
        )
        row = _row_from_out(
            role="close_fit",
            rmax_factor=float(fac),
            rmax_nm=rmax,
            force_zero_rmax="Y",
            ok=ok,
            rc=rc,
            stderr=stderr,
            out_path=out_path,
            out_text=out_text,
        )
        rows.append(row)
        if ok:
            close_fit_out_paths.append(out_path)
        if event_bus:
            status = "ok" if ok else f"failed rc={rc}"
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"GNOM (fit_sizes): close-fit rmax×{fac:.2f}={rmax:.4g} nm ({status})"},
            )

    force_zero_off_out_path = ""
    force_zero_off_parsed: Optional[Dict[str, Any]] = None
    rmax_ext = float(best_rmax_nm) * float(_FORCE_ZERO_OFF_RMAX_FACTOR)
    fz_out = os.path.join(ensemble_dir, f"gnom_rmax_{rmax_ext:.4f}_force_zero_off.out")
    ok_fz, rc_fz, stderr_fz, out_text_fz = _run_gnom_once(
        atsas_dat_path=atsas_dat_path,
        output_dir=ensemble_dir,
        system=system,
        rmin_nm=rmin_nm,
        rmax_nm=rmax_ext,
        rad56_nm=rad56_nm,
        first=first,
        last=last,
        alpha=alpha,
        nr=nr,
        out_path=fz_out,
        force_zero_rmax="N",
    )
    fz_row = _row_from_out(
        role="force_zero_off",
        rmax_factor=float(_FORCE_ZERO_OFF_RMAX_FACTOR),
        rmax_nm=rmax_ext,
        force_zero_rmax="N",
        ok=ok_fz,
        rc=rc_fz,
        stderr=stderr_fz,
        out_path=fz_out,
        out_text=out_text_fz,
        rmax_ref_nm=float(best_rmax_nm),
    )
    rows.append(fz_row)
    if ok_fz:
        force_zero_off_out_path = fz_out
        force_zero_off_parsed = parse_gnom_out(out_text_fz)
    if event_bus:
        status = "ok" if ok_fz else f"failed rc={rc_fz}"
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"GNOM (fit_sizes): force-zero-off probe at "
                    f"{_FORCE_ZERO_OFF_RMAX_FACTOR:.2f}×Rmax={rmax_ext:.4g} nm ({status})"
                ),
            },
        )

    summary_path = os.path.join(ensemble_dir, "ensemble_summary.csv")
    with open(summary_path, "w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(
            [
                "role",
                "rmax_factor",
                "rmax_nm",
                "force_zero_rmax",
                "ok",
                "total_estimate",
                "neg_frac",
                "peak_r_nm",
                "pdi",
                "score",
                "out_path",
            ]
        )
        for row in rows:
            w.writerow(
                [
                    row.get("role"),
                    row.get("rmax_factor"),
                    row.get("rmax_nm"),
                    row.get("force_zero_rmax"),
                    bool(row.get("ok")),
                    row.get("total_estimate"),
                    row.get("neg_frac"),
                    row.get("peak_r_nm"),
                    row.get("pdi"),
                    row.get("score"),
                    row.get("out_path"),
                ]
            )

    rmax_validation = analyze_rmax_validation(
        best_parsed=best_parsed,
        ensemble_rows=[r for r in rows if r.get("role") == "close_fit"],
        force_zero_off_parsed=force_zero_off_parsed,
        rmax_ref_nm=float(best_rmax_nm),
    )
    stability_class = classify_stability(
        ensemble_rows=[r for r in rows if r.get("role") == "close_fit"],
        rmax_validation=rmax_validation,
    )

    return {
        "ensemble_dir": ensemble_dir,
        "ensemble_summary_path": summary_path,
        "close_fit_out_paths": close_fit_out_paths,
        "force_zero_off_out_path": force_zero_off_out_path,
        "ensemble_rows": rows,
        "force_zero_off_parsed": force_zero_off_parsed,
        "rmax_ref_nm": float(best_rmax_nm),
        "rmax_validation": rmax_validation,
        "stability_class": stability_class,
    }
