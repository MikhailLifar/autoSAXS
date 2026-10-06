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



# True Unicode subscripts where available; modifier-letter fallbacks for letters
# with no dedicated subscript code point (b/c/d/f/g/q/w/y/z).
_SUBSCRIPT_CHARS: dict[str, str] = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎",
    "a": "ₐ", "b": "ᵦ", "c": "ᶜ", "d": "ᵈ", "e": "ₑ",
    "f": "ᶠ", "g": "ᵍ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ",
    "k": "ₖ", "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ",
    "p": "ₚ", "r": "ᵣ", "s": "ₛ", "t": "ₜ", "u": "ᵤ",
    "v": "ᵥ", "w": "ʷ", "x": "ₓ", "y": "ʸ", "z": "ᶻ",
}


def _to_subscript(segment: str) -> str:
    """Map a subscript segment to Unicode undertext (best-effort)."""
    out: list[str] = []
    for ch in segment:
        if ch == "_":
            continue
        key = ch.lower() if ch.isalpha() else ch
        out.append(_SUBSCRIPT_CHARS.get(key, ch))
    return "".join(out)


def format_metric_label(name: str) -> str:
    """
    Passport/quantity metric label with undertext.

    ``_`` introduces a subscript segment (``s_min`` → sₘᵢₙ, ``R_g`` → Rᵍ,
    ``I_0`` → I₀). Only for passport-style quantity labels — do not pass file
    paths, snake_case code ids, or menu names.
    """
    token = str(name or "").strip().replace("-", "_")
    if not token or "_" not in token:
        return token
    base, rest = token.split("_", 1)
    if not rest:
        return base
    return f"{base}{_to_subscript(rest)}"


def format_metric_label_html(name: str) -> str:
    """
    Rich-text passport metric label (``s_min`` → s<sub>min</sub>).

    Same underscore→undertext rule as ``format_metric_label``; for QLabel HTML only.
    """
    import html as html_mod

    token = str(name or "").strip().replace("-", "_")
    if not token or "_" not in token:
        return html_mod.escape(token)
    base, rest = token.split("_", 1)
    if not rest:
        return html_mod.escape(base)
    return f"{html_mod.escape(base)}<sub>{html_mod.escape(rest)}</sub>"


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
