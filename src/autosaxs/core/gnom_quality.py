"""Post-hoc GNOM/DATGNOM quality metrics and classification (no fitting)."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from autosaxs.core.gnom import distribution_arrays

DEFAULT_TOTAL_ESTIMATE_MIN = 0.55
DEFAULT_DELTA_RG_PCT_MAX = 10.0
DEFAULT_DELTA_RG_PCT_ACCEPTABLE = 15.0
DEFAULT_PDI_MONODISPERSE_MAX = 0.10
DEFAULT_PDI_POLYDISPERSE_MAX = 0.30
# Reduced χ² (I_exp vs I_fit) bands for passport classification.
DEFAULT_CHI2_GOOD_MIN = 0.5
DEFAULT_CHI2_GOOD_MAX = 1.5
DEFAULT_CHI2_ACCEPTABLE_MIN = 0.3
DEFAULT_CHI2_ACCEPTABLE_MAX = 2.5


@dataclass(frozen=True)
class PrQualityThresholds:
    total_estimate_min: float = DEFAULT_TOTAL_ESTIMATE_MIN
    delta_rg_pct_max: float = DEFAULT_DELTA_RG_PCT_MAX
    delta_rg_pct_acceptable: float = DEFAULT_DELTA_RG_PCT_ACCEPTABLE
    chi2_good_min: float = DEFAULT_CHI2_GOOD_MIN
    chi2_good_max: float = DEFAULT_CHI2_GOOD_MAX
    chi2_acceptable_min: float = DEFAULT_CHI2_ACCEPTABLE_MIN
    chi2_acceptable_max: float = DEFAULT_CHI2_ACCEPTABLE_MAX


@dataclass(frozen=True)
class DrQualityThresholds:
    total_estimate_min: float = DEFAULT_TOTAL_ESTIMATE_MIN
    pdi_monodisperse_max: float = DEFAULT_PDI_MONODISPERSE_MAX
    pdi_polydisperse_max: float = DEFAULT_PDI_POLYDISPERSE_MAX
    chi2_good_min: float = DEFAULT_CHI2_GOOD_MIN
    chi2_good_max: float = DEFAULT_CHI2_GOOD_MAX
    chi2_acceptable_min: float = DEFAULT_CHI2_ACCEPTABLE_MIN
    chi2_acceptable_max: float = DEFAULT_CHI2_ACCEPTABLE_MAX


def chi2_from_iq_table(iq_table: Any) -> Optional[float]:
    """Reduced χ² from ``parse_gnom_out`` ``iq_table`` ``(q, I_exp, sigma, I_fit)``."""
    if iq_table is None:
        return None
    try:
        _q, i_exp, sigma, i_fit = iq_table
    except (TypeError, ValueError):
        return None
    try:
        from autosaxs.core.utils import calc_chi2

        ie = np.asarray(i_exp, dtype=float)
        ifit = np.asarray(i_fit, dtype=float)
        sig = np.asarray(sigma, dtype=float) if sigma is not None else None
    except (TypeError, ValueError):
        return None
    if sig is None:
        return None
    mask = np.isfinite(ie) & np.isfinite(ifit) & np.isfinite(sig) & (sig > 0)
    if int(np.count_nonzero(mask)) < 2:
        return None
    try:
        return float(calc_chi2(ie[mask], ifit[mask], sig[mask]))
    except Exception:
        return None


def classify_chi2(
    chi2: Optional[float],
    *,
    good_min: float = DEFAULT_CHI2_GOOD_MIN,
    good_max: float = DEFAULT_CHI2_GOOD_MAX,
    acceptable_min: float = DEFAULT_CHI2_ACCEPTABLE_MIN,
    acceptable_max: float = DEFAULT_CHI2_ACCEPTABLE_MAX,
) -> str:
    """
    Classify reduced χ² for passport display.

    Returns ``high_quality``, ``acceptable``, ``failed``, or ``unknown``.
    """
    if chi2 is None:
        return "unknown"
    try:
        v = float(chi2)
    except (TypeError, ValueError):
        return "unknown"
    if not np.isfinite(v) or v < 0:
        return "unknown"
    if good_min <= v <= good_max:
        return "high_quality"
    if acceptable_min <= v <= acceptable_max:
        return "acceptable"
    return "failed"


def _downgrade_quality(current: str, other: str) -> str:
    rank = {"high_quality": 0, "acceptable": 1, "failed": 2}
    return current if rank.get(current, 2) >= rank.get(other, 2) else other


def rg_from_pr(r: np.ndarray, p: np.ndarray) -> Optional[float]:
    """Integral Rg from p(r): sqrt(∫r²p dr / (2 ∫p dr))."""
    r = np.asarray(r, dtype=float)
    p = np.asarray(p, dtype=float)
    mask = np.isfinite(r) & np.isfinite(p)
    r, p = r[mask], p[mask]
    if r.size < 2:
        return None
    int_p = float(np.trapezoid(p, r))
    if not np.isfinite(int_p) or int_p <= 0:
        return None
    int_r2p = float(np.trapezoid((r ** 2) * p, r))
    if not np.isfinite(int_r2p) or int_r2p < 0:
        return None
    return float(math.sqrt(int_r2p / (2.0 * int_p)))


def q_min_from_gnom_fit(
    parsed: Dict[str, Any],
    *,
    q_nm: Optional[np.ndarray] = None,
    first_pt_1based: Optional[int] = None,
) -> Optional[float]:
    """Low-q bound used in the GNOM fit (nm⁻¹)."""
    angular = parsed.get("angular_range")
    if angular is not None:
        try:
            q_lo = float(angular[0])
            if np.isfinite(q_lo) and q_lo > 0:
                return q_lo
        except (TypeError, ValueError, IndexError):
            pass
    iq_table = parsed.get("iq_table")
    if iq_table is not None:
        try:
            q = np.asarray(iq_table[0], dtype=float)
            finite = q[np.isfinite(q) & (q > 0)]
            if finite.size:
                return float(np.min(finite))
        except (TypeError, ValueError, IndexError):
            pass
    if q_nm is not None and first_pt_1based is not None:
        idx = int(first_pt_1based) - 1
        q_arr = np.asarray(q_nm, dtype=float)
        if 0 <= idx < q_arr.size and np.isfinite(q_arr[idx]) and q_arr[idx] > 0:
            return float(q_arr[idx])
    return None


def q_max_from_gnom_fit(
    parsed: Dict[str, Any],
    *,
    q_nm: Optional[np.ndarray] = None,
    last_pt_1based: Optional[int] = None,
) -> Optional[float]:
    """High-q bound used in the GNOM fit (nm⁻¹)."""
    angular = parsed.get("angular_range")
    if angular is not None:
        try:
            q_hi = float(angular[1])
            if np.isfinite(q_hi) and q_hi > 0:
                return q_hi
        except (TypeError, ValueError, IndexError):
            pass
    iq_table = parsed.get("iq_table")
    if iq_table is not None:
        try:
            q = np.asarray(iq_table[0], dtype=float)
            finite = q[np.isfinite(q) & (q > 0)]
            if finite.size:
                return float(np.max(finite))
        except (TypeError, ValueError, IndexError):
            pass
    if q_nm is not None and last_pt_1based is not None:
        idx = int(last_pt_1based) - 1
        q_arr = np.asarray(q_nm, dtype=float)
        if 0 <= idx < q_arr.size and np.isfinite(q_arr[idx]) and q_arr[idx] > 0:
            return float(q_arr[idx])
    return None


def shannon_s_min(q_min_nm: float, dmax_nm: float) -> Optional[float]:
    if not (np.isfinite(q_min_nm) and q_min_nm > 0 and np.isfinite(dmax_nm) and dmax_nm > 0):
        return None
    return float((q_min_nm * dmax_nm) / math.pi)


def shannon_s_max(q_max_nm: float, dmax_nm: float) -> Optional[float]:
    if not (np.isfinite(q_max_nm) and q_max_nm > 0 and np.isfinite(dmax_nm) and dmax_nm > 0):
        return None
    return float((q_max_nm * dmax_nm) / math.pi)


def n_shannon_channels(
    q_min_nm: float,
    q_max_nm: float,
    dmax_nm: float,
) -> Optional[float]:
    if not (
        np.isfinite(q_min_nm)
        and q_min_nm > 0
        and np.isfinite(q_max_nm)
        and q_max_nm > q_min_nm
        and np.isfinite(dmax_nm)
        and dmax_nm > 0
    ):
        return None
    return float(((q_max_nm - q_min_nm) * dmax_nm) / math.pi)


# High-frequency residual scale for wiggle_index = hf / WIGGLE_HF_REF.
# Calibrated on validation mono GNOM outs (tests/test_skills_real_data.py /
# validation/reference_mono + fit_distances_datgnom/refine): median hf ≈ 0.026
# → wiggle_index ≈ 0.53 on accepted fits (target band ~0.2–0.8).
WIGGLE_HF_REF = 0.05
DEFAULT_DETAIL_N_SHANNON_FLOOR = 8.0


def _box_smooth(y: np.ndarray, *, half_width: int) -> np.ndarray:
    half = max(1, int(half_width))
    w = 2 * half + 1
    kernel = np.ones(w, dtype=float) / float(w)
    return np.convolve(np.asarray(y, dtype=float), kernel, mode="same")


def wiggle_hf_ratio(
    r: np.ndarray,
    y: np.ndarray,
    *,
    q_max_nm: float,
) -> Optional[float]:
    """
    High-frequency residual ratio for a real-space curve.

    ``hf = rms(y - smooth(y)) / (rms(y) + eps)`` after peak-normalization,
    with smooth window width ~ π/q_max. Independent of ``neg_frac``.
    """
    try:
        q_max = float(q_max_nm)
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(q_max) and q_max > 0):
        return None
    r_arr = np.asarray(r, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    mask = np.isfinite(r_arr) & np.isfinite(y_arr)
    r_arr, y_arr = r_arr[mask], y_arr[mask]
    if r_arr.size < 5:
        return None
    peak = float(np.max(np.abs(y_arr)))
    if not (np.isfinite(peak) and peak > 0):
        return None
    y_n = y_arr / peak
    r_res = math.pi / q_max
    dr = float(np.median(np.diff(r_arr)))
    if not (np.isfinite(dr) and dr > 0):
        return None
    half = max(1, int(round(r_res / dr / 2.0)))
    y_smooth = _box_smooth(y_n, half_width=half)
    y_hf = y_n - y_smooth
    eps = 1e-12
    rms_y = float(np.sqrt(np.mean(y_n ** 2)))
    rms_hf = float(np.sqrt(np.mean(y_hf ** 2)))
    if not np.isfinite(rms_hf) or not np.isfinite(rms_y):
        return None
    return float(rms_hf / (rms_y + eps))


def wiggle_index_from_distribution(
    r: np.ndarray,
    y: np.ndarray,
    *,
    q_max_nm: float,
    hf_ref: float = WIGGLE_HF_REF,
) -> Optional[float]:
    """Wiggle index (~0 clean, ~1 borderline, >=2 too wiggly). Requires fit ``q_max``."""
    hf = wiggle_hf_ratio(r, y, q_max_nm=q_max_nm)
    if hf is None:
        return None
    try:
        ref = float(hf_ref)
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(ref) and ref > 0):
        return None
    return float(hf / ref)


def classify_wiggle_index(wiggle_index: Optional[float]) -> str:
    if wiggle_index is None or not np.isfinite(wiggle_index):
        return "unknown"
    if wiggle_index < 1.0:
        return "low"
    if wiggle_index < 2.0:
        return "acceptable"
    return "high"


def classify_detail_reliability(
    *,
    wiggle_index: Optional[float],
    chi2: Optional[float],
    n_shannon: Optional[float],
    chi2_good_min: float = DEFAULT_CHI2_GOOD_MIN,
    n_shannon_floor: float = DEFAULT_DETAIL_N_SHANNON_FLOOR,
) -> str:
    """
    Combined fit-detail reliability from wiggles, χ², and Shannon channel count.

    Does not use ``neg_frac``. Does not change overall p(r)/D(R) quality class.
    """
    if wiggle_index is None or not np.isfinite(wiggle_index):
        return "unknown"
    if wiggle_index >= 2.0:
        return "SUSPICIOUS"
    ns = n_shannon
    try:
        ns_v = float(ns) if ns is not None else float("nan")
    except (TypeError, ValueError):
        ns_v = float("nan")
    chi2_v: Optional[float]
    try:
        chi2_v = float(chi2) if chi2 is not None else None
    except (TypeError, ValueError):
        chi2_v = None
    if (
        wiggle_index >= 1.0
        and chi2_v is not None
        and np.isfinite(chi2_v)
        and chi2_v < float(chi2_good_min)
        and np.isfinite(ns_v)
        and ns_v >= float(n_shannon_floor)
    ):
        return "SUSPICIOUS"
    return "RELIABLE"


def detail_reliability_tips(
    *,
    detail_reliability_class: str,
    wiggle_index: Optional[float],
    wiggle_class: str,
    shannon_s_max: Optional[float],
    n_shannon: Optional[float],
) -> List[str]:
    tips: List[str] = []
    parts = [f"Detail reliability: {detail_reliability_class}."]
    if n_shannon is not None and np.isfinite(n_shannon):
        parts.append(f"n_shannon = {float(n_shannon):.2f}.")
    if shannon_s_max is not None and np.isfinite(shannon_s_max):
        parts.append(f"s_max = (q_max · D_max) / π = {float(shannon_s_max):.3f}.")
    if wiggle_index is not None and np.isfinite(wiggle_index):
        parts.append(f"wiggle_index = {float(wiggle_index):.3f} ({wiggle_class}).")
    tips.append(" ".join(parts))
    if detail_reliability_class == "SUSPICIOUS":
        tips.append(
            "High-q detail may be unreliable (wiggles and/or over-fitting noise). "
            "Consider lowering q-max before interpreting fine structure or running shape modeling."
        )
    elif wiggle_class == "high":
        tips.append(
            "Real-space curve shows rapid ripples finer than the fit resolution (π/q_max); "
            "treat fine features with caution."
        )
    elif wiggle_class == "acceptable":
        tips.append(
            "Mild real-space ripples — acceptable if Total Estimate / χ² look sane; "
            "avoid over-interpreting small shoulders."
        )
    return tips


def classify_shannon(s_min: Optional[float]) -> str:
    if s_min is None or not np.isfinite(s_min):
        return "unknown"
    if s_min < 1.0:
        return "stable"
    if s_min < 1.5:
        return "first_channel_lost"
    if s_min < 2.0:
        return "marginal"
    return "unreliable"


def shannon_ok(q_min_nm: Optional[float], dmax_nm: Optional[float]) -> Optional[bool]:
    if q_min_nm is None or dmax_nm is None:
        return None
    if not (np.isfinite(q_min_nm) and q_min_nm > 0 and np.isfinite(dmax_nm) and dmax_nm > 0):
        return None
    return bool(q_min_nm < (math.pi / dmax_nm))


def shannon_tip(s_min: Optional[float], shannon_class: str) -> str:
    """Rich guidance for interpreting s_min without assuming a system type upfront."""
    if s_min is None or not np.isfinite(s_min):
        return (
            "Shannon metric s_min = (q_min · D_max) / π could not be computed from the GNOM fit. "
            "Check that the .out reports an angular range and a real-space maximum size."
        )
    s_txt = f"{s_min:.3f}"
    lines = [
        f"Shannon metric s_min = (q_min · D_max) / π = {s_txt} (class: {shannon_class}).",
        "Interpretation by system type:",
        "  • Rigid, monodisperse proteins (globular): s_min < 1.0 is stable; "
        "1.0–1.5 may still be acceptable; ≥ 1.5 is increasingly unstable; ≥ 2.0 is unreliable.",
        "  • Flexible coils or polydisperse nanoparticles: s_min up to ~2.0 can still be "
        "informative when p(r) or D(R) decays smoothly without a sharp cutoff.",
        "If s_min is high, low-q data may be insufficient for the chosen D_max — consider "
        "increasing detector distance or using a longer wavelength to extend the measurable q-range.",
    ]
    if shannon_class == "unreliable":
        lines.append(
            "s_min ≥ 2.0: two or more Shannon channels are lost; size/shape information at this "
            "D_max is dominated by regularization and noise."
        )
    elif shannon_class == "marginal":
        lines.append(
            "1.5 ≤ s_min < 2.0: marginal regime — judge together with Total Estimate, ΔRg, and "
            "the visual shape of p(r)."
        )
    elif shannon_class == "first_channel_lost":
        lines.append(
            "1.0 ≤ s_min < 1.5: the first Shannon channel is missing; rigid proteins need extra "
            "caution, flexible/polydisperse systems may still be acceptable."
        )
    return "\n".join(lines)


def delta_rg_pct(rg_guinier_nm: Optional[float], rg_pr_nm: Optional[float]) -> Optional[float]:
    if rg_guinier_nm is None or rg_pr_nm is None:
        return None
    try:
        rg_g = float(rg_guinier_nm)
        rg_p = float(rg_pr_nm)
    except (TypeError, ValueError):
        return None
    if not (np.isfinite(rg_g) and rg_g > 0 and np.isfinite(rg_p)):
        return None
    return float(abs(rg_g - rg_p) / rg_g * 100.0)


def classify_pr_quality(
    *,
    atsas_fit_ok: bool,
    total_estimate: Optional[float],
    delta_rg_pct_v: Optional[float],
    shannon_class: str,
    suspicious: bool,
    thresholds: Optional[PrQualityThresholds] = None,
) -> Tuple[str, List[str]]:
    t = thresholds or PrQualityThresholds()
    rationale: List[str] = []

    if not atsas_fit_ok:
        rationale.append("GNOM/DATGNOM did not complete successfully.")
        return "failed", rationale

    te = total_estimate
    try:
        te_v = float(te) if te is not None else float("nan")
    except (TypeError, ValueError):
        te_v = float("nan")

    if not np.isfinite(te_v) or te_v < t.total_estimate_min:
        rationale.append(
            f"Total Estimate {te_v:.3f} is below {t.total_estimate_min:.2f} — p(r) restoration is likely unphysical."
        )
        return "failed", rationale

    if suspicious:
        rationale.append("GNOM flagged the solution as SUSPICIOUS.")

    if shannon_class == "unreliable":
        rationale.append("Shannon class is unreliable (s_min ≥ 2.0).")

    drg = delta_rg_pct_v
    if drg is not None and np.isfinite(drg):
        if drg > t.delta_rg_pct_acceptable:
            rationale.append(f"ΔRg = {drg:.1f}% exceeds {t.delta_rg_pct_acceptable:.0f}% (Guinier vs p(r) Rg).")
            return "failed", rationale
        if drg > t.delta_rg_pct_max:
            rationale.append(f"ΔRg = {drg:.1f}% exceeds {t.delta_rg_pct_max:.0f}% (Guinier vs p(r) Rg).")

    blockers = [shannon_class == "unreliable", suspicious, drg is not None and drg > t.delta_rg_pct_max]
    if any(blockers):
        return "acceptable", rationale

    rationale.append(
        f"Total Estimate ≥ {t.total_estimate_min:.2f}, ΔRg within {t.delta_rg_pct_max:.0f}%, "
        "Shannon class acceptable."
    )
    return "high_quality", rationale


def build_pr_user_tips(
    *,
    pr_quality_class: str,
    total_estimate: Optional[float],
    delta_rg_pct_v: Optional[float],
    shannon_class: str,
    shannon_tip_text: str,
    suspicious: bool,
    thresholds: Optional[PrQualityThresholds] = None,
) -> List[str]:
    t = thresholds or PrQualityThresholds()
    tips: List[str] = [shannon_tip_text]

    te = total_estimate
    try:
        te_v = float(te) if te is not None else float("nan")
    except (TypeError, ValueError):
        te_v = float("nan")
    if np.isfinite(te_v) and te_v < t.total_estimate_min:
        tips.append(
            f"Total Estimate = {te_v:.3f} < {t.total_estimate_min:.2f}: treat p(r) as unreliable; "
            "review subtraction, Guinier range, and low-q data before downstream 3D modeling."
        )

    if delta_rg_pct_v is not None and np.isfinite(delta_rg_pct_v) and delta_rg_pct_v > t.delta_rg_pct_max:
        tips.append(
            f"ΔRg = {delta_rg_pct_v:.1f}% (Guinier Rg vs p(r) Rg): check Guinier interval, aggregation, "
            "or buffer mismatch before trusting the pair-distance distribution."
        )

    if suspicious:
        tips.append("GNOM marked this solution SUSPICIOUS — inspect p(r) for non-physical oscillations.")

    if pr_quality_class == "failed":
        tips.append("Overall p(r) quality: FAILED — address the issues above before quantitative interpretation.")
    elif pr_quality_class == "acceptable":
        tips.append("Overall p(r) quality: ACCEPTABLE — use with caution; see Shannon and ΔRg notes above.")
    else:
        tips.append("Overall p(r) quality: HIGH QUALITY by internal consistency checks.")

    return tips


def overall_status_from_class(quality_class: str) -> str:
    mapping = {
        "high_quality": "HIGH QUALITY",
        "acceptable": "ACCEPTABLE",
        "failed": "FAILED",
    }
    return mapping.get(quality_class, "FAILED")


def _detail_metrics_from_fit(
    parsed: Dict[str, Any],
    *,
    dmax_nm: Any,
    q_min_fit_nm: Optional[float],
    q_max_fit_nm: Optional[float],
    arrays: Optional[Tuple[np.ndarray, np.ndarray, Any]],
    chi2: Optional[float],
    chi2_good_min: float,
) -> Dict[str, Any]:
    """Shannon high-q + wiggle + detail reliability (no effect on overall class)."""
    s_max = None
    n_s = None
    if dmax_nm is not None and q_max_fit_nm is not None:
        s_max = shannon_s_max(float(q_max_fit_nm), float(dmax_nm))
    if dmax_nm is not None and q_min_fit_nm is not None and q_max_fit_nm is not None:
        n_s = n_shannon_channels(float(q_min_fit_nm), float(q_max_fit_nm), float(dmax_nm))

    wiggle_index = None
    if arrays is not None and q_max_fit_nm is not None:
        r, y, _err = arrays
        wiggle_index = wiggle_index_from_distribution(
            np.asarray(r, dtype=float),
            np.asarray(y, dtype=float),
            q_max_nm=float(q_max_fit_nm),
        )
    wiggle_class = classify_wiggle_index(wiggle_index)
    detail_class = classify_detail_reliability(
        wiggle_index=wiggle_index,
        chi2=chi2,
        n_shannon=n_s,
        chi2_good_min=chi2_good_min,
    )
    detail_tips = detail_reliability_tips(
        detail_reliability_class=detail_class,
        wiggle_index=wiggle_index,
        wiggle_class=wiggle_class,
        shannon_s_max=s_max,
        n_shannon=n_s,
    )
    return {
        "q_max_fit_nm": q_max_fit_nm,
        "shannon_s_max": s_max,
        "n_shannon": n_s,
        "wiggle_index": wiggle_index,
        "wiggle_class": wiggle_class,
        "detail_reliability_class": detail_class,
        "detail_tips": detail_tips,
    }


def analyze_pr_quality(
    parsed: Dict[str, Any],
    *,
    atsas_fit_ok: bool,
    rg_guinier_nm: Optional[float],
    q_nm: Optional[np.ndarray] = None,
    first_pt_1based: Optional[int] = None,
    last_pt_1based: Optional[int] = None,
    suspicious: bool = False,
    thresholds: Optional[PrQualityThresholds] = None,
    dmax_validation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute p(r) quality metrics from a parsed GNOM/DATGNOM .out."""
    t = thresholds or PrQualityThresholds()
    dmax_nm = parsed.get("real_space_rmax")
    total_estimate = parsed.get("total_estimate")

    pr = parsed.get("distribution")
    rg_pr_nm: Optional[float] = parsed.get("real_space_rg")
    i0_pr: Optional[float] = parsed.get("real_space_i0")
    arrays = distribution_arrays(pr)
    if arrays is not None:
        r, p, _err = arrays
        rg_integral = rg_from_pr(np.asarray(r, dtype=float), np.asarray(p, dtype=float))
        if rg_integral is not None:
            rg_pr_nm = rg_integral

    q_min_fit_nm = q_min_from_gnom_fit(
        parsed, q_nm=q_nm, first_pt_1based=first_pt_1based,
    )
    q_max_fit_nm = q_max_from_gnom_fit(
        parsed, q_nm=q_nm, last_pt_1based=last_pt_1based,
    )

    s_min = None
    s_class = "unknown"
    s_ok = None
    if dmax_nm is not None and q_min_fit_nm is not None:
        s_min = shannon_s_min(q_min_fit_nm, float(dmax_nm))
        s_class = classify_shannon(s_min)
        s_ok = shannon_ok(q_min_fit_nm, float(dmax_nm))

    drg = delta_rg_pct(rg_guinier_nm, rg_pr_nm)
    pr_class, rationale = classify_pr_quality(
        atsas_fit_ok=atsas_fit_ok,
        total_estimate=total_estimate,
        delta_rg_pct_v=drg,
        shannon_class=s_class,
        suspicious=suspicious,
        thresholds=t,
    )
    chi2 = chi2_from_iq_table(parsed.get("iq_table"))
    chi2_class = classify_chi2(
        chi2,
        good_min=t.chi2_good_min,
        good_max=t.chi2_good_max,
        acceptable_min=t.chi2_acceptable_min,
        acceptable_max=t.chi2_acceptable_max,
    )
    if chi2_class in ("acceptable", "failed"):
        pr_class = _downgrade_quality(pr_class, chi2_class)
        if chi2 is not None and chi2_class == "failed":
            rationale = list(rationale) + [f"Reduced χ² = {float(chi2):.3f} is outside acceptable range."]
        elif chi2 is not None and chi2_class == "acceptable":
            rationale = list(rationale) + [f"Reduced χ² = {float(chi2):.3f} is only marginally acceptable."]

    detail = _detail_metrics_from_fit(
        parsed,
        dmax_nm=dmax_nm,
        q_min_fit_nm=q_min_fit_nm,
        q_max_fit_nm=q_max_fit_nm,
        arrays=arrays,
        chi2=chi2,
        chi2_good_min=t.chi2_good_min,
    )

    s_tip = shannon_tip(s_min, s_class)
    user_tips = build_pr_user_tips(
        pr_quality_class=pr_class,
        total_estimate=total_estimate,
        delta_rg_pct_v=drg,
        shannon_class=s_class,
        shannon_tip_text=s_tip,
        suspicious=suspicious,
        thresholds=t,
    )
    for tip in detail.get("detail_tips") or []:
        if tip and tip not in user_tips:
            user_tips.append(tip)

    out: Dict[str, Any] = {
        "dmax_nm": dmax_nm,
        "rg_pr_nm": rg_pr_nm,
        "i0_pr": i0_pr,
        "rg_guinier_nm": rg_guinier_nm,
        "q_min_fit_nm": q_min_fit_nm,
        "q_max_fit_nm": detail["q_max_fit_nm"],
        "total_estimate": total_estimate,
        "chi2": chi2,
        "chi2_class": chi2_class,
        "delta_rg_pct": drg,
        "shannon_s_min": s_min,
        "shannon_s_max": detail["shannon_s_max"],
        "n_shannon": detail["n_shannon"],
        "shannon_class": s_class,
        "shannon_ok": s_ok,
        "shannon_tip": s_tip,
        "wiggle_index": detail["wiggle_index"],
        "wiggle_class": detail["wiggle_class"],
        "detail_reliability_class": detail["detail_reliability_class"],
        "pr_quality_class": pr_class,
        "overall_status": overall_status_from_class(pr_class),
        "quality_rationale": rationale,
        "user_tips": user_tips,
    }

    if dmax_validation:
        out["dmax_validation"] = dmax_validation
        for tip in dmax_validation.get("user_tips") or []:
            if tip and tip not in out["user_tips"]:
                out["user_tips"].append(tip)
        severity = str(dmax_validation.get("severity") or "ok")
        if severity == "failed" and out["pr_quality_class"] != "failed":
            out["quality_rationale"] = list(out["quality_rationale"]) + [
                "Dmax validation failed (force-zero-off / ensemble pathology).",
            ]
            out["pr_quality_class"] = "failed"
            out["overall_status"] = overall_status_from_class("failed")
        # Warnings add tips only; they do not downgrade pr_quality_class (DAM gating).

    return out


