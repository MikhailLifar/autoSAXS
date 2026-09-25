from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Union

_GUINIER_QUALITY_POOR = frozenset(
    {
        "weak",
        "degenerate",
        # Legacy skill labels (pre-rename results on disk).
        "interval_only",
        "qrg limit violated",
    }
)
_GUINIER_QUALITY_WARN = frozenset(
    {
        "acceptable",
        "weak in guinier region",
        # Legacy mid-tier.
        "validated",
    }
)
_GUINIER_CLASS_POOR = frozenset({"upturn", "downturn", "chaotic"})
_PASSPORT_CLASS_POOR = frozenset({"failed", "acceptable"})
_PASSPORT_STATUS_POOR = frozenset({"FAILED", "ACCEPTABLE"})
_STABILITY_POOR = frozenset({"unstable", "marginal"})

# Map legacy skill quality_class tokens → current passport labels.
_GUINIER_QUALITY_LABELS = {
    "validated_strong": "good",
    "validated": "acceptable",
    "interval_only": "weak in Guinier region",
    "qrg limit violated": "weak in Guinier region",
}


def scalar_value(value: Any) -> Any:
    """Unwrap single-element lists from skill stdout parsing."""
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def is_guinier_quality_poor(quality_class: str) -> bool:
    raw = str(quality_class or "").strip()
    mapped = _GUINIER_QUALITY_LABELS.get(raw.lower(), raw)
    return str(mapped).strip().lower() in _GUINIER_QUALITY_POOR


def is_guinier_quality_warn(quality_class: str) -> bool:
    raw = str(quality_class or "").strip()
    mapped = _GUINIER_QUALITY_LABELS.get(raw.lower(), raw)
    return str(mapped).strip().lower() in _GUINIER_QUALITY_WARN


def guinier_quality_severity(quality_class: str) -> str:
    """Return ``ok`` | ``warn`` | ``poor`` for passport coloring."""
    if is_guinier_quality_poor(quality_class):
        return "poor"
    if is_guinier_quality_warn(quality_class):
        return "warn"
    return "ok"


def is_guinier_classification_poor(classification: str) -> bool:
    return str(classification or "").strip().lower() in _GUINIER_CLASS_POOR


def normalize_guinier_quality_label(quality_class: str) -> str:
    """Map legacy quality_class tokens to current passport wording."""
    raw = str(quality_class or "").strip()
    if not raw:
        return ""
    return _GUINIER_QUALITY_LABELS.get(raw.lower(), raw)

_QRG_MAX = 1.3


def is_guinier_qrg_poor(qrg: Any) -> bool:
    try:
        v = float(qrg)
    except (TypeError, ValueError):
        return False
    return math.isfinite(v) and v > _QRG_MAX


def format_guinier_passport_rows(result: Mapping[str, Any]) -> list[tuple[str, str, bool]]:
    """
    Full Guinier passport rows as ``(metric, value, poor)``.

    Includes ``qRg`` (``q_max * Rg``); highlighted when ``> 1.3``.
    """
    data = dict(result or {})
    rows: list[tuple[str, str, bool]] = []

    qcls = str(scalar_value(data.get("quality_class")) or "").strip()
    if qcls:
        qcls = normalize_guinier_quality_label(qcls)
        rows.append(("fit quality", qcls, guinier_quality_severity(qcls)))

    clas = str(scalar_value(data.get("classification")) or "").strip()
    if clas:
        rows.append(("low-q classification", clas, is_guinier_classification_poor(clas)))

    rg = scalar_value(data.get("rg"))
    if rg is None:
        rg = scalar_value(data.get("Rg"))
    if rg is not None:
        rows.append(("Rg", f"{format_display_number(rg)} nm", False))

    i0 = scalar_value(data.get("i0"))
    if i0 is None:
        i0 = scalar_value(data.get("I0"))
    if i0 is not None:
        rows.append(("I(0)", format_display_number(i0), False))

    qrg = scalar_value(data.get("qrg"))
    if qrg is None:
        q_max = scalar_value(data.get("q_max"))
        if q_max is not None and rg is not None:
            try:
                qrg = float(q_max) * float(rg)
            except (TypeError, ValueError):
                qrg = None
    if qrg is not None:
        rows.append(("qRg", format_display_number(qrg), is_guinier_qrg_poor(qrg)))

    ir2 = scalar_value(data.get("interval_r2"))
    if ir2 is None:
        ir2 = scalar_value(data.get("fit_quality"))
    if ir2 is not None:
        rows.append(("interval R²", format_display_number(ir2), False))

    vr2 = scalar_value(data.get("validation_r2"))
    if vr2 is not None:
        rows.append(("validation R²", format_display_number(vr2), False))

    q_min = scalar_value(data.get("q_min"))
    q_max = scalar_value(data.get("q_max"))
    if q_min is not None and q_max is not None:
        rows.append(
            (
                "q range",
                f"[{format_display_number(q_min)}, {format_display_number(q_max)}] nm⁻¹",
                False,
            )
        )

    n_pts = scalar_value(data.get("n_points"))
    if n_pts is not None:
        rows.append(("n points", str(int(n_pts)) if str(n_pts).isdigit() or isinstance(n_pts, int) else str(n_pts), False))

    fp = scalar_value(data.get("first_point_1based"))
    lp = scalar_value(data.get("last_point_1based"))
    if fp is not None:
        rows.append(("first", str(int(fp)), False))
    if lp is not None:
        rows.append(("last", str(int(lp)), False))

    return rows


