"""Shared passport metric/value formatting for liveview tables and panes."""

from __future__ import annotations

import math
from typing import Any, Optional, Union

# Overall passport status tokens shown as their own row — never glued onto s_min.
_OVERALL_STATUS_POOR = frozenset({"FAILED", "ACCEPTABLE"})


def scalar_value(value: Any) -> Any:
    """Unwrap single-element lists from skill stdout parsing."""
    if isinstance(value, list) and len(value) == 1:
        return value[0]
    return value


def format_display_number(value: Union[float, int, str, None]) -> str:
    """
    Format a scalar for passport / wizard labels.

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


# ---------------------------------------------------------------------------
# Explicit quantity display labels (manual undertext).
# Only known scientific symbols — never auto-convert arbitrary snake_case.
# Plain = Unicode subscript for QTableWidget / plain text.
# *_HTML = Qt rich-text <sub> for QLabel HTML tables.
# ---------------------------------------------------------------------------
S_MIN = "sₘᵢₙ"
S_MAX = "sₘₐₓ"
Q_MIN = "qₘᵢₙ"
Q_MAX = "qₘₐₓ"
R_MIN = "rₘᵢₙ"
R_MAX = "rₘₐₓ"
D_MAX = "Dₘₐₓ"
R_MAX_CAP = "Rₘₐₓ"
BIC_CHI2 = "BIC_χ²"
R2_LOG = "R²ₗₒᵍ"

S_MIN_HTML = "s<sub>min</sub>"
S_MAX_HTML = "s<sub>max</sub>"
Q_MIN_HTML = "q<sub>min</sub>"
Q_MAX_HTML = "q<sub>max</sub>"
R_MIN_HTML = "r<sub>min</sub>"
R_MAX_HTML = "r<sub>max</sub>"
D_MAX_HTML = "D<sub>max</sub>"
R_MAX_CAP_HTML = "R<sub>max</sub>"
BIC_CHI2_HTML = "BIC<sub>χ²</sub>"
R2_LOG_HTML = "R<sup>2</sup><sub>log</sub>"

_QUANTITY_PLAIN_TO_HTML: dict[str, str] = {
    S_MIN: S_MIN_HTML,
    S_MAX: S_MAX_HTML,
    Q_MIN: Q_MIN_HTML,
    Q_MAX: Q_MAX_HTML,
    R_MIN: R_MIN_HTML,
    R_MAX: R_MAX_HTML,
    D_MAX: D_MAX_HTML,
    R_MAX_CAP: R_MAX_CAP_HTML,
    BIC_CHI2: BIC_CHI2_HTML,
    R2_LOG: R2_LOG_HTML,
}

# Machine / legacy tokens → final display (call sites should prefer constants).
_MACHINE_METRIC_PLAIN: dict[str, str] = {
    "BIC_chi2": BIC_CHI2,
    "bic_chi2": BIC_CHI2,
    "BIC_χ²": BIC_CHI2,
    "R2_log": R2_LOG,
    "R²_log": R2_LOG,
    "R2_LOG": R2_LOG,
}


def format_metric_label(name: str) -> str:
    """
    Passport metric label passthrough with a few known machine-key remaps.

    Callers should supply the final display string (e.g. ``S_MIN``, ``BIC_CHI2``).
    No general underscore→subscript conversion.
    """
    token = str(name or "").strip()
    known = _MACHINE_METRIC_PLAIN.get(token) or _MACHINE_METRIC_PLAIN.get(token.replace("-", "_"))
    if known is not None:
        return known
    return token


def format_metric_label_html(name: str) -> str:
    """
    HTML-escape a passport metric label.

    Known quantity Unicode labels (``S_MIN``, ``BIC_CHI2``, …) upgrade to rich text.
    """
    import html as html_mod

    plain = format_metric_label(name)
    html = _QUANTITY_PLAIN_TO_HTML.get(plain)
    if html is not None:
        return html
    return html_mod.escape(plain)


def format_value_with_class(
    value: Union[float, int, str, None],
    class_label: Optional[str] = None,
    *,
    suffix: str = "",
) -> str:
    """
    Value with an optional *metric-local* class annotation.

    Do not pass overall passport status here — that belongs in a Status row.
    ``value`` may be a preformatted string (units already applied).
    """
    if isinstance(value, str):
        base = value
    else:
        base = format_display_number(value)
    if suffix:
        base = f"{base}{suffix}"
    cls = str(class_label or "").strip()
    if not cls or cls.lower() == "unknown":
        return base
    return f"{base} ({cls})" if base else cls


def is_overall_status_poor(status: str) -> bool:
    return str(status or "").strip().upper() in _OVERALL_STATUS_POOR


def shannon_row_poor(*, shannon_ok: Any, shannon_class: str = "") -> bool:
    """Severity for the ``s_min`` row from Shannon fields only."""
    if isinstance(shannon_ok, str):
        return shannon_ok.strip().lower() in ("false", "0", "no", "fail")
    if shannon_ok is not None:
        return not bool(shannon_ok)
    return str(shannon_class or "").strip().lower() in ("unreliable", "failed", "fail")
