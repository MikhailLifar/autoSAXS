"""YAML writers for fit_sizes handoff and run logs."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import numpy as np
import yaml

from autosaxs.core.utils import _make_yaml_safe


def dump_yaml(path: str, doc: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as fp:
        yaml.dump(
            _make_yaml_safe(doc),
            fp,
            default_flow_style=False,
            allow_unicode=True,
        )


def _quality_handoff_block(dr_quality: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "sizes_quality_class": dr_quality.get("sizes_quality_class"),
        "overall_status": dr_quality.get("overall_status"),
        "total_estimate": dr_quality.get("total_estimate"),
        "d_avg_nm": dr_quality.get("d_avg_nm"),
        "d_std_nm": dr_quality.get("d_std_nm"),
        "pdi": dr_quality.get("pdi"),
        "modality_class": dr_quality.get("modality_class"),
        "modality_confidence": dr_quality.get("modality_confidence"),
        "dr_n_peaks": dr_quality.get("dr_n_peaks"),
        "dr_peak_positions_nm": list(dr_quality.get("dr_peak_positions_nm") or []),
        "rg_guinier_nm": dr_quality.get("rg_guinier_nm"),
        "shannon_s_min": dr_quality.get("shannon_s_min"),
        "shannon_class": dr_quality.get("shannon_class"),
        "stability_class": dr_quality.get("stability_class"),
        "force_zero_off_pathology": bool(dr_quality.get("force_zero_off_pathology")),
        "quality_rationale": list(dr_quality.get("quality_rationale") or []),
        "user_tips": list(dr_quality.get("user_tips") or []),
    }


def _analysis_handoff_block(dr_quality: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "parametric_family": dr_quality.get("parametric_family"),
        "parametric_aic": dr_quality.get("parametric_aic"),
        "parametric_R0_nm": dr_quality.get("parametric_R0_nm"),
        "parametric_width_nm": dr_quality.get("parametric_width_nm"),
        "n_phases_suggested": int(dr_quality.get("n_components_suggested") or 1),
        "mixture_dist_hint": dr_quality.get("mixture_dist_hint"),
    }


def _model_mixture_handoff_block(*, best_rmax_nm: float, dr_quality: Dict[str, Any]) -> Dict[str, Any]:
    width = dr_quality.get("parametric_width_nm") or dr_quality.get("d_std_nm")
    try:
        width_f = float(width) if width is not None else None
    except (TypeError, ValueError):
        width_f = None
    poly_max_nm = 0.5 * float(best_rmax_nm)
    if width_f is not None and np.isfinite(width_f):
        poly_max_nm = max(poly_max_nm, min(2.0 * width_f, float(best_rmax_nm)))
    return {
        "r_max_nm": float(best_rmax_nm),
        "r_min_nm": 0.1,
        "poly_min_nm": 0.05,
        "poly_max_nm": float(poly_max_nm),
        "n_phases_suggested": int(dr_quality.get("n_components_suggested") or 1),
        "mixture_dist_hint": dr_quality.get("mixture_dist_hint") or "Schultz",
        "parametric_family": dr_quality.get("parametric_family") or "unknown",
        "modality_class": dr_quality.get("modality_class") or "unknown",
        "modality_confidence": dr_quality.get("modality_confidence") or "low",
        "stability_class": dr_quality.get("stability_class") or "unknown",
        "force_zero_off_pathology": bool(dr_quality.get("force_zero_off_pathology")),
        "peak_positions_nm": list(dr_quality.get("dr_peak_positions_nm") or []),
        "sizes_quality_class": dr_quality.get("sizes_quality_class") or "failed",
    }


def write_fit_sizes_handoff(
    *,
    output_dir: str,
    base: str,
    profile: str,
    shape: str,
    system: int,
    best: Dict[str, Any],
    best_rmax_nm: float,
    first_pt: int,
    last_pt: Optional[int],
    rmin_nm: Optional[float],
    rad56_nm: Optional[float],
    alpha: Optional[float],
    nr: Optional[int],
    user_rg_nm: Optional[float],
    dr_quality: Dict[str, Any],
    best_gnom_out_path: str,
    fit_vs_exp_png_path: Optional[str],
    best_dr_png_path: Optional[str],
    log_path: str,
    ensemble_summary_path: str,
) -> str:
    """Write compact handoff YAML: best fit, quality, analysis, model_mixture hints."""
    fit: Dict[str, Any] = {
        "shape": shape,
        "system": int(system),
        "rmin_nm": rmin_nm,
        "rmax_nm": float(best_rmax_nm),
        "rad56_nm": rad56_nm,
        "first": first_pt,
        "last": last_pt,
        "alpha": alpha,
        "nr": nr,
        "total_estimate": best.get("total_estimate"),
        "neg_frac": best.get("neg_frac"),
        "score": best.get("score"),
        "suspicious": bool(best.get("suspicious")),
        "out_path": os.path.basename(best_gnom_out_path),
    }
    if user_rg_nm is not None:
        fit["rg_nm"] = float(user_rg_nm)

    handoff_path = os.path.join(output_dir, f"{base}_fit_sizes.yml")
    dump_yaml(
        handoff_path,
        {
            "profile": profile,
            "shape": shape,
            "system": int(system),
            "fit": fit,
            "quality": _quality_handoff_block(dr_quality),
            "analysis": _analysis_handoff_block(dr_quality),
            "model_mixture": _model_mixture_handoff_block(
                best_rmax_nm=best_rmax_nm,
                dr_quality=dr_quality,
            ),
            "artifacts": {
                "best_gnom_out": os.path.basename(best_gnom_out_path),
                "best_dr_png": os.path.basename(best_dr_png_path) if best_dr_png_path else "",
                "fit_vs_exp_png": os.path.basename(fit_vs_exp_png_path) if fit_vs_exp_png_path else "",
                "log": os.path.basename(log_path),
                "ensemble_summary": (
                    os.path.relpath(ensemble_summary_path, start=output_dir)
                    if ensemble_summary_path
                    else ""
                ),
            },
        },
    )
    return handoff_path


def write_fit_sizes_log(
    *,
    output_dir: str,
    base: str,
    profile: str,
    atsas_dat_path: str,
    shape: str,
    system: int,
    atsas_fit_ok: bool,
    guinier_summary: Optional[Dict[str, Any]],
    rmax_trials: List[Dict[str, Any]],
    user_rmax_nm: Optional[float],
    user_first: Optional[int],
    candidates: List[Dict[str, Any]],
    failures: List[Dict[str, Any]],
    best: Dict[str, Any],
    ensemble_info: Dict[str, Any],
    dr_quality: Dict[str, Any],
    fit_vs_exp_png_path: Optional[str],
    best_dr_png_path: Optional[str],
    failure_reason: str = "",
    failure_message: str = "",
) -> str:
    """Write extended run log YAML with candidate and ensemble details."""
    rmax_param_src = "user" if user_rmax_nm is not None else "rmax_optimization"
    first_param_src = "user" if user_first is not None else "fit_guinier"
    log_path = os.path.join(output_dir, f"{base}_fit_sizes_log.yml")
    dump_yaml(
        log_path,
        {
            "profile": profile,
            "atsas_dat_path": atsas_dat_path,
            "shape": shape,
            "system": int(system),
            "atsas_fit_ok": bool(atsas_fit_ok),
            "failure_reason": failure_reason or None,
            "failure_message": failure_message or None,
            "unit_note": (
                "Input profile assumed q in nm^-1; GNOM uses the same units on the command line, "
                "therefore R is in nm."
            ),
            "fit_param_sources": {
                "rmax_nm": rmax_param_src,
                "first": first_param_src,
            },
            "fit_guinier": guinier_summary,
            "rmax_optimization_trials": rmax_trials if user_rmax_nm is None else None,
            "selected": best,
            "candidates": candidates,
            "failures": failures,
            "ensemble": {
                "dir": ensemble_info.get("ensemble_dir") or "",
                "summary_path": ensemble_info.get("ensemble_summary_path") or "",
                "close_fit_out_paths": list(ensemble_info.get("close_fit_out_paths") or []),
                "force_zero_off_out_path": ensemble_info.get("force_zero_off_out_path") or "",
                "rows": list(ensemble_info.get("ensemble_rows") or []),
                "rmax_validation": ensemble_info.get("rmax_validation"),
            },
            "quality": dr_quality,
            "plots": {
                "fit_vs_exp_png": fit_vs_exp_png_path or "",
                "best_dr_png": best_dr_png_path or "",
            },
        },
    )
    return log_path
