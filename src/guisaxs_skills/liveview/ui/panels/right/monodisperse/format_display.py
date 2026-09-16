from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Union

_GUINIER_QUALITY_POOR = frozenset({"weak", "degenerate", "interval_only"})
_GUINIER_CLASS_POOR = frozenset({"upturn", "downturn", "chaotic"})
_PASSPORT_CLASS_POOR = frozenset({"failed", "acceptable"})
_PASSPORT_STATUS_POOR = frozenset({"FAILED", "ACCEPTABLE"})
_STABILITY_POOR = frozenset({"unstable", "marginal"})


def scalar_value(value: Any) -> Any:
    """Unwrap single-element lists from skill stdout parsing."""
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def is_guinier_quality_poor(quality_class: str) -> bool:
    return str(quality_class or "").strip().lower() in _GUINIER_QUALITY_POOR


def is_guinier_classification_poor(classification: str) -> bool:
    return str(classification or "").strip().lower() in _GUINIER_CLASS_POOR


def is_passport_quality_poor(
    *,
    overall_status: str = "",
    quality_class: str = "",
    stability_class: str = "",
) -> bool:
    """True when GNOM / D(R) quality passport (or stability) signals caution or failure."""
    status = str(overall_status or "").strip().upper()
    if status in _PASSPORT_STATUS_POOR:
        return True
    q = str(quality_class or "").strip().lower()
    if q in _PASSPORT_CLASS_POOR:
        return True
    stab = str(stability_class or "").strip().lower()
    return stab in _STABILITY_POOR


def format_display_number(value: Union[float, int, str, None]) -> str:
    """
    Format a scalar for monodisperse wizard labels.

    - |value| >= 1: two digits after the decimal point
    - otherwise: show up to the first three non-zero decimal digits
    """
    if value is None:
        return ""
    value = scalar_value(value)
    try:
        x = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(x):
        return "—"
    if x == 0.0:
        return "0"
    if abs(x) >= 1.0:
        return f"{x:.2f}"

    sign = "-" if x < 0 else ""
    compact = format(abs(x), ".12g")
    if "e" in compact or "E" in compact:
        mantissa, exp_str = compact.lower().split("e")
        exp = int(exp_str)
        if "." in mantissa:
            whole, frac = mantissa.split(".", 1)
        else:
            whole, frac = mantissa, ""
        digits = list(whole + frac)
        nz = 0
        kept: list[str] = []
        for ch in digits:
            if ch == ".":
                continue
            kept.append(ch)
            if ch != "0":
                nz += 1
                if nz >= 3:
                    break
        mantissa_str = "".join(kept).lstrip("0") or "0"
        if exp >= 0:
            if exp + 1 <= len(mantissa_str):
                body = mantissa_str[: exp + 1]
                tail = mantissa_str[exp + 1 :]
                compact_dec = body + ("." + tail if tail else "")
            else:
                compact_dec = mantissa_str + "0" * (exp + 1 - len(mantissa_str))
        else:
            zeros = "0" * (-exp - 1)
            compact_dec = f"0.{zeros}{mantissa_str}"
        return f"{sign}{compact_dec}".rstrip("0").rstrip(".") if "." in compact_dec else f"{sign}{compact_dec}"

    if "." not in compact:
        return f"{sign}{compact}"
    intpart, frac = compact.split(".", 1)
    out: list[str] = []
    nonzero = 0
    for ch in frac:
        out.append(ch)
        if ch != "0":
            nonzero += 1
            if nonzero >= 3:
                break
    return f"{sign}{intpart}.{''.join(out)}"


