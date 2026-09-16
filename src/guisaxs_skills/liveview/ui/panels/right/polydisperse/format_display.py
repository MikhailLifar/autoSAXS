"""Passport / diagnostics formatting for polydisperse D(R) pane and adjust wizard."""

from __future__ import annotations

import html as html_mod
from typing import Any, Mapping

from ..monodisperse.format_display import format_display_number, scalar_value


def format_sizes_passport_rows(result: Mapping[str, Any]) -> list[tuple[str, bool]]:
    """
    D(R) passport rows as ``(text, poor)``.

    ``poor`` marks rows that indicate failure (for per-line red styling).
    """
    from autosaxs.core.gnom_quality import DrQualityThresholds

    t = DrQualityThresholds()
    rows: list[tuple[str, bool]] = []

    te = scalar_value(result.get("total_estimate"))
    status = str(scalar_value(result.get("overall_status")) or "")
    sizes_class = str(scalar_value(result.get("sizes_quality_class")) or "")
    if te is not None and te not in ("", None):
        te_poor = False
        try:
            te_poor = float(te) < float(t.total_estimate_min)
        except (TypeError, ValueError):
            te_poor = False
        head = f"Total est. = {format_display_number(te)}"
        if status:
            head += f" · {status}"
        elif sizes_class:
            head += f" · {sizes_class}"
        # Whole TE/status line red when TE fails or overall FAILED
        status_fail = status.upper() == "FAILED" or sizes_class.lower() == "failed"
        rows.append((head, te_poor or status_fail))

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
        rows.append((f"χ² = {format_display_number(chi2)} ({chi2_label})", chi2_poor))

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
        text = f"s_min = {format_display_number(s_min)}"
        if s_class and s_class != "unknown":
            text += f" ({s_class})"
        rows.append((text, shannon_fail))

    size_parts: list[str] = []
    d_avg = scalar_value(result.get("d_avg_nm"))
    d_std = scalar_value(result.get("d_std_nm"))
    pdi = scalar_value(result.get("pdi"))
    modality = scalar_value(result.get("modality_class"))
    if d_avg is not None and d_avg not in ("", None):
        if d_std is not None and d_std not in ("", None):
            size_parts.append(
                f"⟨R⟩ = {format_display_number(d_avg)} ± {format_display_number(d_std)}"
            )
        else:
            size_parts.append(f"⟨R⟩ = {format_display_number(d_avg)}")
    if pdi is not None and pdi not in ("", None):
        size_parts.append(f"PDI = {format_display_number(pdi)}")
    if modality:
        size_parts.append(str(modality))
    if size_parts:
        rows.append((" · ".join(size_parts), False))

    stab = str(scalar_value(result.get("stability_class")) or "").strip()
    if stab:
        stab_fail = stab.lower() in ("unstable", "failed", "fail")
        rows.append((f"stability = {stab}", stab_fail))

    dmax = scalar_value(result.get("dmax_nm"))
    if dmax is None:
        dmax = scalar_value(result.get("rmax_nm"))
    if dmax is not None and dmax not in ("", None):
        rows.append((f"Rmax = {format_display_number(dmax)}", False))

    return rows


def format_sizes_passport_text(result: Mapping[str, Any]) -> str:
    return "\n".join(t for t, _poor in format_sizes_passport_rows(result))


def format_sizes_passport_html(
    result: Mapping[str, Any],
    *,
    poor_color: str = "#ff4d4f",
) -> str:
    """Rich-text passport with only failing rows colored red."""
    parts: list[str] = []
    for text, poor in format_sizes_passport_rows(result):
        esc = html_mod.escape(text)
        if poor:
            parts.append(f'<span style="color:{poor_color}">{esc}</span>')
        else:
            parts.append(esc)
    return "<br/>".join(parts) if parts else "—"