def format_guinier_passport_html(
    result: Mapping[str, Any],
    *,
    poor_color: Optional[str] = None,
) -> str:
    from ..passport_table import format_passport_table_html
    from ......ui.style import COLOR_QUALITY_POOR

    return format_passport_table_html(
        format_guinier_passport_rows(result),
        poor_color=poor_color or COLOR_QUALITY_POOR,
    )


def format_guinier_passport_text(result: Mapping[str, Any]) -> str:
    rows = format_guinier_passport_rows(result)
    if not rows:
        return "—"
    return "\n".join(f"{m}: {v}" for m, v, _p in rows)


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
    compact: bool = False,
) -> list[tuple[str, str, bool]]:
    """
    Passport rows as ``(metric, value, poor)``.

    ``poor`` marks rows that indicate failure (for per-line red styling).
    ``compact`` omits wiggle index, s_max, and I(0) (analysis-pane preview).
    """
    from autosaxs.core.gnom_quality import PrQualityThresholds

    handoff = dict(guinier_handoff or {})
    t = PrQualityThresholds()
    rows: list[tuple[str, str, bool]] = []

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
        rows.append(("χ²", f"{format_display_number(chi2)} ({chi2_label})", chi2_poor))

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
            val = format_display_number(wig)
            if wig_class and wig_class != "unknown":
                val += f" ({wig_class})"
            rows.append(("wiggle_index", val, wig_poor))

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
            metric = "s_min (q_min·Dmax/π)"
        else:
            metric = "s_min"
        val = format_display_number(s_min)
        extras = []
        if s_class and s_class != "unknown":
            extras.append(s_class)
        if s_status:
            extras.append(str(s_status))
        if extras:
            val += f" ({', '.join(extras)})"
        rows.append((metric, val, shannon_fail))

    rg_g = scalar_value(result.get("rg_guinier_nm"))
    if rg_g is None:
        rg_g = handoff.get("rg")
    if rg_g is not None and scalar_value(rg_g) not in ("", None):
        rows.append(("Rg_guinier", f"{format_display_number(rg_g)} nm", False))
    if not compact:
        i0_g = handoff.get("i0")
        if i0_g is not None and scalar_value(i0_g) not in ("", None):
            rows.append(("I(0)_guinier", format_display_number(i0_g), False))

    rg_pr = result.get("rg_pr_nm")
    if rg_pr is not None and scalar_value(rg_pr) not in ("", None):
        rows.append(("Rg_P(r)", f"{format_display_number(rg_pr)} nm", False))
    if not compact:
        i0_pr = result.get("i0_pr")
        if i0_pr is not None and scalar_value(i0_pr) not in ("", None):
            rows.append(("I0_P(r)", format_display_number(i0_pr), False))

    drg = scalar_value(result.get("delta_rg_pct"))
    if drg is not None and drg not in ("", None):
        try:
            drg_f = float(drg)
            if drg_f > t.delta_rg_pct_acceptable:
                drg_status = "failed"
                drg_poor = True
            elif drg_f > t.delta_rg_pct_max:
                drg_status = "marginal"
                drg_poor = False
            else:
                drg_status = "ok"
                drg_poor = False
        except (TypeError, ValueError):
            drg_status = str(scalar_value(result.get("pr_quality_class")) or "—")
            drg_poor = str(drg_status).lower() in ("failed", "fail")
        rows.append(("ΔRg", f"{format_display_number(drg)}% ({drg_status})", drg_poor))

    if dmax is not None and dmax not in ("", None):
        rows.append(("Dmax", f"{format_display_number(dmax)} nm", False))

    return rows


def format_gnom_passport_text(
    result: Mapping[str, Any],
    *,
    guinier_handoff: Optional[Mapping[str, Any]] = None,
    compact: bool = False,
) -> str:
    """Plain-text passport (one line per metric)."""
    return "\n".join(
        f"{m} = {v}"
        for m, v, _poor in format_gnom_passport_rows(
            result, guinier_handoff=guinier_handoff, compact=compact
        )
    )


def format_gnom_passport_html(
    result: Mapping[str, Any],
    *,
    guinier_handoff: Optional[Mapping[str, Any]] = None,
    poor_color: str = "#ff4d4f",
    compact: bool = False,
) -> str:
    """Rich-text passport table with failing rows colored red."""
    from ..passport_table import format_passport_table_html

    return format_passport_table_html(
        format_gnom_passport_rows(result, guinier_handoff=guinier_handoff, compact=compact),
        poor_color=poor_color,
    )
