"""Artifact writers for the fit_distances skill (CSV/YAML/reports/failure)."""

from __future__ import annotations

import csv
import os
import sys
from typing import Any, Dict, List, Optional, Union

import numpy as np
import yaml

from autosaxs.core.report_fragments import write_skill_report_fragments

from ..deps import EventBus, EventType
from ..gnom_fit_common import default_atsas_failure_message
from .quality_io import (
    _assess_and_write_pr_quality,
    _empty_pr_quality_fields,
    _pr_quality_markdown,
    _serialize_quality_for_return,
)
from .vis import write_fit_vs_exp_png, write_pr_png


def _ensemble_block_for_log(ensemble_info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Serialize ensemble probe results for the run log YAML (no heavy distribution tables)."""
    info = ensemble_info or {}
    fz_parsed = info.get("force_zero_off_parsed")
    fz_summary: Optional[Dict[str, Any]] = None
    if isinstance(fz_parsed, dict):
        fz_summary = {
            "total_estimate": fz_parsed.get("total_estimate"),
            "real_space_rg": fz_parsed.get("real_space_rg"),
            "real_space_i0": fz_parsed.get("real_space_i0"),
            "real_space_rmax": fz_parsed.get("real_space_rmax"),
            "current_alpha": fz_parsed.get("current_alpha"),
            "suspicious": bool(fz_parsed.get("suspicious")),
        }
    return {
        "dir": info.get("ensemble_dir") or "",
        "summary_path": info.get("ensemble_summary_path") or "",
        "close_fit_out_paths": list(info.get("close_fit_out_paths") or []),
        "force_zero_off_out_path": info.get("force_zero_off_out_path") or "",
        "dmax_ref_nm": info.get("dmax_ref_nm"),
        "rows": list(info.get("ensemble_rows") or []),
        "force_zero_off": fz_summary,
        "dmax_validation": info.get("dmax_validation"),
    }


_FITS_CSV_COLUMNS = [
    "rg_nm",
    "first",
    "last",
    "smooth",
    "rmax_nm",
    "peak_r",
    "peak_p",
    "fwhm",
    "suspicious",
    "intermediate",
    "total_estimate",
    "neg_frac",
    "score",
    "tail_ratio",
    "smoothness",
    "ok",
    "out_path",
]


def _write_fits_csv(path: str, candidates: List[Dict[str, Any]]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_FITS_CSV_COLUMNS)
        for c in candidates:
            w.writerow(
                [
                    c.get("rg_nm"),
                    c.get("first"),
                    c.get("last"),
                    c.get("smooth"),
                    c.get("rmax_nm"),
                    c.get("peak_r"),
                    c.get("peak_p"),
                    c.get("fwhm"),
                    bool(c.get("suspicious")),
                    bool(c.get("intermediate")),
                    c.get("total_estimate"),
                    c.get("neg_frac"),
                    c.get("score"),
                    c.get("tail_ratio"),
                    c.get("smoothness"),
                    bool(c.get("ok")),
                    c.get("out_path"),
                ]
            )


def _finalize_fit_distances_failure(
    *,
    output_dir: str,
    profile: str,
    base: str,
    atsas_dat_path: str,
    failure_reason: str,
    failures: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    guinier_summary: Optional[Dict[str, Any]],
    event_bus: Optional[EventBus],
    detail: str = "",
    q_nm: Optional[np.ndarray] = None,
    first_pt: Optional[int] = None,
    rg_guinier_nm: Optional[float] = None,
) -> Dict[str, Union[str, List[str]]]:
    """Write failure artifacts and return a non-throwing skill result dict."""
    os.makedirs(output_dir, exist_ok=True)
    message = default_atsas_failure_message("fit_distances")
    if detail.strip():
        message = f"{message}\n\nLast error: {detail.strip()[:1500]}"

    failure_txt_path = os.path.join(output_dir, f"{base}_atsas_fit_failure.txt")
    with open(failure_txt_path, "w", encoding="utf-8") as fp:
        fp.write(message)
        fp.write("\n")

    fits_csv_path = os.path.join(output_dir, "fit_distances_fits.csv")
    _write_fits_csv(fits_csv_path, candidates)

    log_path = os.path.join(output_dir, f"{base}_fit_distances_log.yml")
    summary = {
        "profile": profile,
        "atsas_dat_path": atsas_dat_path,
        "atsas_fit_ok": False,
        "gnom_failed": True,
        "failure_reason": failure_reason,
        "failure_message": message,
        "fit_guinier": guinier_summary,
        "candidates": candidates,
        "failures": failures,
        "ensemble": _ensemble_block_for_log(None),
        "fits_csv_path": fits_csv_path,
        "failure_txt_path": failure_txt_path,
    }
    with open(log_path, "w") as f:
        yaml.dump(summary, f, default_flow_style=False)

    from autosaxs.core.report_fragments import write_skill_report_fragments

    md_body = (
        "### DATGNOM / p(r) (fit_distances)\n\n"
        f"**DATGNOM failed** — no valid p(r) was produced.\n\n"
        f"{message}\n"
    )
    write_skill_report_fragments(
        output_dir,
        base,
        "fit_distances",
        md_body,
        summary_references=[
            {"role": "fit_distances_failure", "path": os.path.basename(failure_txt_path), "format": "text"},
            {"role": "fit_distances_summary", "path": os.path.basename(log_path), "format": "text"},
        ],
        summary_extra={"atsas_fit_ok": False, "failure_reason": failure_reason},
    )

    quality = _empty_pr_quality_fields()
    if q_nm is not None:
        quality = _assess_and_write_pr_quality(
            output_dir=output_dir,
            base=base,
            out_text="",
            atsas_fit_ok=False,
            rg_guinier_nm=rg_guinier_nm,
            q_nm=np.asarray(q_nm, dtype=float),
            first_pt=first_pt,
            suspicious=False,
            event_bus=event_bus,
        )
        quality["user_tips"] = list(quality.get("user_tips") or []) + [
            f"DATGNOM failed ({failure_reason})."
        ]

    warn = f"fit_distances: {message}"
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": f"WARNING: {warn}"})
    else:
        print(f"WARNING: {warn}", file=sys.stderr)

    return {
        "output_subdir": output_dir,
        "atsas_fit_ok": False,
        "gnom_failed": True,
        "failure_reason": failure_reason,
        "failure_message": message,
        "gnom_out_paths": [],
        "best_gnom_out_path": "",
        "fit_distances_log_path": log_path,
        "fit_params_path": "",
        "best_symlink_out_path": "",
        "fits_csv_path": fits_csv_path,
        "failure_txt_path": failure_txt_path,
        "fit_vs_exp_png_path": "",
        "fit_vs_exp_png_error": message,
        "best_pr_png_path": "",
        "best_pr_png_error": message,
        "ensemble_dir": "",
        "ensemble_summary_path": "",
        "close_fit_out_paths": [],
        "force_zero_off_out_path": "",
        **_serialize_quality_for_return(quality),
    }



def write_success_artifacts(
    *,
    profile: str,
    base: str,
    output_dir: str,
    atsas_dat_path: str,
    best_gnom_out_path: str,
    gnom_out_paths: List[str],
    out_text: str,
    best: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    failures: List[Dict[str, Any]],
    guinier_summary: Optional[Dict[str, Any]],
    rg_trials: List[Dict[str, Any]],
    pr_quality: Dict[str, Any],
    ensemble_info: Dict[str, Any],
    user_rg_nm: Optional[float],
    user_first: Optional[int],
    user_last: Optional[int],
    user_smooth: Optional[float],
    event_bus: Optional[EventBus],
) -> Dict[str, Union[str, List[str]]]:
    """Persist success artifacts and return the skill result dict."""
    best_link_path = os.path.join(output_dir, f"{base}_gnom.out")
    try:
        if os.path.lexists(best_link_path):
            os.remove(best_link_path)
        rel_target = os.path.relpath(best_gnom_out_path, start=output_dir)
        os.symlink(rel_target, best_link_path)
    except OSError:
        pass

    fits_csv_path = os.path.join(output_dir, "fit_distances_fits.csv")
    _write_fits_csv(fits_csv_path, candidates)

    fit_vs_exp_png_path, fit_vs_exp_png_error = write_fit_vs_exp_png(
        out_text=out_text,
        output_dir=output_dir,
        base=base,
        best=best,
        event_bus=event_bus,
    )
    best_pr_png_path, best_pr_png_error = write_pr_png(
        best=best,
        pr_quality=pr_quality,
        close_fit_out_paths=list(ensemble_info.get("close_fit_out_paths") or []),
        force_zero_off_out_path=str(ensemble_info.get("force_zero_off_out_path") or ""),
        event_bus=event_bus,
    )

    fit_params_path = os.path.join(output_dir, f"{base}_fit_distances_fit_params.yml")
    fit_params_doc = {
        "rg_nm": float(best["rg_nm"]),
        "first": best.get("first"),
        "last": best.get("last"),
    }
    with open(fit_params_path, "w") as fp:
        yaml.dump(fit_params_doc, fp, default_flow_style=False)

    rg_param_src = "user" if user_rg_nm is not None else "rg_optimization"
    first_param_src = "user" if user_first is not None else "fit_guinier"
    last_param_src = "user" if user_last is not None else "omitted"
    smooth_param_src = "user" if user_smooth is not None else "default"

    # Attach validation summary onto ensemble_info for a complete log dump.
    ensemble_for_log = dict(ensemble_info)
    if ensemble_for_log.get("dmax_validation") is None and isinstance(pr_quality, dict):
        ensemble_for_log["dmax_validation"] = pr_quality.get("dmax_validation")

    log_path = os.path.join(output_dir, f"{base}_fit_distances_log.yml")
    summary = {
        "profile": profile,
        "atsas_dat_path": atsas_dat_path,
        "atsas_fit_ok": True,
        "unit_note": "Input profile assumed q in nm^-1; DATGNOM uses the same units, therefore Rg and r are in nm.",
        "fit_params_path": fit_params_path,
        "fit_param_sources": {
            "rg_nm": rg_param_src,
            "first": first_param_src,
            "last": last_param_src,
            "smooth": smooth_param_src,
        },
        "fit_guinier": guinier_summary,
        "rg_optimization_trials": rg_trials if user_rg_nm is None else None,
        "selected": {
            "rg_nm": float(best["rg_nm"]),
            "first": best.get("first"),
            "last": best.get("last"),
            "smooth": best.get("smooth"),
            "rmax_nm": best.get("rmax_nm"),
            "out_path": best_gnom_out_path,
            "suspicious": bool(best.get("suspicious")),
            "total_estimate": best.get("total_estimate"),
            "neg_frac": best.get("neg_frac"),
            "score": best.get("score"),
        },
        "candidates": candidates,
        "failures": failures,
        "best_symlink_out_path": best_link_path,
        "fits_csv_path": fits_csv_path,
        "fit_vs_exp_png_path": fit_vs_exp_png_path,
        "fit_vs_exp_png_error": fit_vs_exp_png_error,
        "best_pr_png_path": best_pr_png_path,
        "best_pr_png_error": best_pr_png_error,
        "ensemble": _ensemble_block_for_log(ensemble_for_log),
        "quality": pr_quality,
    }
    with open(log_path, "w") as f:
        yaml.dump(summary, f, default_flow_style=False)

    md_parts = ["### DATGNOM / p(r) (fit_distances)\n"]
    if fit_vs_exp_png_path and os.path.isfile(fit_vs_exp_png_path):
        md_parts.append(f"![Fit vs experiment]({os.path.basename(fit_vs_exp_png_path)})\n")
    if best_pr_png_path and os.path.isfile(best_pr_png_path):
        md_parts.append(f"![p(r)]({os.path.basename(best_pr_png_path)})\n")
    md_parts.append(_pr_quality_markdown(pr_quality))
    summary_refs: List[Dict[str, Any]] = [
        {"role": "fit_distances_summary", "path": os.path.basename(log_path), "format": "text"},
        {
            "role": "fit_distances_scores",
            "path": os.path.basename(fits_csv_path),
            "format": "csv",
            "row": 0,
            "columns": ["rmax_nm", "total_estimate", "ok"],
        },
    ]
    ens_sum = ensemble_info.get("ensemble_summary_path") or ""
    if ens_sum and os.path.isfile(ens_sum):
        summary_refs.append(
            {
                "role": "fit_distances_ensemble",
                "path": os.path.relpath(ens_sum, start=output_dir),
                "format": "csv",
            }
        )
    write_skill_report_fragments(
        output_dir,
        base,
        "fit_distances",
        "".join(md_parts),
        summary_references=summary_refs,
        summary_extra={
            "pr_quality_class": pr_quality.get("pr_quality_class"),
            "overall_status": pr_quality.get("overall_status"),
        },
    )

    return {
        "output_subdir": output_dir,
        "atsas_fit_ok": True,
        "gnom_out_paths": gnom_out_paths,
        "best_gnom_out_path": best_gnom_out_path,
        "fit_distances_log_path": log_path,
        "fit_params_path": fit_params_path,
        "best_symlink_out_path": best_link_path,
        "fits_csv_path": fits_csv_path,
        "fit_vs_exp_png_path": fit_vs_exp_png_path or "",
        "fit_vs_exp_png_error": fit_vs_exp_png_error or "",
        "best_pr_png_path": best_pr_png_path or "",
        "best_pr_png_error": best_pr_png_error or "",
        "ensemble_dir": ensemble_info.get("ensemble_dir") or "",
        "ensemble_summary_path": ensemble_info.get("ensemble_summary_path") or "",
        "close_fit_out_paths": list(ensemble_info.get("close_fit_out_paths") or []),
        "force_zero_off_out_path": ensemble_info.get("force_zero_off_out_path") or "",
        **_serialize_quality_for_return(pr_quality),
    }