def _tail_mean_signed(r: np.ndarray, p: np.ndarray, *, frac: float = 0.1) -> Optional[float]:
    r = np.asarray(r, dtype=float)
    p = np.asarray(p, dtype=float)
    m = np.isfinite(r) & np.isfinite(p)
    r, p = r[m], p[m]
    if r.size < 5:
        return None
    n_tail = max(3, int(math.ceil(frac * r.size)))
    return float(np.nanmean(p[-n_tail:]))


def _abrupt_zero_at_dmax(r: np.ndarray, p: np.ndarray) -> Optional[bool]:
    """True if p(r) drops abruptly into the forced zero at Dmax (cliff)."""
    r = np.asarray(r, dtype=float)
    p = np.asarray(p, dtype=float)
    m = np.isfinite(r) & np.isfinite(p)
    r, p = r[m], p[m]
    if r.size < 6:
        return None
    p_abs_max = float(np.nanmax(np.abs(p)))
    if not np.isfinite(p_abs_max) or p_abs_max <= 0:
        return None
    if abs(float(p[-1])) > 1e-6 * p_abs_max:
        return False
    prev = float(p[-2])
    return bool(prev / p_abs_max > 0.15)


def analyze_dmax_validation(
    *,
    best_parsed: Dict[str, Any],
    ensemble_rows: List[Dict[str, Any]],
    force_zero_off_parsed: Optional[Dict[str, Any]],
    dmax_ref_nm: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Automated Dmax validation from a close-fits ensemble and a force-zero-off run.

    ``ensemble_rows`` entries should include at least ``total_estimate``, ``neg_frac``,
    ``rg_pr_nm`` (optional), and ``ok``.

    When ``force_zero_off_parsed`` comes from an extended-rmax probe, pass ``dmax_ref_nm``
    so pathology is scored on the region *beyond* the putative Dmax (aggregation /
    repulsion), not on the whole curve.
    """
    tips: List[str] = []
    severity = "ok"

    best_arrays = distribution_arrays(best_parsed.get("distribution"))
    abrupt: Optional[bool] = None
    if best_arrays is not None:
        abrupt = _abrupt_zero_at_dmax(best_arrays[0], best_arrays[1])
        if abrupt:
            tips.append(
                "p(r) approaches Dmax abruptly (possible underestimated Dmax); "
                "inspect the close-fits ensemble."
            )
            severity = "warning"

    ok_rows = [row for row in ensemble_rows if row.get("ok")]
    te_vals: List[float] = []
    rg_vals: List[float] = []
    neg_vals: List[float] = []
    for row in ok_rows:
        try:
            te = float(row.get("total_estimate"))
            if np.isfinite(te):
                te_vals.append(te)
        except (TypeError, ValueError):
            pass
        try:
            rg = float(row.get("rg_pr_nm"))
            if np.isfinite(rg):
                rg_vals.append(rg)
        except (TypeError, ValueError):
            pass
        try:
            nf = float(row.get("neg_frac"))
            if np.isfinite(nf):
                neg_vals.append(nf)
        except (TypeError, ValueError):
            pass

    te_span = float(max(te_vals) - min(te_vals)) if len(te_vals) >= 2 else None
    rg_span_pct: Optional[float] = None
    if len(rg_vals) >= 2:
        rg_mid = 0.5 * (max(rg_vals) + min(rg_vals))
        if rg_mid > 0:
            rg_span_pct = float((max(rg_vals) - min(rg_vals)) / rg_mid * 100.0)

    if te_span is not None and te_span > 0.15:
        tips.append(
            f"Close-fits ensemble Total Estimate spans {te_span:.3f} across Dmax±10% — "
            "Dmax may be unstable."
        )
        severity = "warning" if severity == "ok" else severity
    if rg_span_pct is not None and rg_span_pct > 10.0:
        tips.append(
            f"Close-fits ensemble Rg(p(r)) spans {rg_span_pct:.1f}% across Dmax±10%."
        )
        severity = "warning" if severity == "ok" else severity
    if neg_vals and max(neg_vals) > 0.05:
        tips.append(
            "Some close-fits ensemble members show elevated negativity — inspect p(r) tails."
        )
        severity = "warning" if severity == "ok" else severity

    force_tail: Optional[float] = None
    force_pathology: Optional[str] = None
    if force_zero_off_parsed is not None:
        fz_arrays = distribution_arrays(force_zero_off_parsed.get("distribution"))
        if fz_arrays is not None:
            r_fz = np.asarray(fz_arrays[0], dtype=float)
            p_fz = np.asarray(fz_arrays[1], dtype=float)
            p_abs_max = float(np.nanmax(np.abs(p_fz))) if p_fz.size else 0.0
            # Prefer the region beyond putative Dmax when an extended probe was used.
            if (
                dmax_ref_nm is not None
                and np.isfinite(float(dmax_ref_nm))
                and float(dmax_ref_nm) > 0
                and r_fz.size
            ):
                beyond = r_fz >= float(dmax_ref_nm)
                if np.any(beyond):
                    force_tail = float(np.nanmean(p_fz[beyond]))
                else:
                    force_tail = _tail_mean_signed(r_fz, p_fz)
            else:
                force_tail = _tail_mean_signed(r_fz, p_fz)
            if force_tail is not None and np.isfinite(p_abs_max) and p_abs_max > 0:
                rel = force_tail / p_abs_max
                if rel > 0.20:
                    force_pathology = "aggregation"
                    tips.append(
                        "Extended force-zero-off probe: p(r) stays strongly positive past Dmax "
                        "(possible aggregation or underestimated Dmax)."
                    )
                    severity = "failed"
                elif rel > 0.08:
                    force_pathology = "aggregation_mild"
                    tips.append(
                        "Extended force-zero-off probe: p(r) is mildly positive past Dmax — "
                        "check for weak aggregation or slightly low Dmax."
                    )
                    severity = "warning" if severity == "ok" else severity
                elif rel < -0.08:
                    force_pathology = "repulsion"
                    tips.append(
                        "Extended force-zero-off probe: p(r) goes systematically negative past Dmax "
                        "(possible interparticle repulsion / structure factor)."
                    )
                    severity = "failed"
                elif rel < -0.03:
                    force_pathology = "repulsion_mild"
                    tips.append(
                        "Extended force-zero-off probe: p(r) is mildly negative past Dmax — "
                        "check for weak structure-factor / repulsion."
                    )
                    severity = "warning" if severity == "ok" else severity
                else:
                    tips.append(
                        "Force-zero-off check: p(r) past Dmax is consistent with a natural decay to zero."
                    )

    return {
        "abrupt_zero_at_dmax": abrupt,
        "ensemble_n_ok": len(ok_rows),
        "ensemble_te_span": te_span,
        "ensemble_rg_span_pct": rg_span_pct,
        "ensemble_neg_frac_max": float(max(neg_vals)) if neg_vals else None,
        "force_zero_off_tail_mean": force_tail,
        "force_zero_off_pathology": force_pathology,
        "dmax_ref_nm": dmax_ref_nm,
        "severity": severity,
        "user_tips": tips,
    }


def analyze_rmax_validation(
    *,
    best_parsed: Dict[str, Any],
    ensemble_rows: List[Dict[str, Any]],
    force_zero_off_parsed: Optional[Dict[str, Any]],
    rmax_ref_nm: Optional[float] = None,
) -> Dict[str, Any]:
    """Rmax validation for polydisperse D(R) (close fits + force-zero-off probe)."""
    raw = analyze_dmax_validation(
        best_parsed=best_parsed,
        ensemble_rows=ensemble_rows,
        force_zero_off_parsed=force_zero_off_parsed,
        dmax_ref_nm=rmax_ref_nm,
    )
    tips = []
    for tip in raw.get("user_tips") or []:
        tips.append(
            str(tip)
            .replace("p(r)", "D(R)")
            .replace("Dmax", "Rmax")
            .replace("Rg(p(r))", "peak position")
        )
    out = dict(raw)
    out["user_tips"] = tips
    out["rmax_ref_nm"] = rmax_ref_nm
    out.pop("dmax_ref_nm", None)
    out["abrupt_zero_at_rmax"] = out.pop("abrupt_zero_at_dmax", None)
    return out


def classify_stability(
    *,
    ensemble_rows: List[Dict[str, Any]],
    rmax_validation: Optional[Dict[str, Any]] = None,
) -> str:
    """Classify D(R) stability from close-fit ensemble and force-zero-off probe."""
    ok_rows = [r for r in ensemble_rows if r.get("ok")]
    if len(ok_rows) < 2:
        return "unstable"

    peak_vals: List[float] = []
    pdi_vals: List[float] = []
    for row in ok_rows:
        try:
            pr = float(row.get("peak_r_nm"))
            if np.isfinite(pr):
                peak_vals.append(pr)
        except (TypeError, ValueError):
            pass
        try:
            pdi = float(row.get("pdi"))
            if np.isfinite(pdi):
                pdi_vals.append(pdi)
        except (TypeError, ValueError):
            pass

    peak_span_pct: Optional[float] = None
    if len(peak_vals) >= 2:
        mid = 0.5 * (max(peak_vals) + min(peak_vals))
        if mid > 0:
            peak_span_pct = float((max(peak_vals) - min(peak_vals)) / mid * 100.0)

    pdi_span: Optional[float] = None
    if len(pdi_vals) >= 2:
        pdi_span = float(max(pdi_vals) - min(pdi_vals))

    severity = str((rmax_validation or {}).get("severity") or "ok")
    if severity == "failed":
        return "unstable"
    if peak_span_pct is not None and peak_span_pct > 15.0:
        return "unstable"
    if pdi_span is not None and pdi_span > 0.15:
        return "marginal"
    te_span = (rmax_validation or {}).get("ensemble_te_span")
    try:
        if te_span is not None and float(te_span) > 0.15:
            return "marginal"
    except (TypeError, ValueError):
        pass
    if severity == "warning":
        return "marginal"
    if peak_span_pct is not None and peak_span_pct > 8.0:
        return "marginal"
    return "stable"


def _find_local_maxima(r: np.ndarray, d: np.ndarray) -> List[float]:
    r = np.asarray(r, dtype=float)
    d = np.asarray(d, dtype=float)
    if r.size < 3:
        return []
    peaks: List[float] = []
    for i in range(1, len(d) - 1):
        if not (np.isfinite(d[i - 1]) and np.isfinite(d[i]) and np.isfinite(d[i + 1])):
            continue
        if d[i] >= d[i - 1] and d[i] > d[i + 1] and d[i] > 0:
            peaks.append(float(r[i]))
    if not peaks and d.size:
        i_max = int(np.nanargmax(d))
        if np.isfinite(d[i_max]) and d[i_max] > 0:
            peaks.append(float(r[i_max]))
    return peaks


def dr_distribution_moments(r: np.ndarray, d: np.ndarray) -> Dict[str, Optional[float]]:
    r = np.asarray(r, dtype=float)
    d = np.asarray(d, dtype=float)
    mask = np.isfinite(r) & np.isfinite(d) & (d >= 0)
    r, d = r[mask], d[mask]
    if r.size < 2:
        return {"d_avg_nm": None, "d_std_nm": None, "pdi": None}
    w = np.trapezoid(d, r)
    if not np.isfinite(w) or w <= 0:
        return {"d_avg_nm": None, "d_std_nm": None, "pdi": None}
    mean = float(np.trapezoid(r * d, r) / w)
    mean_sq = float(np.trapezoid((r ** 2) * d, r) / w)
    var = max(0.0, mean_sq - mean ** 2)
    std = float(math.sqrt(var))
    pdi = float(std / mean) if mean > 0 else None
    return {"d_avg_nm": mean, "d_std_nm": std, "pdi": pdi}


def classify_modality(n_peaks: int, pdi: Optional[float], thresholds: DrQualityThresholds) -> str:
    if n_peaks <= 0:
        return "unknown"
    if n_peaks >= 2:
        return "multimodal"
    if pdi is None or not np.isfinite(pdi):
        return "unimodal_polydisperse"
    if pdi <= thresholds.pdi_monodisperse_max:
        return "monodisperse"
    return "unimodal_polydisperse"


def classify_sizes_quality(
    *,
    atsas_fit_ok: bool,
    total_estimate: Optional[float],
    parse_dr_ok: bool,
    neg_frac: Optional[float],
    thresholds: Optional[DrQualityThresholds] = None,
) -> Tuple[str, List[str]]:
    t = thresholds or DrQualityThresholds()
    rationale: List[str] = []

    if not atsas_fit_ok:
        rationale.append("GNOM did not complete successfully.")
        return "failed", rationale

    if not parse_dr_ok:
        rationale.append("D(R) could not be parsed from the GNOM .out.")
        return "failed", rationale

    try:
        te_v = float(total_estimate) if total_estimate is not None else float("nan")
    except (TypeError, ValueError):
        te_v = float("nan")
    if not np.isfinite(te_v) or te_v < t.total_estimate_min:
        rationale.append(
            f"Total Estimate {te_v:.3f} is below {t.total_estimate_min:.2f}."
        )
        return "failed", rationale

    nf = neg_frac
    try:
        nf_v = float(nf) if nf is not None else 0.0
    except (TypeError, ValueError):
        nf_v = 0.0
    if nf_v > 0.2:
        rationale.append(f"Negative fraction in D(R) is {nf_v:.2f}.")
        return "acceptable", rationale

    rationale.append(f"Total Estimate ≥ {t.total_estimate_min:.2f} and D(R) parsed successfully.")
    return "high_quality", rationale


def build_sizes_user_tips(
    *,
    sizes_quality_class: str,
    modality_class: str,
    pdi: Optional[float],
    d_avg_nm: Optional[float],
    d_std_nm: Optional[float],
    total_estimate: Optional[float],
    shape: str,
    thresholds: Optional[DrQualityThresholds] = None,
    mixture_dist_hint: Optional[str] = None,
    n_components_suggested: Optional[int] = None,
    parametric_family: Optional[str] = None,
    stability_class: Optional[str] = None,
    modality_confidence: Optional[str] = None,
) -> List[str]:
    t = thresholds or DrQualityThresholds()
    tips: List[str] = []

    if d_avg_nm is not None and np.isfinite(d_avg_nm):
        if d_std_nm is not None and np.isfinite(d_std_nm):
            tips.append(f"Mean size ⟨R⟩ = {d_avg_nm:.3g} ± {d_std_nm:.3g} nm (from D(R)).")
        else:
            tips.append(f"Mean size ⟨R⟩ = {d_avg_nm:.3g} nm (from D(R)).")
    if pdi is not None and np.isfinite(pdi):
        tips.append(f"Polydispersity index PDI = σ/⟨R⟩ = {pdi:.3f}.")

    if modality_class == "monodisperse":
        tips.append("Narrow unimodal D(R) — sample appears monodisperse in size.")
    elif modality_class == "multimodal":
        tips.append(
            "Multiple peaks in D(R) suggest several populations; consider mixture analysis "
            "or separate fractions."
        )
    elif modality_class == "unimodal_polydisperse":
        tips.append("Broad unimodal D(R) — typical for polydisperse sols or nanoparticle batches.")

    if parametric_family and parametric_family != "unknown":
        tips.append(f"Parametric hint on D(R): {parametric_family} (preliminary).")
    if mixture_dist_hint and n_components_suggested is not None:
        tips.append(
            f"Suggested model_mixture starting point: {mixture_dist_hint} distribution, "
            f"{int(n_components_suggested)} phase(s)."
        )
    if modality_confidence == "low":
        tips.append("Modality ambiguous — confirm with model_mixture before trusting component count.")
    if stability_class == "unstable":
        tips.append("Rmax ensemble unstable — treat D(R) as a rough hint only.")
    elif stability_class == "marginal":
        tips.append("Rmax ensemble marginally stable — verify before model_mixture.")

    try:
        te_v = float(total_estimate) if total_estimate is not None else float("nan")
    except (TypeError, ValueError):
        te_v = float("nan")
    if np.isfinite(te_v) and te_v < t.total_estimate_min:
        tips.append(
            f"Total Estimate = {te_v:.3f} < {t.total_estimate_min:.2f}: treat D(R) as unreliable."
        )

    if shape.strip().lower() in ("rod", "rods", "cylinder", "cylinders"):
        tips.append("For rods, D(R) is a length distribution; cylinder radius (--rad56) affects the model.")

    if sizes_quality_class == "failed":
        tips.append("Overall D(R) quality: FAILED.")
    elif sizes_quality_class == "acceptable":
        tips.append("Overall D(R) quality: ACCEPTABLE — use with caution.")
    else:
        tips.append("Overall D(R) quality: HIGH QUALITY by internal checks.")

    return tips


def apply_sizes_extended_quality(
    quality: Dict[str, Any],
    *,
    parametric: Optional[Dict[str, Any]] = None,
    ensemble_info: Optional[Dict[str, Any]] = None,
    shape: str = "spheres",
    thresholds: Optional[DrQualityThresholds] = None,
) -> Dict[str, Any]:
    """Merge parametric hints, ensemble stability, and rmax validation into quality dict."""
    t = thresholds or DrQualityThresholds()
    out = dict(quality)
    parametric = parametric or {}
    ensemble_info = ensemble_info or {}
    rmax_validation = ensemble_info.get("rmax_validation")
    stability_class = ensemble_info.get("stability_class") or "unknown"

    for key in (
        "parametric_family",
        "parametric_aic",
        "parametric_R0_nm",
        "parametric_width_nm",
        "n_components_suggested",
        "mixture_dist_hint",
        "parametric_peaks_nm",
        "modality_confidence",
    ):
        if key in parametric:
            out[key] = parametric[key]

    out["stability_class"] = stability_class
    out["ensemble_dir"] = ensemble_info.get("ensemble_dir") or ""
    out["ensemble_summary_path"] = ensemble_info.get("ensemble_summary_path") or ""
    out["close_fit_out_paths"] = list(ensemble_info.get("close_fit_out_paths") or [])
    out["force_zero_off_out_path"] = ensemble_info.get("force_zero_off_out_path") or ""
    if rmax_validation:
        out["rmax_validation"] = rmax_validation
        pathology = rmax_validation.get("force_zero_off_pathology")
        out["force_zero_off_pathology"] = bool(
            pathology in ("aggregation", "aggregation_mild", "repulsion", "repulsion_mild")
        )
        for tip in rmax_validation.get("user_tips") or []:
            if tip and tip not in out.get("user_tips", []):
                out.setdefault("user_tips", []).append(tip)

    sizes_class = str(out.get("sizes_quality_class") or "failed")
    rationale = list(out.get("quality_rationale") or [])
    if stability_class == "unstable" and sizes_class == "high_quality":
        sizes_class = "acceptable"
        rationale.append("Rmax ensemble unstable — downgraded to acceptable.")
    if rmax_validation and str(rmax_validation.get("severity")) == "failed":
        if sizes_class == "high_quality":
            sizes_class = "acceptable"
        rationale.append("Force-zero-off Rmax validation raised concerns.")
    out["sizes_quality_class"] = sizes_class
    out["overall_status"] = overall_status_from_class(sizes_class)
    out["quality_rationale"] = rationale

    out["user_tips"] = build_sizes_user_tips(
        sizes_quality_class=sizes_class,
        modality_class=str(out.get("modality_class") or "unknown"),
        pdi=out.get("pdi"),
        d_avg_nm=out.get("d_avg_nm"),
        d_std_nm=out.get("d_std_nm"),
        total_estimate=out.get("total_estimate"),
        shape=shape,
        thresholds=t,
        mixture_dist_hint=out.get("mixture_dist_hint"),
        n_components_suggested=out.get("n_components_suggested"),
        parametric_family=out.get("parametric_family"),
        stability_class=stability_class,
        modality_confidence=out.get("modality_confidence"),
    )
    if out.get("shannon_tip"):
        out["user_tips"] = list(out["user_tips"]) + [str(out["shannon_tip"])]
    detail_tips = detail_reliability_tips(
        detail_reliability_class=str(out.get("detail_reliability_class") or "unknown"),
        wiggle_index=out.get("wiggle_index"),
        wiggle_class=str(out.get("wiggle_class") or "unknown"),
        shannon_s_max=out.get("shannon_s_max"),
        n_shannon=out.get("n_shannon"),
    )
    for tip in detail_tips:
        if tip and tip not in out["user_tips"]:
            out["user_tips"].append(tip)
    return out


def analyze_dr_quality(
    parsed: Dict[str, Any],
    *,
    atsas_fit_ok: bool,
    rg_guinier_nm: Optional[float],
    shape: str,
    neg_frac: Optional[float] = None,
    thresholds: Optional[DrQualityThresholds] = None,
    q_nm: Optional[np.ndarray] = None,
    first_pt_1based: Optional[int] = None,
    last_pt_1based: Optional[int] = None,
) -> Dict[str, Any]:
    t = thresholds or DrQualityThresholds()
    dr = parsed.get("distribution")
    parse_dr_ok = dr is not None
    moments: Dict[str, Optional[float]] = {"d_avg_nm": None, "d_std_nm": None, "pdi": None}
    peaks: List[float] = []
    arrays = distribution_arrays(dr)
    if arrays is not None:
        r, d, _err = arrays
        r_arr = np.asarray(r, dtype=float)
        d_arr = np.asarray(d, dtype=float)
        moments = dr_distribution_moments(r_arr, d_arr)
        peaks = _find_local_maxima(r_arr, d_arr)

    dmax_nm = parsed.get("real_space_rmax")
    q_min_fit_nm = q_min_from_gnom_fit(
        parsed, q_nm=q_nm, first_pt_1based=first_pt_1based,
    )
    q_max_fit_nm = q_max_from_gnom_fit(
        parsed, q_nm=q_nm, last_pt_1based=last_pt_1based,
    )
    s_min = None
    s_class = "unknown"
    s_ok = None
    if dmax_nm is not None and q_min_fit_nm is not None:
        s_min = shannon_s_min(q_min_fit_nm, float(dmax_nm))
        s_class = classify_shannon(s_min)
        s_ok = shannon_ok(q_min_fit_nm, float(dmax_nm))
    s_tip = shannon_tip(s_min, s_class)

    modality = classify_modality(len(peaks), moments.get("pdi"), t)
    sizes_class, rationale = classify_sizes_quality(
        atsas_fit_ok=atsas_fit_ok,
        total_estimate=parsed.get("total_estimate"),
        parse_dr_ok=parse_dr_ok,
        neg_frac=neg_frac,
        thresholds=t,
    )
    chi2 = chi2_from_iq_table(parsed.get("iq_table"))
    chi2_class = classify_chi2(
        chi2,
        good_min=t.chi2_good_min,
        good_max=t.chi2_good_max,
        acceptable_min=t.chi2_acceptable_min,
        acceptable_max=t.chi2_acceptable_max,
    )
    if chi2_class in ("acceptable", "failed"):
        sizes_class = _downgrade_quality(sizes_class, chi2_class)
        if chi2 is not None and chi2_class == "failed":
            rationale = list(rationale) + [f"Reduced χ² = {float(chi2):.3f} is outside acceptable range."]
        elif chi2 is not None and chi2_class == "acceptable":
            rationale = list(rationale) + [f"Reduced χ² = {float(chi2):.3f} is only marginally acceptable."]

    detail = _detail_metrics_from_fit(
        parsed,
        dmax_nm=dmax_nm,
        q_min_fit_nm=q_min_fit_nm,
        q_max_fit_nm=q_max_fit_nm,
        arrays=arrays,
        chi2=chi2,
        chi2_good_min=t.chi2_good_min,
    )

    user_tips = build_sizes_user_tips(
        sizes_quality_class=sizes_class,
        modality_class=modality,
        pdi=moments.get("pdi"),
        d_avg_nm=moments.get("d_avg_nm"),
        d_std_nm=moments.get("d_std_nm"),
        total_estimate=parsed.get("total_estimate"),
        shape=shape,
        thresholds=t,
    )
    if s_tip:
        user_tips = list(user_tips) + [s_tip]
    for tip in detail.get("detail_tips") or []:
        if tip and tip not in user_tips:
            user_tips.append(tip)

    return {
        "d_avg_nm": moments.get("d_avg_nm"),
        "d_std_nm": moments.get("d_std_nm"),
        "pdi": moments.get("pdi"),
        "dr_peak_positions_nm": peaks,
        "dr_n_peaks": len(peaks),
        "modality_class": modality,
        "rg_guinier_nm": rg_guinier_nm,
        "dmax_nm": dmax_nm,
        "q_min_fit_nm": q_min_fit_nm,
        "q_max_fit_nm": detail["q_max_fit_nm"],
        "total_estimate": parsed.get("total_estimate"),
        "chi2": chi2,
        "chi2_class": chi2_class,
        "shannon_s_min": s_min,
        "shannon_s_max": detail["shannon_s_max"],
        "n_shannon": detail["n_shannon"],
        "shannon_class": s_class,
        "shannon_ok": s_ok,
        "shannon_tip": s_tip,
        "wiggle_index": detail["wiggle_index"],
        "wiggle_class": detail["wiggle_class"],
        "detail_reliability_class": detail["detail_reliability_class"],
        "sizes_quality_class": sizes_class,
        "overall_status": overall_status_from_class(sizes_class),
        "quality_rationale": rationale,
        "user_tips": user_tips,
    }


def write_quality_passport_yaml(path: str, doc: Dict[str, Any]) -> None:
    import yaml

    from autosaxs.core.utils import _make_yaml_safe

    with open(path, "w", encoding="utf-8") as fp:
        yaml.dump(
            _make_yaml_safe(doc),
            fp,
            default_flow_style=False,
            allow_unicode=True,
        )


def detail_indicators_from_gnom_out(gnom_path: Optional[str]) -> Dict[str, Any]:
    """
    Informational Shannon / wiggle / detail-reliability fields from a GNOM ``.out``.

    Prefer a sibling ``*_fit_distances_quality.yml`` when present; otherwise compute
    from the parsed ``.out``. Never gates modeling — indicator only.
    """
    empty: Dict[str, Any] = {
        "detail_reliability_class": "unknown",
        "wiggle_index": None,
        "wiggle_class": "unknown",
        "n_shannon": None,
        "shannon_s_max": None,
    }
    if not gnom_path:
        return empty
    path = str(gnom_path)
    if not os.path.isfile(path):
        return empty

    # Sibling quality passport written by fit_distances, if available.
    parent = os.path.dirname(path)
    base = os.path.splitext(os.path.basename(path))[0]
    for cand in (
        os.path.join(parent, f"{base}_fit_distances_quality.yml"),
        os.path.join(parent, "..", f"{base}_fit_distances_quality.yml"),
    ):
        cand = os.path.normpath(cand)
        if os.path.isfile(cand):
            try:
                import yaml

                with open(cand, "r", encoding="utf-8") as fp:
                    doc = yaml.safe_load(fp) or {}
                if isinstance(doc, dict):
                    out = dict(empty)
                    for key in empty:
                        if key in doc:
                            out[key] = doc.get(key)
                    return out
            except Exception:
                pass

    try:
        from autosaxs.core.gnom import parse_gnom_out

        parsed = parse_gnom_out(path)
        quality = analyze_pr_quality(
            parsed,
            atsas_fit_ok=True,
            rg_guinier_nm=None,
        )
    except Exception:
        return empty
    return {
        "detail_reliability_class": quality.get("detail_reliability_class") or "unknown",
        "wiggle_index": quality.get("wiggle_index"),
        "wiggle_class": quality.get("wiggle_class") or "unknown",
        "n_shannon": quality.get("n_shannon"),
        "shannon_s_max": quality.get("shannon_s_max"),
    }


def detail_indicators_markdown(ind: Dict[str, Any]) -> str:
    """Short report lines for modeling skills (indicator only)."""
    lines = ["\n#### GNOM detail reliability (indicator)\n"]
    det = ind.get("detail_reliability_class") or "unknown"
    lines.append(f"- **Detail reliability:** {det}")
    n_s = ind.get("n_shannon")
    if n_s is not None:
        try:
            lines.append(f"\n- **n_shannon:** {float(n_s):.2f}")
        except (TypeError, ValueError):
            pass
    s_max = ind.get("shannon_s_max")
    if s_max is not None:
        try:
            lines.append(f"\n- **Shannon s_max:** {float(s_max):.3f}")
        except (TypeError, ValueError):
            pass
    wig = ind.get("wiggle_index")
    if wig is not None:
        try:
            lines.append(
                f"\n- **Wiggle index:** {float(wig):.3f} ({ind.get('wiggle_class', 'unknown')})"
            )
        except (TypeError, ValueError):
            pass
    return "".join(lines) + "\n"
