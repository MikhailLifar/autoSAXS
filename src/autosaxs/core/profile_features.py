"""Deterministic I(q) / real-space feature helpers for skill passports.

Units: ``q`` in nm⁻¹, lengths in nm. Pure NumPy — no ATSAS.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np


def assess_subtraction_negativity(
    I: np.ndarray,
    sigma: Optional[np.ndarray] = None,
    q: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """
    Negativity diagnostics for a subtracted profile.

    **Definition (with σ):** a point is negative when ``I + σ < 0`` — the same
    criterion as :func:`autosaxs.core.utils.subtraction_correctness` (Jacques /
    Trewhella high-q over-subtraction signature).

    **Without σ:** informational only — points with ``I < 0``; ``correctness``
    stays ``correct`` (backward compatible with the legacy string classifier).

    Returns
    -------
    dict
        ``correctness`` (``correct`` | ``over-subtracted``),
        ``frac_neg`` (fraction of points meeting the negativity criterion),
        ``q_first_neg_nm`` (smallest ``q`` among negative points, or ``None``).
    """
    I_arr = np.asarray(I, dtype=float)
    if I_arr.size == 0:
        return {"correctness": "correct", "frac_neg": 0.0, "q_first_neg_nm": None}

    if sigma is not None:
        sigma_arr = np.asarray(sigma, dtype=float)
        if sigma_arr.shape != I_arr.shape:
            raise ValueError("assess_subtraction_negativity: I and sigma shape mismatch")
        neg = np.isfinite(I_arr) & np.isfinite(sigma_arr) & ((I_arr + sigma_arr) < 0.0)
        correctness = "over-subtracted" if bool(np.any(neg)) else "correct"
    else:
        neg = np.isfinite(I_arr) & (I_arr < 0.0)
        correctness = "correct"

    n_finite = int(np.sum(np.isfinite(I_arr)))
    frac_neg = float(np.sum(neg) / n_finite) if n_finite > 0 else 0.0
    q_first: Optional[float] = None
    if q is not None and bool(np.any(neg)):
        q_arr = np.asarray(q, dtype=float)
        if q_arr.shape != I_arr.shape:
            raise ValueError("assess_subtraction_negativity: I and q shape mismatch")
        q_neg = q_arr[neg]
        q_neg = q_neg[np.isfinite(q_neg)]
        if q_neg.size:
            q_first = float(np.min(q_neg))
    return {
        "correctness": correctness,
        "frac_neg": frac_neg,
        "q_first_neg_nm": q_first,
    }


def powerlaw_exponent(
    q: np.ndarray,
    I: np.ndarray,
    *,
    q_min_nm: Optional[float] = None,
    q_max_nm: Optional[float] = None,
    n_min: int = 8,
    high_q_fraction: float = 0.35,
) -> Optional[float]:
    """
    Fit ``I(q) ∝ q^α`` in log–log space; return slope ``α`` (nm⁻¹ units on ``q``).

    When ``q_min_nm`` / ``q_max_nm`` are omitted, uses the highest-``q`` fraction
    of positive-intensity points (default last 35%). Returns ``None`` if too few
    points. Used by ``fit_sizes`` passport (Li 2016 Porod / dimensionality slopes
    −1 / −2 / −4).
    """
    q_arr = np.asarray(q, dtype=float)
    I_arr = np.asarray(I, dtype=float)
    valid = np.isfinite(q_arr) & np.isfinite(I_arr) & (q_arr > 0) & (I_arr > 0)
    if np.sum(valid) < n_min:
        return None
    qq = q_arr[valid]
    II = I_arr[valid]
    order = np.argsort(qq)
    qq, II = qq[order], II[order]

    if q_min_nm is not None or q_max_nm is not None:
        lo = float(q_min_nm) if q_min_nm is not None else float(qq[0])
        hi = float(q_max_nm) if q_max_nm is not None else float(qq[-1])
        mask = (qq >= lo) & (qq <= hi)
        qq, II = qq[mask], II[mask]
    else:
        frac = float(np.clip(high_q_fraction, 0.05, 1.0))
        n_take = max(n_min, int(np.ceil(frac * qq.size)))
        qq, II = qq[-n_take:], II[-n_take:]

    if qq.size < n_min:
        return None
    x = np.log(qq)
    y = np.log(II)
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(y))):
        return None
    # Degenerate q span
    if float(np.ptp(x)) < 1e-12:
        return None
    slope, _intercept = np.polyfit(x, y, 1)
    if not np.isfinite(slope):
        return None
    return float(slope)


def detect_iq_peaks(
    q: np.ndarray,
    I: np.ndarray,
    *,
    min_prominence_frac: float = 0.05,
    min_distance_points: int = 3,
    q_min_nm: Optional[float] = None,
    q_max_nm: Optional[float] = None,
    max_peaks: int = 20,
) -> List[Dict[str, float]]:
    """
    Deterministic local-maxima list on ``I(q)`` (``q`` in nm⁻¹).

    Prominence is measured vs the higher of the two adjacent valley minima and
    required to be ≥ ``min_prominence_frac * max(I)`` in the search window.
    Interpretation (Bragg / S(q) / form-factor) is playbook-owned — Spinozzi 2024
    LNP pins are ~1.43–1.60 nm⁻¹ after Å→nm conversion.
    """
    q_arr = np.asarray(q, dtype=float)
    I_arr = np.asarray(I, dtype=float)
    valid = np.isfinite(q_arr) & np.isfinite(I_arr)
    if np.sum(valid) < 5:
        return []
    qq = q_arr[valid]
    II = I_arr[valid]
    order = np.argsort(qq)
    qq, II = qq[order], II[order]
    if q_min_nm is not None:
        m = qq >= float(q_min_nm)
        qq, II = qq[m], II[m]
    if q_max_nm is not None:
        m = qq <= float(q_max_nm)
        qq, II = qq[m], II[m]
    if qq.size < 5:
        return []

    i_max = float(np.nanmax(np.abs(II)))
    if not (np.isfinite(i_max) and i_max > 0):
        return []
    min_prom = float(min_prominence_frac) * i_max
    min_dist = max(1, int(min_distance_points))

    candidates: List[Tuple[int, float, float]] = []
    for i in range(1, qq.size - 1):
        if not (II[i] >= II[i - 1] and II[i] > II[i + 1]):
            continue
        # Left valley
        j = i - 1
        while j > 0 and II[j] >= II[j - 1]:
            j -= 1
        left_min = float(II[j])
        # Right valley
        k = i + 1
        while k < qq.size - 1 and II[k] >= II[k + 1]:
            k += 1
        right_min = float(II[k])
        prom = float(II[i] - max(left_min, right_min))
        if prom < min_prom:
            continue
        candidates.append((i, float(qq[i]), prom))

    # Enforce minimum index spacing, strongest prominence first
    candidates.sort(key=lambda t: t[2], reverse=True)
    chosen: List[Tuple[int, float, float]] = []
    taken = np.zeros(qq.size, dtype=bool)
    for idx, q_peak, prom in candidates:
        lo = max(0, idx - min_dist)
        hi = min(qq.size, idx + min_dist + 1)
        if np.any(taken[lo:hi]):
            continue
        taken[idx] = True
        chosen.append((idx, q_peak, prom))
        if len(chosen) >= int(max_peaks):
            break
    chosen.sort(key=lambda t: t[1])
    return [
        {"q_nm": q_peak, "I": float(II[idx]), "prominence": prom}
        for idx, q_peak, prom in chosen
    ]


def p0_from_distribution(
    r: np.ndarray,
    p: np.ndarray,
) -> Optional[float]:
    """
    Estimate P(0) / D(0) from a real-space distribution.

    Prefers the value at the smallest non-negative ``r``; if the grid starts
    above 0, linearly extrapolates using the first two finite points when possible.
    """
    r_arr = np.asarray(r, dtype=float)
    p_arr = np.asarray(p, dtype=float)
    m = np.isfinite(r_arr) & np.isfinite(p_arr)
    r_arr, p_arr = r_arr[m], p_arr[m]
    if r_arr.size == 0:
        return None
    order = np.argsort(r_arr)
    r_arr, p_arr = r_arr[order], p_arr[order]
    if float(r_arr[0]) <= 1e-12:
        return float(p_arr[0])
    if r_arr.size >= 2 and float(r_arr[1]) > float(r_arr[0]):
        # Linear extrapolate to r=0
        slope = (p_arr[1] - p_arr[0]) / (r_arr[1] - r_arr[0])
        return float(p_arr[0] - slope * r_arr[0])
    return float(p_arr[0])


def classify_p0_solvent(
    p0: Optional[float],
    *,
    p_scale: Optional[float] = None,
    rel_eps: float = 0.02,
) -> Dict[str, Any]:
    """
    Jacques & Trewhella (2010) solvent check when force-zero at r=0 is released:

    - positive P(0) → under-subtracted
    - negative P(0) → over-subtracted
    - near zero → ok

    ``rel_eps`` is relative to ``p_scale`` (typically ``max(|p|)``); absolute
    fallback ``1e-12`` when scale is missing.
    """
    if p0 is None or not np.isfinite(float(p0)):
        return {"p0": None, "p0_classification": "unknown"}
    p0_f = float(p0)
    scale = None
    if p_scale is not None and np.isfinite(float(p_scale)) and float(p_scale) > 0:
        scale = float(p_scale)
    thr = (rel_eps * scale) if scale is not None else 1e-12
    if abs(p0_f) <= thr:
        cls = "ok"
    elif p0_f > 0:
        cls = "under_subtracted"
    else:
        cls = "over_subtracted"
    return {"p0": p0_f, "p0_classification": cls}


def p0_solvent_from_distribution(
    r: np.ndarray,
    p: np.ndarray,
    *,
    rel_eps: float = 0.02,
) -> Dict[str, Any]:
    """P(0) value + Jacques solvent classification from ``(r, p)`` arrays."""
    p_arr = np.asarray(p, dtype=float)
    scale = float(np.nanmax(np.abs(p_arr))) if p_arr.size else None
    if scale is not None and (not np.isfinite(scale) or scale <= 0):
        scale = None
    return classify_p0_solvent(p0_from_distribution(r, p), p_scale=scale, rel_eps=rel_eps)


def peaks_as_summary_rows(
    peaks: Sequence[Dict[str, float]],
) -> List[Dict[str, Union[float, str]]]:
    """Normalize peak dicts for YAML / report ``summary_extra``."""
    rows: List[Dict[str, Union[float, str]]] = []
    for peak in peaks:
        try:
            rows.append(
                {
                    "q_nm": float(peak["q_nm"]),
                    "I": float(peak["I"]),
                    "prominence": float(peak.get("prominence", 0.0)),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return rows


_P0_BRACKET_CLASSES = frozenset({"under_subtracted", "over_subtracted"})


def format_p0_passport_value(
    p0: Optional[float],
    p0_classification: Optional[str] = None,
    *,
    number_str: Optional[str] = None,
) -> Optional[str]:
    """
    Human-facing **single-row** P(0) passport *value* (D-ASX-004 UI rule).

    Metric label is always ``P(0)``; this returns only the value cell, e.g.
    ``0.012 (over_subtracted)`` or ``0.012`` when classification is ``ok`` /
    ``unknown`` / missing. Returns ``None`` when ``p0`` is absent.

    Structured ``p0`` / ``p0_classification`` fields may still exist in YAML for
    tests and agents; they must not appear as separate passport rows.
    """
    if p0 is None:
        return None
    try:
        p0_f = float(p0)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(p0_f):
        return None
    if number_str is not None and str(number_str).strip():
        body = str(number_str).strip()
    else:
        body = f"{p0_f:.4g}"
    cls = str(p0_classification or "").strip().lower()
    if cls in _P0_BRACKET_CLASSES:
        return f"{body} ({cls})"
    return body
