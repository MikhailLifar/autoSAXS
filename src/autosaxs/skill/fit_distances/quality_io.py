"""Quality passport helpers for the fit_distances skill."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Union

import numpy as np

from autosaxs.core.gnom import parse_gnom_out
from autosaxs.core.gnom_quality import analyze_pr_quality, write_quality_passport_yaml

from ..deps import EventBus, EventType


def _pr_quality_result_keys() -> List[str]:
    return [
        "dmax_nm",
        "rg_pr_nm",
        "i0_pr",
        "rg_guinier_nm",
        "q_min_fit_nm",
        "total_estimate",
        "delta_rg_pct",
        "shannon_s_min",
        "shannon_class",
        "shannon_ok",
        "shannon_tip",
        "pr_quality_class",
        "overall_status",
        "quality_rationale",
        "user_tips",
        "quality_passport_path",
    ]


def _empty_pr_quality_fields() -> Dict[str, Any]:
    return {
        "dmax_nm": None,
        "rg_pr_nm": None,
        "i0_pr": None,
        "rg_guinier_nm": None,
        "q_min_fit_nm": None,
        "total_estimate": None,
        "delta_rg_pct": None,
        "shannon_s_min": None,
        "shannon_class": "unknown",
        "shannon_ok": None,
        "shannon_tip": "",
        "pr_quality_class": "failed",
        "overall_status": "FAILED",
        "quality_rationale": [],
        "user_tips": [],
        "quality_passport_path": "",
    }


def _serialize_quality_for_return(quality: Dict[str, Any]) -> Dict[str, Union[str, List[str], float]]:
    out: Dict[str, Union[str, List[str], float]] = {}
    for key in _pr_quality_result_keys():
        val = quality.get(key)
        if key in ("quality_rationale", "user_tips"):
            out[key] = [str(x) for x in (val or [])]
        elif key == "shannon_ok":
            if val is None:
                out[key] = ""
            else:
                out[key] = "true" if val else "false"
        elif isinstance(val, (list, dict)):
            continue
        elif val is None:
            out[key] = ""
        elif isinstance(val, (int, float)):
            out[key] = float(val)
        else:
            out[key] = str(val)
    return out


def _assess_and_write_pr_quality(
    *,
    output_dir: str,
    base: str,
    out_text: str,
    atsas_fit_ok: bool,
    rg_guinier_nm: Optional[float],
    q_nm: np.ndarray,
    first_pt: Optional[int],
    suspicious: bool,
    event_bus: Optional[EventBus],
    dmax_validation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    parsed = parse_gnom_out(out_text)
    quality = analyze_pr_quality(
        parsed,
        atsas_fit_ok=atsas_fit_ok,
        rg_guinier_nm=rg_guinier_nm,
        q_nm=q_nm,
        first_pt_1based=first_pt,
        suspicious=suspicious,
        dmax_validation=dmax_validation,
    )
    passport_path = os.path.join(output_dir, f"{base}_fit_distances_quality.yml")
    passport_doc = {
        "pipeline_step": "6-7",
        "overall_status": quality["overall_status"],
        **quality,
    }
    write_quality_passport_yaml(passport_path, passport_doc)
    quality["quality_passport_path"] = passport_path
    if event_bus and quality.get("user_tips"):
        for tip in quality["user_tips"][:5]:
            event_bus.publish(EventType.MESSAGE, {"text": f"fit_distances quality: {tip}"})
    return quality


def _pr_quality_markdown(quality: Dict[str, Any]) -> str:
    """Sample metrics only — no general classification-rule text in the report body."""
    lines = [
        "\n#### Quality assessment (p(r))\n",
        f"- **Status:** {quality.get('overall_status', 'FAILED')}",
    ]
    te = quality.get("total_estimate")
    if te is not None:
        lines.append(f"\n- **Total Estimate:** {float(te):.3f}")
    drg = quality.get("delta_rg_pct")
    if drg is not None:
        lines.append(f"\n- **ΔRg:** {float(drg):.1f}%")
    s_min = quality.get("shannon_s_min")
    if s_min is not None:
        lines.append(
            f"\n- **Shannon s_min:** {float(s_min):.3f} ({quality.get('shannon_class', 'unknown')})"
        )
    rg_g = quality.get("rg_guinier_nm")
    rg_p = quality.get("rg_pr_nm")
    if rg_g is not None:
        lines.append(f"\n- **Rg (Guinier):** {float(rg_g):.4g} nm")
    if rg_p is not None:
        lines.append(f"\n- **Rg (p(r)):** {float(rg_p):.4g} nm")
    dmax = quality.get("dmax_nm")
    if dmax is not None:
        lines.append(f"\n- **D_max:** {float(dmax):.4g} nm")
    return "".join(lines) + "\n"


def _pr_metrics(r: np.ndarray, p: np.ndarray) -> Dict[str, Any]:
    """
    Compute a few descriptive metrics for p(r) without defining any composite score.
    """
    r = np.asarray(r, dtype=float)
    p = np.asarray(p, dtype=float)
    out: Dict[str, Any] = {}
    if r.size == 0 or p.size == 0 or r.size != p.size:
        return out
    if not np.any(np.isfinite(p)):
        return out
    i = int(np.nanargmax(p))
    peak_r = float(r[i])
    peak_p = float(p[i])
    out.update({"peak_r": peak_r, "peak_p": peak_p})
    if not np.isfinite(peak_p) or peak_p <= 0:
        return out
    half = peak_p / 2.0
    left_idx = np.where(p[:i] <= half)[0]
    right_idx = np.where(p[i:] <= half)[0]
    if left_idx.size > 0 and right_idx.size > 0:
        out["fwhm"] = float(r[i + int(right_idx[0])] - r[int(left_idx[-1])])
    return out
