from __future__ import annotations

import math
from typing import Any, Union

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
