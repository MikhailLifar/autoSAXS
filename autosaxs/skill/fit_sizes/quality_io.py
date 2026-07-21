"""Quality passport helpers for the fit_sizes skill."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Union

import numpy as np

from autosaxs.core.gnom import parse_gnom_out
from autosaxs.core.gnom_quality import analyze_dr_quality

from ..deps import EventBus, EventType


# Keys that must stay list-valued after apply_batch single-sample merge.
_FIT_SIZES_LIST_RESULT_KEYS = frozenset(
    {
        "gnom_out_paths",
        "close_fit_out_paths",
        "quality_rationale",
        "user_tips",
        "dr_peak_positions_nm",
    }
)


def normalize_fit_sizes_single_sample(
    result: Dict[str, Union[str, List[str], float, int]],
) -> Dict[str, Union[str, List[str], float, int]]:
    """
    Unwrap single-element lists left by ``apply_batch`` for scalar metadata.

    ``apply_batch`` only auto-unwraps string paths for single-sample calls; numeric
    and boolean fields remain as one-element lists unless corrected here.
    """
    out: Dict[str, Union[str, List[str], float, int]] = {}
    for key, value in result.items():
        if key in _FIT_SIZES_LIST_RESULT_KEYS:
            out[key] = value
        elif isinstance(value, list) and len(value) == 1:
            out[key] = value[0]
        else:
            out[key] = value
    return out


def _dr_quality_result_keys() -> List[str]:
    return [
        "d_avg_nm",
        "d_std_nm",
        "pdi",
        "dr_peak_positions_nm",
        "dr_n_peaks",
        "modality_class",
        "rg_guinier_nm",
        "dmax_nm",
        "q_min_fit_nm",
        "total_estimate",
        "shannon_s_min",
        "shannon_class",
        "shannon_ok",
        "shannon_tip",
        "sizes_quality_class",
        "overall_status",
        "quality_rationale",
        "user_tips",
        "fit_sizes_path",
        "parametric_family",
        "parametric_R0_nm",
        "parametric_width_nm",
        "n_components_suggested",
        "mixture_dist_hint",
        "modality_confidence",
        "stability_class",
        "ensemble_dir",
        "ensemble_summary_path",
        "force_zero_off_out_path",
        "force_zero_off_pathology",
    ]


def _empty_dr_quality_fields() -> Dict[str, Any]:
    return {
        "d_avg_nm": None,
        "d_std_nm": None,
        "pdi": None,
        "dr_peak_positions_nm": [],
        "dr_n_peaks": 0,
        "modality_class": "unknown",
        "rg_guinier_nm": None,
        "dmax_nm": None,
        "q_min_fit_nm": None,
        "total_estimate": None,
        "shannon_s_min": None,
        "shannon_class": "unknown",
        "shannon_ok": None,
        "shannon_tip": "",
        "sizes_quality_class": "failed",
        "overall_status": "FAILED",
        "quality_rationale": [],
        "user_tips": [],
        "fit_sizes_path": "",
        "parametric_family": "unknown",
        "parametric_R0_nm": None,
        "parametric_width_nm": None,
        "n_components_suggested": 1,
        "mixture_dist_hint": "Schultz",
        "modality_confidence": "low",
        "stability_class": "unknown",
        "ensemble_dir": "",
        "ensemble_summary_path": "",
        "force_zero_off_out_path": "",
        "force_zero_off_pathology": False,
    }


def _serialize_dr_quality_for_return(quality: Dict[str, Any]) -> Dict[str, Union[str, List[str], float]]:
    out: Dict[str, Union[str, List[str], float]] = {}
    for key in _dr_quality_result_keys():
        val = quality.get(key)
        if key in ("quality_rationale", "user_tips"):
            out[key] = [str(x) for x in (val or [])]
        elif key == "dr_peak_positions_nm":
            out[key] = [str(float(x)) for x in (val or [])]
        elif key == "dr_n_peaks":
            out[key] = int(val or 0)
        elif key == "n_components_suggested":
            out[key] = int(val or 1)
        elif key in ("shannon_ok", "force_zero_off_pathology"):
            if val is None:
                out[key] = ""
            else:
                out[key] = "true" if bool(val) else "false"
        elif isinstance(val, (list, dict)):
            continue
        elif val is None:
            out[key] = ""
        elif isinstance(val, (int, float)):
            out[key] = float(val)
        else:
            out[key] = str(val)
    return out


def _assess_dr_quality(
    *,
    out_text: str,
    atsas_fit_ok: bool,
    rg_guinier_nm: Optional[float],
    shape: str,
    neg_frac: Optional[float],
    event_bus: Optional[EventBus],
    q_nm: Optional[np.ndarray] = None,
    first_pt_1based: Optional[int] = None,
) -> Dict[str, Any]:
    parsed = parse_gnom_out(out_text)
    quality = analyze_dr_quality(
        parsed,
        atsas_fit_ok=atsas_fit_ok,
        rg_guinier_nm=rg_guinier_nm,
        shape=shape,
        neg_frac=neg_frac,
        q_nm=q_nm,
        first_pt_1based=first_pt_1based,
    )
    if event_bus and quality.get("user_tips"):
        for tip in quality["user_tips"][:5]:
            event_bus.publish(EventType.MESSAGE, {"text": f"fit_sizes quality: {tip}"})
    return quality


def _assess_and_write_dr_quality(
    *,
    output_dir: str,
    base: str,
    out_text: str,
    atsas_fit_ok: bool,
    rg_guinier_nm: Optional[float],
    shape: str,
    neg_frac: Optional[float],
    event_bus: Optional[EventBus],
    q_nm: Optional[np.ndarray] = None,
    first_pt_1based: Optional[int] = None,
) -> Dict[str, Any]:
    _ = output_dir, base
    return _assess_dr_quality(
        out_text=out_text,
        atsas_fit_ok=atsas_fit_ok,
        rg_guinier_nm=rg_guinier_nm,
        shape=shape,
        neg_frac=neg_frac,
        event_bus=event_bus,
        q_nm=q_nm,
        first_pt_1based=first_pt_1based,
    )


def _write_dr_quality_passport(
    *,
    output_dir: str,
    base: str,
    quality: Dict[str, Any],
    event_bus: Optional[EventBus],
) -> Dict[str, Any]:
    _ = output_dir, base, event_bus
    return quality


def _dr_quality_markdown(quality: Dict[str, Any]) -> str:
    """Sample metrics only — no general classification-rule text in the report body."""
    lines = [
        "\n#### Quality assessment (D(R))\n",
        f"- **Status:** {quality.get('overall_status', 'FAILED')}",
        f"\n- **Modality:** {quality.get('modality_class', 'unknown')}",
    ]
    te = quality.get("total_estimate")
    if te is not None:
        lines.append(f"\n- **Total Estimate:** {float(te):.3f}")
    s_min = quality.get("shannon_s_min")
    if s_min is not None:
        lines.append(
            f"\n- **Shannon s_min:** {float(s_min):.3f} ({quality.get('shannon_class', 'unknown')})"
        )
    pdi = quality.get("pdi")
    if pdi is not None:
        lines.append(f"\n- **PDI:** {float(pdi):.3f}")
    d_avg = quality.get("d_avg_nm")
    d_std = quality.get("d_std_nm")
    if d_avg is not None:
        if d_std is not None:
            lines.append(f"\n- **⟨R⟩:** {float(d_avg):.3g} ± {float(d_std):.3g} nm")
        else:
            lines.append(f"\n- **⟨R⟩:** {float(d_avg):.3g} nm")
    fam = quality.get("parametric_family")
    if fam and fam != "unknown":
        lines.append(f"\n- **Parametric hint:** {fam}")
    n_ph = quality.get("n_components_suggested")
    dist = quality.get("mixture_dist_hint")
    if n_ph is not None and dist:
        lines.append(f"\n- **model_mixture hint:** {dist}, {int(n_ph)} phase(s)")
    stab = quality.get("stability_class")
    if stab and stab != "unknown":
        lines.append(f"\n- **Rmax stability:** {stab}")
    return "".join(lines) + "\n"
