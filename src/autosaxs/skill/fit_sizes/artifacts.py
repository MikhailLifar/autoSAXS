"""Artifact writers for the fit_sizes skill (YAML/reports/failure)."""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional, Union

from autosaxs.core.report_fragments import write_skill_report_fragments

from ..deps import EventBus, EventType
from ..gnom_fit_common import default_atsas_failure_message
from .quality_io import (
    _assess_dr_quality,
    _dr_quality_markdown,
    _serialize_dr_quality_for_return,
)
from .vis import write_dr_png, write_fit_vs_exp_png
from .yaml_io import write_fit_sizes_handoff, write_fit_sizes_log


def _finalize_fit_sizes_failure(
    *,
    output_dir: str,
    profile: str,
    base: str,
    atsas_dat_path: str,
    shape: str,
    system: int,
    failure_reason: str,
    failures: List[Dict[str, Any]],
    candidates: List[Dict[str, Any]],
    guinier_summary: Optional[Dict[str, Any]],
    event_bus: Optional[EventBus],
    detail: str = "",
    rg_guinier_nm: Optional[float] = None,
) -> Dict[str, Union[str, List[str]]]:
    os.makedirs(output_dir, exist_ok=True)
    message = default_atsas_failure_message("fit_sizes")
    if detail.strip():
        message = f"{message}\n\nLast error: {detail.strip()[:1500]}"

    failure_txt_path = os.path.join(output_dir, f"{base}_atsas_fit_failure.txt")
    with open(failure_txt_path, "w", encoding="utf-8") as fp:
        fp.write(message)
        fp.write("\n")

    quality = _assess_dr_quality(
        out_text="",
        atsas_fit_ok=False,
        rg_guinier_nm=rg_guinier_nm,
        shape=shape,
        neg_frac=None,
        event_bus=event_bus,
        q_nm=None,
        first_pt_1based=None,
    )
    quality["user_tips"] = list(quality.get("user_tips") or []) + [
        f"GNOM failed ({failure_reason})."
    ]

    log_path = write_fit_sizes_log(
        output_dir=output_dir,
        base=base,
        profile=profile,
        atsas_dat_path=atsas_dat_path,
        shape=shape,
        system=system,
        atsas_fit_ok=False,
        guinier_summary=guinier_summary,
        rmax_trials=[],
        user_rmax_nm=None,
        user_first=None,
        candidates=candidates,
        failures=failures,
        best={},
        ensemble_info={},
        dr_quality=quality,
        fit_vs_exp_png_path=None,
        best_dr_png_path=None,
        failure_reason=failure_reason,
        failure_message=message,
    )
    handoff_path = write_fit_sizes_handoff(
        output_dir=output_dir,
        base=base,
        profile=profile,
        shape=shape,
        system=system,
        best={},
        best_rmax_nm=0.0,
        first_pt=0,
        last_pt=None,
        rmin_nm=None,
        rad56_nm=None,
        alpha=None,
        nr=None,
        user_rg_nm=None,
        dr_quality=quality,
        best_gnom_out_path="",
        fit_vs_exp_png_path=None,
        best_dr_png_path=None,
        log_path=log_path,
        ensemble_summary_path="",
    )
    quality["fit_sizes_path"] = handoff_path

    md_body = (
        f"### GNOM size distribution (fit_sizes, shape={shape})\n\n"
        f"**GNOM failed** — no valid D(R) was produced.\n\n"
        f"{message}\n"
    )
    write_skill_report_fragments(
        output_dir,
        base,
        "fit_sizes",
        md_body,
        summary_references=[
            {"role": "fit_sizes_failure", "path": os.path.basename(failure_txt_path), "format": "text"},
            {"role": "fit_sizes_handoff", "path": os.path.basename(handoff_path), "format": "text"},
            {"role": "fit_sizes_log", "path": os.path.basename(log_path), "format": "text"},
        ],
        summary_extra={"atsas_fit_ok": False, "failure_reason": failure_reason},
    )

    warn = f"fit_sizes: {message}"
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
        "fit_sizes_path": handoff_path,
        "fit_sizes_log_path": log_path,
        "best_summary_path": log_path,
        "fit_params_path": handoff_path,
        "fit_sizes_hints_path": handoff_path,
        "quality_passport_path": handoff_path,
        "best_symlink_out_path": "",
        "failure_txt_path": failure_txt_path,
        "fit_vs_exp_png_path": "",
        "fit_vs_exp_png_error": message,
        "best_dr_png_path": "",
        "best_dr_png_error": message,
        **_serialize_dr_quality_for_return(quality),
    }


