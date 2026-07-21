"""Cheap parametric hints on recovered GNOM D(R) (post-hoc, no extra GNOM)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from scipy.optimize import curve_fit

from autosaxs.core.gnom_quality import _find_local_maxima
from autosaxs.core.utils import gaussian_pdf, lognormal_pdf, schultz_pdf


def _normalize_dr(r: np.ndarray, d: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
    r = np.asarray(r, dtype=float)
    d = np.asarray(d, dtype=float)
    mask = np.isfinite(r) & np.isfinite(d) & (d >= 0)
    r, d = r[mask], d[mask]
    if r.size < 3:
        return r, d, 0.0
    area = float(np.trapezoid(d, r))
    if not np.isfinite(area) or area <= 0:
        return r, d, 0.0
    return r, d / area, area


def _aic(n: int, rss: float, k: int) -> float:
    if n <= k or rss <= 0 or not np.isfinite(rss):
        return float("inf")
    return float(n * np.log(rss / n) + 2 * k)


def _fit_1comp(
    r: np.ndarray,
    y: np.ndarray,
    *,
    family: str,
) -> Tuple[Optional[Dict[str, float]], float]:
    peak_r = float(r[int(np.argmax(y))]) if y.size else 1.0
    width = max(0.05 * peak_r, 0.1)

    if family == "gauss":
        model = lambda rr, r0, sig: gaussian_pdf(rr, r0, sig)

        def p0():
            return (peak_r, width)

        bounds = ([max(1e-6, 0.1 * peak_r), 1e-6], [max(peak_r * 3, peak_r + 1), peak_r])
    elif family == "lognormal":
        model = lambda rr, mu, sig: lognormal_pdf(rr, mu, sig)

        def p0():
            return (max(peak_r, 1e-6), 0.35)

        bounds = ([1e-6, 0.05], [peak_r * 5, 2.0])
    elif family == "schultz":
        model = lambda rr, r0, sig: schultz_pdf(rr, r0, sig)

        def p0():
            return (peak_r, width)

        bounds = ([1e-6, 1e-6], [peak_r * 5, peak_r])
    else:
        return None, float("inf")

    try:
        popt, _pcov = curve_fit(
            model,
            r,
            y,
            p0=p0(),
            bounds=bounds,
            maxfev=2000,
        )
        fit = model(r, *popt)
        rss = float(np.sum((y - fit) ** 2))
        return {"R0": float(popt[0]), "width": float(popt[1])}, _aic(len(r), rss, 2)
    except (RuntimeError, ValueError, TypeError):
        return None, float("inf")


def _fit_2gauss(r: np.ndarray, y: np.ndarray) -> Tuple[Optional[Dict[str, Any]], float]:
    peaks = _find_local_maxima(r, y)
    if len(peaks) >= 2:
        r1, r2 = sorted(peaks[:2])
    else:
        r1 = float(r[int(np.argmax(y))]) * 0.8
        r2 = float(r[int(np.argmax(y))]) * 1.2
    w = max(0.1 * min(r1, r2), 0.1)

    def model(rr, r0a, siga, r0b, sigb):
        return gaussian_pdf(rr, r0a, siga) + gaussian_pdf(rr, r0b, sigb)

    try:
        popt, _ = curve_fit(
            model,
            r,
            y,
            p0=(r1, w, r2, w),
            bounds=([1e-6, 1e-6, 1e-6, 1e-6], [np.max(r), np.max(r), np.max(r), np.max(r)]),
            maxfev=3000,
        )
        fit = model(r, *popt)
        rss = float(np.sum((y - fit) ** 2))
        return {
            "R0_a": float(popt[0]),
            "width_a": float(popt[1]),
            "R0_b": float(popt[2]),
            "width_b": float(popt[3]),
        }, _aic(len(r), rss, 4)
    except (RuntimeError, ValueError, TypeError):
        return None, float("inf")


def mixture_dist_hint(parametric_family: str) -> str:
    if parametric_family == "gauss":
        return "Gauss"
    return "Schultz"


def classify_dr_parametric(
    r: np.ndarray,
    d: np.ndarray,
    *,
    modality_class: str,
    dr_n_peaks: int,
) -> Dict[str, Any]:
    """Fit coarse parametric models to normalized D(R); return hints for model_mixture."""
    r_n, y_n, _area = _normalize_dr(r, d)
    empty: Dict[str, Any] = {
        "parametric_family": "unknown",
        "parametric_aic": None,
        "parametric_R0_nm": None,
        "parametric_width_nm": None,
        "n_components_suggested": 1,
        "mixture_dist_hint": "Schultz",
        "parametric_peaks_nm": [],
        "modality_confidence": "low",
    }
    if r_n.size < 5 or not np.any(y_n > 0):
        return empty

    peaks = _find_local_maxima(r_n, y_n * _area if _area > 0 else y_n)
    best_family = "unknown"
    best_params: Optional[Dict[str, float]] = None
    best_aic = float("inf")
    for fam in ("lognormal", "schultz", "gauss"):
        params, aic = _fit_1comp(r_n, y_n, family=fam)
        if params is not None and aic < best_aic:
            best_aic = aic
            best_family = fam
            best_params = params

    n_suggested = 1
    if modality_class == "multimodal" or dr_n_peaks >= 2:
        n_suggested = min(3, max(2, dr_n_peaks))
    two_params, aic2 = _fit_2gauss(r_n, y_n)
    if two_params is not None and aic2 + 4 < best_aic:
        n_suggested = max(n_suggested, 2)

    if modality_class == "multimodal":
        n_suggested = max(n_suggested, 2)
    elif modality_class == "monodisperse":
        n_suggested = 1

    modality_confidence = "high"
    if n_suggested >= 2 and dr_n_peaks < 2:
        modality_confidence = "low"
    elif n_suggested == 1 and dr_n_peaks >= 2:
        modality_confidence = "low"

    out: Dict[str, Any] = {
        "parametric_family": best_family,
        "parametric_aic": None if not np.isfinite(best_aic) else float(best_aic),
        "parametric_R0_nm": None if best_params is None else best_params.get("R0"),
        "parametric_width_nm": None if best_params is None else best_params.get("width"),
        "n_components_suggested": int(min(3, max(1, n_suggested))),
        "mixture_dist_hint": mixture_dist_hint(best_family),
        "parametric_peaks_nm": peaks,
        "modality_confidence": modality_confidence,
    }
    return out