def format_gnom_passport_rows(
    result: Mapping[str, Any],
    *,
    guinier_handoff: Optional[Mapping[str, Any]] = None,
) -> list[tuple[str, bool]]:
    """
    Passport rows as ``(text, poor)``.

    ``poor`` marks rows that indicate failure (for per-line red styling in the adjust wizard).
    """
    from autosaxs.core.gnom_quality import PrQualityThresholds

    handoff = dict(guinier_handoff or {})
    t = PrQualityThresholds()
    rows: list[tuple[str, bool]] = []

    te = scalar_value(result.get("total_estimate"))
    if te is not None and te not in ("", None):
        te_poor = False
        try:
            te_poor = float(te) < float(t.total_estimate_min)
        except (TypeError, ValueError):
            te_poor = False
        rows.append((f"Total est. = {format_display_number(te)}", te_poor))

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
    s_status = str(scalar_value(result.get("overall_status")) or "")
    if s_status in ("", None) and result.get("shannon_ok") is not None:
        s_status = "ok" if scalar_value(result.get("shannon_ok")) else "fail"
    q_min = scalar_value(result.get("q_min_fit_nm"))
    dmax = scalar_value(result.get("dmax_nm"))
    if s_min is not None and s_min not in ("", None):
        shannon_ok_v = result.get("shannon_ok")
        if isinstance(shannon_ok_v, str):
            shannon_fail = shannon_ok_v.strip().lower() in ("false", "0", "no", "fail")
        elif shannon_ok_v is None:
            shannon_fail = s_class.lower() in ("unreliable", "failed", "fail") or str(s_status).upper() == "FAILED"
        else:
            shannon_fail = not bool(shannon_ok_v)
        if q_min is not None and dmax is not None:
            text = (
                f"s_min = (q_min · Dmax) / π = {format_display_number(s_min)} "
                f"(class {s_class}, status {s_status or '—'})"
            )
        else:
            text = f"s_min = {format_display_number(s_min)} (class {s_class}, status {s_status or '—'})"
        rows.append((text, shannon_fail))

    rg_g = scalar_value(result.get("rg_guinier_nm"))
    if rg_g is None:
        rg_g = handoff.get("rg")
    i0_g = handoff.get("i0")
    rg_parts: list[str] = []
    if rg_g is not None and scalar_value(rg_g) not in ("", None):
        rg_parts.append(f"Rg_guinier = {format_display_number(rg_g)}")
    if i0_g is not None and scalar_value(i0_g) not in ("", None):
        rg_parts.append(f"I(0)_guinier = {format_display_number(i0_g)}")
    if rg_parts:
        rows.append((", ".join(rg_parts), False))

    pr_parts: list[str] = []
    rg_pr = result.get("rg_pr_nm")
    if rg_pr is not None and scalar_value(rg_pr) not in ("", None):
        pr_parts.append(f"Rg_P(r) = {format_display_number(rg_pr)}")
    i0_pr = result.get("i0_pr")
    if i0_pr is not None and scalar_value(i0_pr) not in ("", None):
        pr_parts.append(f"I0_P(r) = {format_display_number(i0_pr)}")
    if pr_parts:
        rows.append(("; ".join(pr_parts), False))

    drg = scalar_value(result.get("delta_rg_pct"))
    if drg is not None and drg not in ("", None):
        try:
            drg_f = float(drg)
            if drg_f > t.delta_rg_pct_acceptable:
                drg_status = "failed"
                drg_poor = True
            elif drg_f > t.delta_rg_pct_max:
                drg_status = "marginal"
                drg_poor = False  # caution only — not full failure red
            else:
                drg_status = "ok"
                drg_poor = False
        except (TypeError, ValueError):
            drg_status = str(scalar_value(result.get("pr_quality_class")) or "—")
            drg_poor = str(drg_status).lower() in ("failed", "fail")
        rows.append((f"ΔRg = {format_display_number(drg)}% (status {drg_status})", drg_poor))

    if dmax is not None and dmax not in ("", None):
        rows.append((f"Dmax = {format_display_number(dmax)}", False))

    return rows


def format_gnom_passport_text(
    result: Mapping[str, Any],
    *,
    guinier_handoff: Optional[Mapping[str, Any]] = None,
) -> str:
    """Plain-text passport (monodisperse P(r) pane)."""
    return "\n".join(t for t, _poor in format_gnom_passport_rows(result, guinier_handoff=guinier_handoff))


def format_gnom_passport_html(
    result: Mapping[str, Any],
    *,
    guinier_handoff: Optional[Mapping[str, Any]] = None,
    poor_color: str = "#ff4d4f",
) -> str:
    """Rich-text passport with only failing rows colored red."""
    import html as html_mod

    parts: list[str] = []
    for text, poor in format_gnom_passport_rows(result, guinier_handoff=guinier_handoff):
        esc = html_mod.escape(text)
        if poor:
            parts.append(f'<span style="color:{poor_color}">{esc}</span>')
        else:
            parts.append(esc)
    return "<br/>".join(parts) if parts else "—"