def write_success_artifacts(
    *,
    profile: str,
    base: str,
    output_dir: str,
    atsas_dat_path: str,
    shape: str,
    system: int,
    best_gnom_out_path: str,
    gnom_out_paths: List[str],
    best: Dict[str, Any],
    candidates: List[Dict[str, Any]],
    failures: List[Dict[str, Any]],
    guinier_summary: Optional[Dict[str, Any]],
    rmax_trials: List[Dict[str, Any]],
    dr_quality: Dict[str, Any],
    user_rg_nm: Optional[float],
    user_first: Optional[int],
    user_rmax_nm: Optional[float],
    best_rmax_nm: float,
    first_pt: int,
    last_pt: Optional[int],
    rmin_nm: Optional[float],
    rad56_nm: Optional[float],
    alpha: Optional[float],
    nr: Optional[int],
    ensemble_info: Optional[Dict[str, Any]] = None,
    event_bus: Optional[EventBus],
) -> Dict[str, Union[str, List[str]]]:
    """Persist success artifacts and return the skill result dict."""
    ensemble_info = ensemble_info or {}
    close_fit_out_paths = list(ensemble_info.get("close_fit_out_paths") or [])
    force_zero_off_out_path = str(ensemble_info.get("force_zero_off_out_path") or "")
    best_link_path = os.path.join(output_dir, f"{base}_gnom_sizes.out")
    try:
        if os.path.lexists(best_link_path):
            os.remove(best_link_path)
        rel_target = os.path.relpath(best_gnom_out_path, start=output_dir)
        os.symlink(rel_target, best_link_path)
    except OSError:
        pass

    fit_vs_exp_png_path, fit_vs_exp_png_error = write_fit_vs_exp_png(
        best_gnom_out_path=best_gnom_out_path,
        output_dir=output_dir,
        base=base,
        system=system,
        best=best,
        event_bus=event_bus,
    )
    best_dr_png_path, best_dr_png_error = write_dr_png(
        best=best,
        dr_quality=dr_quality,
        system=system,
        close_fit_out_paths=close_fit_out_paths,
        force_zero_off_out_path=force_zero_off_out_path,
        event_bus=event_bus,
    )

    ensemble_summary_path = str(ensemble_info.get("ensemble_summary_path") or "")
    log_path = write_fit_sizes_log(
        output_dir=output_dir,
        base=base,
        profile=profile,
        atsas_dat_path=atsas_dat_path,
        shape=shape,
        system=system,
        atsas_fit_ok=True,
        guinier_summary=guinier_summary,
        rmax_trials=rmax_trials,
        user_rmax_nm=user_rmax_nm,
        user_first=user_first,
        candidates=candidates,
        failures=failures,
        best=best,
        ensemble_info=ensemble_info,
        dr_quality=dr_quality,
        fit_vs_exp_png_path=fit_vs_exp_png_path,
        best_dr_png_path=best_dr_png_path,
    )
    handoff_path = write_fit_sizes_handoff(
        output_dir=output_dir,
        base=base,
        profile=profile,
        shape=shape,
        system=system,
        best=best,
        best_rmax_nm=best_rmax_nm,
        first_pt=first_pt,
        last_pt=last_pt,
        rmin_nm=rmin_nm,
        rad56_nm=rad56_nm,
        alpha=alpha,
        nr=nr,
        user_rg_nm=user_rg_nm,
        dr_quality=dr_quality,
        best_gnom_out_path=best_gnom_out_path,
        fit_vs_exp_png_path=fit_vs_exp_png_path,
        best_dr_png_path=best_dr_png_path,
        log_path=log_path,
        ensemble_summary_path=ensemble_summary_path,
    )
    dr_quality["fit_sizes_path"] = handoff_path

    md_parts = [f"### GNOM size distribution (fit_sizes, shape={shape})\n"]
    if fit_vs_exp_png_path and os.path.isfile(fit_vs_exp_png_path):
        md_parts.append(f"![Selected GNOM fit vs data]({os.path.basename(fit_vs_exp_png_path)})\n")
    if best_dr_png_path and os.path.isfile(best_dr_png_path):
        md_parts.append(f"![D(R)]({os.path.basename(best_dr_png_path)})\n")
    md_parts.append(_dr_quality_markdown(dr_quality))
    summary_refs = [
        {"role": "fit_sizes_handoff", "path": os.path.basename(handoff_path), "format": "text"},
        {"role": "fit_sizes_log", "path": os.path.basename(log_path), "format": "text"},
    ]
    if ens_sum := ensemble_summary_path:
        if os.path.isfile(ens_sum):
            summary_refs.append(
                {
                    "role": "fit_sizes_ensemble",
                    "path": os.path.relpath(ens_sum, start=output_dir),
                    "format": "csv",
                },
            )
    write_skill_report_fragments(
        output_dir,
        base,
        "fit_sizes",
        "".join(md_parts),
        summary_references=summary_refs,
        summary_extra={
            "sizes_quality_class": dr_quality.get("sizes_quality_class"),
            "overall_status": dr_quality.get("overall_status"),
            "modality_class": dr_quality.get("modality_class"),
        },
    )

    return {
        "output_subdir": output_dir,
        "atsas_fit_ok": True,
        "gnom_out_paths": gnom_out_paths,
        "best_gnom_out_path": best_gnom_out_path,
        "fit_sizes_path": handoff_path,
        "fit_sizes_log_path": log_path,
        "best_summary_path": log_path,
        "fit_params_path": handoff_path,
        "fit_sizes_hints_path": handoff_path,
        "quality_passport_path": handoff_path,
        "best_symlink_out_path": best_link_path,
        "fit_vs_exp_png_path": fit_vs_exp_png_path or "",
        "fit_vs_exp_png_error": fit_vs_exp_png_error or "",
        "best_dr_png_path": best_dr_png_path or "",
        "best_dr_png_error": best_dr_png_error or "",
        "ensemble_dir": ensemble_info.get("ensemble_dir") or "",
        "ensemble_summary_path": ensemble_summary_path,
        "close_fit_out_paths": close_fit_out_paths,
        "force_zero_off_out_path": force_zero_off_out_path,
        **_serialize_dr_quality_for_return(dr_quality),
    }
