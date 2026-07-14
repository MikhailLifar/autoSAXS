"""Post-hoc GNOM/DATGNOM quality metrics and classification (no fitting)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

DEFAULT_TOTAL_ESTIMATE_MIN = 0.55
DEFAULT_DELTA_RG_PCT_MAX = 10.0
DEFAULT_DELTA_RG_PCT_ACCEPTABLE = 15.0
DEFAULT_PDI_MONODISPERSE_MAX = 0.10
DEFAULT_PDI_POLYDISPERSE_MAX = 0.30


@dataclass(frozen=True)
class PrQualityThresholds:
    total_estimate_min: float = DEFAULT_TOTAL_ESTIMATE_MIN
    delta_rg_pct_max: float = DEFAULT_DELTA_RG_PCT_MAX
    delta_rg_pct_acceptable: float = DEFAULT_DELTA_RG_PCT_ACCEPTABLE


@dataclass(frozen=True)
class DrQualityThresholds:
    total_estimate_min: float = DEFAULT_TOTAL_ESTIMATE_MIN
    pdi_monodisperse_max: float = DEFAULT_PDI_MONODISPERSE_MAX
    pdi_polydisperse_max: float = DEFAULT_PDI_POLYDISPERSE_MAX


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


def shannon_s_min(q_min_nm: float, dmax_nm: float) -> Optional[float]:
    if not (np.isfinite(q_min_nm) and q_min_nm > 0 and np.isfinite(dmax_nm) and dmax_nm > 0):
        return None
    return float((q_min_nm * dmax_nm) / math.pi)


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


def analyze_pr_quality(
    parsed: Dict[str, Any],
    *,
    atsas_fit_ok: bool,
    rg_guinier_nm: Optional[float],
    q_nm: Optional[np.ndarray] = None,
    first_pt_1based: Optional[int] = None,
    suspicious: bool = False,
    thresholds: Optional[PrQualityThresholds] = None,
) -> Dict[str, Any]:
    """Compute p(r) quality metrics from a parsed GNOM/DATGNOM .out."""
    t = thresholds or PrQualityThresholds()
    dmax_nm = parsed.get("real_space_rmax")
    total_estimate = parsed.get("total_estimate")

    pr = parsed.get("distribution")
    rg_pr_nm: Optional[float] = parsed.get("real_space_rg")
    i0_pr: Optional[float] = parsed.get("real_space_i0")
    if pr is not None:
        r, p = pr
        rg_integral = rg_from_pr(np.asarray(r, dtype=float), np.asarray(p, dtype=float))
        if rg_integral is not None:
            rg_pr_nm = rg_integral

    q_min_fit_nm = q_min_from_gnom_fit(
        parsed, q_nm=q_nm, first_pt_1based=first_pt_1based,
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

    return {
        "dmax_nm": dmax_nm,
        "rg_pr_nm": rg_pr_nm,
        "i0_pr": i0_pr,
        "rg_guinier_nm": rg_guinier_nm,
        "q_min_fit_nm": q_min_fit_nm,
        "total_estimate": total_estimate,
        "delta_rg_pct": drg,
        "shannon_s_min": s_min,
        "shannon_class": s_class,
        "shannon_ok": s_ok,
        "shannon_tip": s_tip,
        "pr_quality_class": pr_class,
        "overall_status": overall_status_from_class(pr_class),
        "quality_rationale": rationale,
        "user_tips": user_tips,
    }


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


def analyze_dr_quality(
    parsed: Dict[str, Any],
    *,
    atsas_fit_ok: bool,
    rg_guinier_nm: Optional[float],
    shape: str,
    neg_frac: Optional[float] = None,
    thresholds: Optional[DrQualityThresholds] = None,
) -> Dict[str, Any]:
    t = thresholds or DrQualityThresholds()
    dr = parsed.get("distribution")
    parse_dr_ok = dr is not None
    moments: Dict[str, Optional[float]] = {"d_avg_nm": None, "d_std_nm": None, "pdi": None}
    peaks: List[float] = []
    if dr is not None:
        r, d = dr
        r_arr = np.asarray(r, dtype=float)
        d_arr = np.asarray(d, dtype=float)
        moments = dr_distribution_moments(r_arr, d_arr)
        peaks = _find_local_maxima(r_arr, d_arr)

    modality = classify_modality(len(peaks), moments.get("pdi"), t)
    sizes_class, rationale = classify_sizes_quality(
        atsas_fit_ok=atsas_fit_ok,
        total_estimate=parsed.get("total_estimate"),
        parse_dr_ok=parse_dr_ok,
        neg_frac=neg_frac,
        thresholds=t,
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

    return {
        "d_avg_nm": moments.get("d_avg_nm"),
        "d_std_nm": moments.get("d_std_nm"),
        "pdi": moments.get("pdi"),
        "dr_peak_positions_nm": peaks,
        "dr_n_peaks": len(peaks),
        "modality_class": modality,
        "rg_guinier_nm": rg_guinier_nm,
        "total_estimate": parsed.get("total_estimate"),
        "sizes_quality_class": sizes_class,
        "overall_status": overall_status_from_class(sizes_class),
        "quality_rationale": rationale,
        "user_tips": user_tips,
    }


def write_quality_passport_yaml(path: str, doc: Dict[str, Any]) -> None:
    import yaml

    with open(path, "w", encoding="utf-8") as fp:
        yaml.dump(doc, fp, default_flow_style=False, allow_unicode=True)
