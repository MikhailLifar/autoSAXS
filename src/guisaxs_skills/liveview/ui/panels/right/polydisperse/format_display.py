"""Passport / diagnostics formatting for polydisperse D(R) pane and adjust wizard."""

from __future__ import annotations

from typing import Any, Mapping

from ..monodisperse.format_display import format_display_number, scalar_value


def format_sizes_passport_rows(
    result: Mapping[str, Any],
    *,
    compact: bool = False,
) -> list[tuple[str, str, bool]]:
    """
    D(R) passport rows as ``(metric, value, poor)``.

    ``compact`` omits wiggle index, s_max, and I(0)-style extras (analysis-pane preview).
    """
    from autosaxs.core.gnom_quality import DrQualityThresholds

    t = DrQualityThresholds()
    rows: list[tuple[str, str, bool]] = []

    te = scalar_value(result.get("total_estimate"))
    status = str(scalar_value(result.get("overall_status")) or "")
    sizes_class = str(scalar_value(result.get("sizes_quality_class")) or "")
    if te is not None and te not in ("", None):
        te_poor = False
        try:
            te_poor = float(te) < float(t.total_estimate_min)
        except (TypeError, ValueError):
            te_poor = False
        val = format_display_number(te)
        if status:
            val += f" · {status}"
        elif sizes_class:
            val += f" · {sizes_class}"
        status_fail = status.upper() == "FAILED" or sizes_class.lower() == "failed"
        rows.append(("Total est.", val, te_poor or status_fail))

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
        rows.append(("χ²", f"{format_display_number(chi2)} ({chi2_label})", chi2_poor))

    det = str(scalar_value(result.get("detail_reliability_class")) or "").strip()
    if det and det.lower() != "unknown":
        det_poor = det.upper() == "SUSPICIOUS"
        rows.append(("Detail reliability", det, det_poor))

    n_s = scalar_value(result.get("n_shannon"))
    if n_s is not None and n_s not in ("", None):
        rows.append(("n_shannon", format_display_number(n_s), False))

    if not compact:
        s_max = scalar_value(result.get("shannon_s_max"))
        if s_max is not None and s_max not in ("", None):
            rows.append(("s_max", format_display_number(s_max), False))

        wig = scalar_value(result.get("wiggle_index"))
        wig_class = str(scalar_value(result.get("wiggle_class")) or "unknown")
        if wig is not None and wig not in ("", None):
            wig_poor = wig_class.lower() == "high"
            val = format_display_number(wig)
            if wig_class and wig_class != "unknown":
                val += f" ({wig_class})"
            rows.append(("wiggle_index", val, wig_poor))

    s_min = scalar_value(result.get("shannon_s_min"))
    s_class = str(scalar_value(result.get("shannon_class")) or "unknown")
    if s_min is not None and s_min not in ("", None):
        shannon_ok_v = result.get("shannon_ok")
        if isinstance(shannon_ok_v, str):
            shannon_fail = shannon_ok_v.strip().lower() in ("false", "0", "no", "fail")
        elif shannon_ok_v is None:
            shannon_fail = s_class.lower() in ("unreliable", "failed", "fail")
        else:
            shannon_fail = not bool(shannon_ok_v)
        val = format_display_number(s_min)
        if s_class and s_class != "unknown":
            val += f" ({s_class})"
        rows.append(("s_min", val, shannon_fail))

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
    return "\n".join(f"{m} = {v}" for m, v, _p in format_sizes_passport_rows(result, compact=compact))


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
