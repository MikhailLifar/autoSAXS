"""ATSAS DATGNOM/GNOM runners and Dmax close-fits ensemble for fit_distances."""

from __future__ import annotations

import csv
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from autosaxs.core.gnom import candidate_score, distribution_arrays, parse_gnom_out
from autosaxs.core.gnom_quality import rg_from_pr

from ..deps import EventBus, EventType

_CLOSE_FIT_RMAX_FACTORS = (0.90, 0.95, 1.00, 1.05, 1.10)


_FORCE_ZERO_OFF_RMAX_FACTOR = 1.5


def _run_datgnom_once(
    *,
    atsas_dat_path: str,
    output_dir: str,
    rg_nm: float,
    first: Optional[int] = None,
    last: Optional[int] = None,
    smooth: Optional[float] = None,
    out_path: str,
) -> tuple[bool, int, str, str]:
    """
    Returns (ok, returncode, stderr, out_text).
    """
    # Always pass absolute paths because we run with cwd=output_dir and the caller
    # may already include output_dir in the provided relative paths.
    atsas_dat_path_abs = str(Path(atsas_dat_path).expanduser().resolve())
    out_path_abs = str(Path(out_path).expanduser().resolve())

    cmd: List[str] = ["datgnom", f"--rg={float(rg_nm):.6g}"]
    if first is not None:
        cmd.append(f"--first={int(first)}")
    if last is not None:
        cmd.append(f"--last={int(last)}")
    if smooth is not None:
        cmd.append(f"--smooth={float(smooth):.6g}")
    cmd += ["-o", out_path_abs, atsas_dat_path_abs]
    proc = subprocess.run(cmd, cwd=output_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        return False, int(proc.returncode), (proc.stderr or "")[:2000], ""
    if not os.path.isfile(out_path_abs):
        return False, int(proc.returncode), "gnom reported success but output file was not created", ""
    try:
        out_text = Path(out_path_abs).read_text(errors="replace")
    except OSError as e:
        return False, int(proc.returncode), f"failed to read DATGNOM output: {e}", ""
    return True, int(proc.returncode), (proc.stderr or "")[:2000], out_text


def _run_gnom_pr_once(
    *,
    atsas_dat_path: str,
    output_dir: str,
    rmax_nm: float,
    first: Optional[int] = None,
    last: Optional[int] = None,
    alpha: Optional[float] = None,
    force_zero_rmax: str = "Y",
    out_path: str,
) -> tuple[bool, int, str, str]:
    """Run monodisperse GNOM (system=0). Returns (ok, returncode, stderr, out_text)."""
    atsas_dat_arg = atsas_dat_path
    atsas_dat_local = os.path.basename(atsas_dat_path)
    if os.path.isfile(os.path.join(output_dir, atsas_dat_local)):
        atsas_dat_arg = atsas_dat_local
    out_arg = os.path.basename(out_path) if os.path.dirname(out_path) else out_path
    out_effective_path = os.path.join(output_dir, out_arg)

    cmd: List[str] = [
        "gnom",
        "--system=0",
        f"--rmax={float(rmax_nm):.6g}",
        f"--force-zero-rmax={force_zero_rmax}",
    ]
    if first is not None:
        cmd.append(f"--first={int(first)}")
    if last is not None:
        cmd.append(f"--last={int(last)}")
    if alpha is not None and np.isfinite(float(alpha)) and float(alpha) > 0:
        cmd.append(f"--alpha={float(alpha):.6g}")
    cmd += ["-o", out_arg, atsas_dat_arg]
    proc = subprocess.run(cmd, cwd=output_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        return False, int(proc.returncode), (proc.stderr or proc.stdout or "")[:2000], ""
    if not os.path.isfile(out_effective_path):
        return False, int(proc.returncode), "gnom reported success but output file was not created", ""
    try:
        out_text = Path(out_effective_path).read_text(errors="replace")
    except OSError as e:
        return False, int(proc.returncode), f"failed to read GNOM output: {e}", ""
    return True, int(proc.returncode), (proc.stderr or "")[:2000], out_text


def _run_dmax_close_fit_ensemble(
    *,
    atsas_dat_path: str,
    sample_output_dir: str,
    dmax_nm: float,
    first: Optional[int],
    last: Optional[int],
    alpha: Optional[float],
    event_bus: Optional[EventBus],
) -> Dict[str, Any]:
    """
    Persist a Dmax±10% close-fits ensemble and a force-zero-off validation .out.

    Close fits use ``--force-zero-rmax=Y`` at nearby Dmax values.
    The force-zero-off pathology probe uses an *extended* rmax (1.5× Dmax) with
    ``--force-zero-rmax=N`` (aggregation / repulsion check past the putative size).
    Alpha is left automatic for GNOM (DATGNOM Current ALPHA is not reused).
    """
    ensemble_dir = os.path.join(sample_output_dir, "ensemble")
    close_fits_dir = os.path.join(ensemble_dir, "close_fits")
    os.makedirs(close_fits_dir, exist_ok=True)
    # Intentionally ignore DATGNOM alpha for GNOM CLI probes (mismatched scale).
    _ = alpha

    rows: List[Dict[str, Any]] = []
    close_fit_out_paths: List[str] = []

    for fac in _CLOSE_FIT_RMAX_FACTORS:
        rmax = float(dmax_nm) * float(fac)
        out_name = f"gnom_rmax_{rmax:.4f}.out"
        out_path = os.path.join(close_fits_dir, out_name)
        ok, rc, stderr, out_text = _run_gnom_pr_once(
            atsas_dat_path=atsas_dat_path,
            output_dir=close_fits_dir,
            rmax_nm=rmax,
            first=first,
            last=last,
            alpha=None,
            force_zero_rmax="Y",
            out_path=out_path,
        )
        row: Dict[str, Any] = {
            "role": "close_fit",
            "rmax_factor": float(fac),
            "rmax_nm": rmax,
            "force_zero_rmax": "Y",
            "ok": bool(ok),
            "returncode": int(rc),
            "stderr": stderr,
            "out_path": out_path if ok else "",
            "total_estimate": None,
            "neg_frac": None,
            "rg_pr_nm": None,
            "score": None,
        }
        if ok:
            close_fit_out_paths.append(out_path)
            parsed = parse_gnom_out(out_text)
            arrays = distribution_arrays(parsed.get("distribution"))
            neg_frac = None
            rg_pr = parsed.get("real_space_rg")
            if arrays is not None:
                _r, p, _err = arrays
                p = np.asarray(p, dtype=float)
                if p.size and np.any(np.isfinite(p)):
                    neg_frac = float(np.mean(p < 0.0))
                rg_int = rg_from_pr(np.asarray(_r, dtype=float), p)
                if rg_int is not None:
                    rg_pr = rg_int
            te = parsed.get("total_estimate")
            row["total_estimate"] = te
            row["neg_frac"] = neg_frac
            row["rg_pr_nm"] = rg_pr
            row["score"] = candidate_score({"total_estimate": te, "neg_frac": neg_frac})
            row["suspicious"] = bool(parsed.get("suspicious"))
        rows.append(row)
        if event_bus:
            status = "ok" if ok else f"failed rc={rc}"
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"DATGNOM (fit_distances): close-fit rmax×{fac:.2f}={rmax:.4g} nm ({status})"},
            )

    force_zero_off_out_path = ""
    force_zero_off_parsed: Optional[Dict[str, Any]] = None
    rmax_ext = float(dmax_nm) * float(_FORCE_ZERO_OFF_RMAX_FACTOR)
    fz_out = os.path.join(
        ensemble_dir,
        f"gnom_rmax_{rmax_ext:.4f}_force_zero_off.out",
    )
    ok_fz, rc_fz, stderr_fz, out_text_fz = _run_gnom_pr_once(
        atsas_dat_path=atsas_dat_path,
        output_dir=ensemble_dir,
        rmax_nm=rmax_ext,
        first=first,
        last=last,
        alpha=None,
        force_zero_rmax="N",
        out_path=fz_out,
    )
    fz_row: Dict[str, Any] = {
        "role": "force_zero_off",
        "rmax_factor": float(_FORCE_ZERO_OFF_RMAX_FACTOR),
        "rmax_nm": rmax_ext,
        "force_zero_rmax": "N",
        "ok": bool(ok_fz),
        "returncode": int(rc_fz),
        "stderr": stderr_fz,
        "out_path": fz_out if ok_fz else "",
        "total_estimate": None,
        "neg_frac": None,
        "rg_pr_nm": None,
        "score": None,
        "dmax_ref_nm": float(dmax_nm),
    }
    if ok_fz:
        force_zero_off_out_path = fz_out
        force_zero_off_parsed = parse_gnom_out(out_text_fz)
        arrays = distribution_arrays(force_zero_off_parsed.get("distribution"))
        neg_frac = None
        rg_pr = force_zero_off_parsed.get("real_space_rg")
        if arrays is not None:
            _r, p, _err = arrays
            p = np.asarray(p, dtype=float)
            if p.size and np.any(np.isfinite(p)):
                neg_frac = float(np.mean(p < 0.0))
            rg_int = rg_from_pr(np.asarray(_r, dtype=float), p)
            if rg_int is not None:
                rg_pr = rg_int
        te = force_zero_off_parsed.get("total_estimate")
        fz_row["total_estimate"] = te
        fz_row["neg_frac"] = neg_frac
        fz_row["rg_pr_nm"] = rg_pr
        fz_row["score"] = candidate_score({"total_estimate": te, "neg_frac": neg_frac})
        fz_row["suspicious"] = bool(force_zero_off_parsed.get("suspicious"))
    rows.append(fz_row)
    if event_bus:
        status = "ok" if ok_fz else f"failed rc={rc_fz}"
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"DATGNOM (fit_distances): force-zero-off probe at "
                    f"{_FORCE_ZERO_OFF_RMAX_FACTOR:.2f}×Dmax={rmax_ext:.4g} nm ({status})"
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
                "rg_pr_nm",
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
                    row.get("rg_pr_nm"),
                    row.get("score"),
                    row.get("out_path"),
                ]
            )

    return {
        "ensemble_dir": ensemble_dir,
        "ensemble_summary_path": summary_path,
        "close_fit_out_paths": close_fit_out_paths,
        "force_zero_off_out_path": force_zero_off_out_path,
        "ensemble_rows": rows,
        "force_zero_off_parsed": force_zero_off_parsed,
        "dmax_ref_nm": float(dmax_nm),
    }
