"""Passport / diagnostics formatting for polydisperse D(R) pane and adjust wizard."""

from __future__ import annotations

import math
from typing import Any, Mapping

from guisaxs_skills.ui.passport_format import (
    format_display_number,
    format_metric_label,
    format_value_with_class,
    is_overall_status_poor,
    scalar_value,
    shannon_row_poor,
)


def format_sizes_passport_rows(
    result: Mapping[str, Any],
    *,
    compact: bool = False,
) -> list[tuple[str, str, bool]]:
    """
    D(R) passport rows as ``(metric, value, poor)``.

    ``compact`` omits wiggle index, s_max, and I(0)-style extras (analysis-pane preview).
    Numeric metrics stay numeric; overall status is a separate Status row.
    """
    from autosaxs.core.gnom_quality import DrQualityThresholds

    t = DrQualityThresholds()
    rows: list[tuple[str, str, bool]] = []

    status = str(scalar_value(result.get("overall_status")) or "").strip()
    sizes_class = str(scalar_value(result.get("sizes_quality_class")) or "").strip()
    if status:
        rows.append(("Status", status, is_overall_status_poor(status)))
    elif sizes_class:
        rows.append(
            ("Status", sizes_class, sizes_class.lower() in ("failed", "fail", "acceptable"))
        )

    te = scalar_value(result.get("total_estimate"))
    if te is not None and te not in ("", None):
        te_poor = False
        try:
            te_poor = float(te) < float(t.total_estimate_min)
        except (TypeError, ValueError):
            te_poor = False
        rows.append(("Total est.", format_display_number(te), te_poor))

    chi2 = scalar_value(result.get("chi2"))
    chi2_class = str(scalar_value(result.get("chi2_class")) or "").strip()
    if chi2 is not None and chi2 not in ("", None):
        if not chi2_class or chi2_class == "unknown":
            from autosaxs.core.gnom_quality import classify_chi2

            chi2_class = classify_chi2(
                float(chi2) if chi2 is not None else None,
                good_min=t.chi2_good_min,
                good_max=t.chi2_good_max,
                acceptable_min=t.chi2_acceptable_min,
                acceptable_max=t.chi2_acceptable_max,
            )
        chi2_label = {
            "high_quality": "good",
            "acceptable": "acceptable",
            "failed": "failed",
        }.get(chi2_class.lower(), chi2_class or "—")
        chi2_poor = chi2_class.lower() in ("failed", "fail")
        rows.append(("χ²", format_value_with_class(chi2, chi2_label), chi2_poor))

    det = str(scalar_value(result.get("detail_reliability_class")) or "").strip()
    if det and det.lower() != "unknown":
        det_poor = det.upper() == "SUSPICIOUS"
        rows.append(("Detail reliability", det, det_poor))

    n_s = scalar_value(result.get("n_shannon"))
    if n_s is not None and n_s not in ("", None):
        try:
            n_s = math.floor(float(n_s))
        except (TypeError, ValueError):
            pass
        rows.append(("n_shannon", format_display_number(n_s), False))

    if not compact:
        s_max = scalar_value(result.get("shannon_s_max"))
        if s_max is not None and s_max not in ("", None):
            rows.append(("s_max", format_display_number(s_max), False))

        wig = scalar_value(result.get("wiggle_index"))
        wig_class = str(scalar_value(result.get("wiggle_class")) or "unknown")
        if wig is not None and wig not in ("", None):
            wig_poor = wig_class.lower() == "high"
            rows.append(
                (
                    "wiggle_index",
                    format_value_with_class(wig, wig_class),
                    wig_poor,
                )
            )

    s_min = scalar_value(result.get("shannon_s_min"))
    s_class = str(scalar_value(result.get("shannon_class")) or "unknown")
    if s_min is not None and s_min not in ("", None):
        rows.append(
            (
                "s_min",
                format_display_number(s_min),
                shannon_row_poor(shannon_ok=result.get("shannon_ok"), shannon_class=s_class),
            )
        )
    if s_class and s_class.lower() != "unknown":
        rows.append(
            (
                "shannon_class",
                s_class,
                s_class.lower() in ("unreliable", "failed", "fail"),
            )
        )

    d_avg = scalar_value(result.get("d_avg_nm"))
    d_std = scalar_value(result.get("d_std_nm"))
    if d_avg is not None and d_avg not in ("", None):
        if d_std is not None and d_std not in ("", None):
            rows.append(
                ("⟨R⟩", f"{format_display_number(d_avg)} ± {format_display_number(d_std)} nm", False)
            )
        else:
            rows.append(("⟨R⟩", f"{format_display_number(d_avg)} nm", False))

    pdi = scalar_value(result.get("pdi"))
    if pdi is not None and pdi not in ("", None):
        rows.append(("PDI", format_display_number(pdi), False))

    modality = scalar_value(result.get("modality_class"))
    if modality:
        rows.append(("Modality", str(modality), False))

    stab = str(scalar_value(result.get("stability_class")) or "").strip()
    if stab:
        stab_fail = stab.lower() in ("unstable", "failed", "fail")
        rows.append(("Stability", stab, stab_fail))

    dmax = scalar_value(result.get("dmax_nm"))
    if dmax is None:
        dmax = scalar_value(result.get("rmax_nm"))
    if dmax is not None and dmax not in ("", None):
        rows.append(("Rmax", f"{format_display_number(dmax)} nm", False))

    return rows


def format_sizes_passport_text(result: Mapping[str, Any], *, compact: bool = False) -> str:
    return "\n".join(f"{format_metric_label(m)} = {v}" for m, v, _p in format_sizes_passport_rows(result, compact=compact))


def format_sizes_passport_html(
    result: Mapping[str, Any],
    *,
    poor_color: str = "#ff4d4f",
    compact: bool = False,
) -> str:
    """Rich-text passport table with failing rows colored red."""
    from ..passport_table import format_passport_table_html

    return format_passport_table_html(
        format_sizes_passport_rows(result, compact=compact),
        poor_color=poor_color,
    )
