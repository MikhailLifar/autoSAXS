"""Interpretable GNOM candidate scoring: soft-taper / shape_tight_extent.

Product default for auto ``fit_distances`` / ``fit_sizes`` search (AutoGNOM research):
Shannon-bounded extent × log₁₀(α) joint grid, pick by ``shape_tight_extent``.

Units: ``q`` in nm⁻¹, lengths (Dmax, Rmax, Rg) in nm.
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from autosaxs.core.gnom import distribution_arrays, parse_gnom_out
from autosaxs.core.gnom_quality import DEFAULT_CHI2_GOOD_MAX

# --- Shannon × α grid (phase-5/6 freeze) ---
DMAX_GRID_N = 31
DMAX_LO_RG_MULT = 2.0
ALPHA_LOG10_LO = -1.0
ALPHA_LOG10_HI = 2.5
ALPHA_GRID_N = 11

# Soft-taper shape targets (human P(r) prefs)
TAIL_MASS_TARGET = 0.033

# χ² guardrail (log-normalized median residual²)
CHI2_MED_TARGET = float(DEFAULT_CHI2_GOOD_MAX)  # 1.5
CHI2_LOG_WEIGHT = 0.5

# Extent band for monodisperse P(r): ρ = Dmax / Rg_Guinier
# shape_tight_extent uses upper 5.0 (vs phase-5 shape_full 6.5)
PR_EXTENT_LO = 3.2
PR_EXTENT_HI = 5.0
EXTENT_OVER_WEIGHT = 0.15

# Polydisperse D(R): Rmax/Rg is typically smaller than protein Dmax/Rg
# (compact sphere R/Rg ≈ √(5/3) ≈ 1.29; polydisperse Rmax sits higher).
DR_EXTENT_LO = 1.0
DR_EXTENT_HI = 4.0
DR_RMAX_LO_RG_MULT = 0.5


def _f(x: Any, default: float = float("nan")) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def q_min_first_positive_nm(q_nm: Union[np.ndarray, Sequence[float]]) -> float:
    """First positive finite ``q`` (nm⁻¹)."""
    q = np.asarray(q_nm, dtype=float)
    mask = np.isfinite(q) & (q > 0.0)
    if not np.any(mask):
        raise ValueError("no positive finite q for Shannon bound")
    return float(q[np.argmax(mask)])


def shannon_extent_up_nm(q_min_nm: float) -> float:
    q = float(q_min_nm)
    if not (q > 0 and math.isfinite(q)):
        raise ValueError(f"invalid q_min_nm={q_min_nm}")
    return float(math.pi / q)


def shannon_extent_bounds(
    *,
    q_min_nm: float,
    rg_guinier_nm: float,
    lo_rg_mult: float = DMAX_LO_RG_MULT,
) -> Tuple[float, float, Dict[str, Any]]:
    """Return ``(lo, hi, meta)`` for a Shannon-bounded real-space extent grid."""
    e_up = shannon_extent_up_nm(q_min_nm)
    rg = float(rg_guinier_nm)
    if not (rg > 0 and math.isfinite(rg)):
        raise ValueError(f"invalid rg_guinier_nm={rg_guinier_nm}")
    e_lo = float(lo_rg_mult) * rg
    meta: Dict[str, Any] = {
        "q_min_nm": float(q_min_nm),
        "extent_up_nm": float(e_up),
        "extent_lo_nm": float(e_lo),
        "lo_rg_mult": float(lo_rg_mult),
        "fallback": False,
    }
    if e_up <= e_lo:
        e_hi = max(6.0 * rg, e_lo + 1.0)
        meta["fallback"] = True
        meta["fallback_hi_nm"] = float(e_hi)
        return float(e_lo), float(e_hi), meta
    return float(e_lo), float(e_up), meta


def extent_candidate_grid(
    *,
    q_min_nm: float,
    rg_guinier_nm: float,
    n: int = DMAX_GRID_N,
    lo_rg_mult: float = DMAX_LO_RG_MULT,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    lo, hi, meta = shannon_extent_bounds(
        q_min_nm=q_min_nm,
        rg_guinier_nm=rg_guinier_nm,
        lo_rg_mult=lo_rg_mult,
    )
    grid = np.linspace(lo, hi, int(n)).astype(float)
    meta["n"] = int(n)
    meta["grid_lo"] = float(grid[0])
    meta["grid_hi"] = float(grid[-1])
    return grid, meta


def alpha_log10_grid(
    lo: float = ALPHA_LOG10_LO,
    hi: float = ALPHA_LOG10_HI,
    n: int = ALPHA_GRID_N,
) -> np.ndarray:
    return np.linspace(float(lo), float(hi), int(n)).astype(float)


def chi2_med_from_iq_table(iq_table: Any) -> Optional[float]:
    """Robust fit quality: median over points of ``((I_exp - I_fit)/σ)²``."""
    if iq_table is None:
        return None
    try:
        _q, i_exp, sigma, i_fit = iq_table
        ie = np.asarray(i_exp, dtype=float)
        ifit = np.asarray(i_fit, dtype=float)
        sig = np.asarray(sigma, dtype=float)
    except (TypeError, ValueError):
        return None
    mask = np.isfinite(ie) & np.isfinite(ifit) & np.isfinite(sig) & (sig > 0)
    if int(np.count_nonzero(mask)) < 2:
        return None
    res2 = ((ie[mask] - ifit[mask]) / sig[mask]) ** 2
    v = float(np.median(res2))
    return v if math.isfinite(v) else None


def _n_modes(p: np.ndarray, thr_frac: float = 0.05) -> int:
    p_abs_max = float(np.nanmax(np.abs(p))) if p.size else 0.0
    if p_abs_max <= 0:
        return 0
    thr = thr_frac * p_abs_max
    n = 0
    for i in range(1, p.size - 1):
        if p[i] >= p[i - 1] and p[i] > p[i + 1] and p[i] >= thr:
            n += 1
    return n if n > 0 else 1


def shape_features_from_distribution(
    r: np.ndarray,
    values: np.ndarray,
    *,
    extent_nm: float,
) -> Dict[str, Any]:
    """Soft-taper / unimodal / negativity / wiggle features from a real-space curve."""
    trap = getattr(np, "trapezoid", None) or np.trapz
    r = np.asarray(r, dtype=float)
    p = np.asarray(values, dtype=float)
    m = np.isfinite(r) & np.isfinite(p)
    r, p = r[m], p[m]
    dmax = float(extent_nm)

    neg_frac = 0.0
    neg_mass = 0.0
    tail_ratio = 0.0
    tail_mass = 0.0
    smoothness = 0.0
    n_sign_runs = 0.0
    n_modes = 1
    peak_frac = 0.5
    p_end_norm = 0.0

    if p.size and dmax > 0:
        p_abs_max = float(np.nanmax(np.abs(p)))
        if p_abs_max > 0:
            neg_frac = float(np.mean(p < 0.0))
            abs_p = np.abs(p)
            int_neg = float(trap(np.maximum(-p, 0.0), r))
            int_abs = float(trap(abs_p, r))
            neg_mass = float(int_neg / (int_abs + 1e-12))
            tail_n = min(5, int(p.size))
            tail_ratio = float(np.nanmean(np.abs(p[-tail_n:])) / (p_abs_max + 1e-12))
            p_end_norm = float(abs(p[-1]) / (p_abs_max + 1e-12))
            if p.size >= 3:
                smoothness = float(np.nanmean(np.abs(np.diff(p, n=2))) / (p_abs_max + 1e-12))
            p_pos = np.maximum(p, 0.0)
            if r.size >= 2:
                mid = 0.5 * (p_pos[1:] + p_pos[:-1]) * np.diff(r)
                c = np.concatenate([[0.0], np.cumsum(mid)])
                c = c / (c[-1] + 1e-30)
                i80 = max(0, int(np.searchsorted(r, 0.8 * dmax)) - 1)
                i80 = min(i80, len(c) - 1)
                tail_mass = float(np.clip(1.0 - c[i80], 0.0, 1.0))
            ipeak = int(np.argmax(p))
            if p[ipeak] <= 0:
                ipeak = int(np.argmax(np.abs(p)))
            peak_frac = float(np.clip(r[ipeak] / dmax, 0.0, 1.0))
            n_modes = int(_n_modes(p))
            s = np.sign(p)
            s = s[s != 0]
            n_sign_runs = float(np.sum(s[1:] != s[:-1])) if s.size >= 2 else 0.0

    return {
        "neg_frac": neg_frac,
        "neg_mass": neg_mass,
        "tail_ratio": tail_ratio,
        "tail_mass": tail_mass,
        "smoothness": smoothness,
        "n_sign_runs": n_sign_runs,
        "n_modes": n_modes,
        "peak_frac": peak_frac,
        "p_end_norm": p_end_norm,
        "extent_nm": float(dmax),
    }


def shape_features_from_parsed(
    parsed: Dict[str, Any],
    *,
    extent_requested_nm: float,
    rg_guinier_nm: Optional[float] = None,
) -> Dict[str, Any]:
    """Features from a parsed GNOM/DATGNOM ``.out`` (works for p(r) or D(R))."""
    te = _f(parsed.get("total_estimate"))
    rmax_parsed = _f(parsed.get("real_space_rmax"))
    extent = float(rmax_parsed) if math.isfinite(rmax_parsed) else float(extent_requested_nm)
    feats: Dict[str, Any] = {
        "total_estimate": te if math.isfinite(te) else None,
        "rmax_nm": float(extent),
        "rmax_requested_nm": float(extent_requested_nm),
        "alpha": _f(parsed.get("current_alpha")),
        "suspicious": bool(parsed.get("suspicious")),
        "chi2_med": chi2_med_from_iq_table(parsed.get("iq_table")),
        "ok_parse": math.isfinite(te),
    }
    arrays = distribution_arrays(parsed.get("distribution"))
    if arrays is not None:
        r, vals, _err = arrays
        feats.update(shape_features_from_distribution(r, vals, extent_nm=extent))
    else:
        feats.update(
            shape_features_from_distribution(
                np.asarray([], dtype=float),
                np.asarray([], dtype=float),
                extent_nm=extent,
            )
        )
    rg_g = _f(rg_guinier_nm)
    if math.isfinite(rg_g) and rg_g > 0 and extent > 0:
        feats["d_over_rg"] = float(extent / rg_g)
        feats["rg_guinier_nm"] = float(rg_g)
    else:
        feats["d_over_rg"] = None
        feats["rg_guinier_nm"] = float(rg_g) if math.isfinite(rg_g) else None
    return feats


def shape_features_from_out(
    source: Union[str, os.PathLike, Dict[str, Any]],
    *,
    extent_requested_nm: float,
    rg_guinier_nm: Optional[float] = None,
) -> Dict[str, Any]:
    """``source`` is a path, raw ``.out`` text, or an already-parsed dict."""
    if isinstance(source, dict):
        parsed = source
    else:
        parsed = parse_gnom_out(source)
    return shape_features_from_parsed(
        parsed,
        extent_requested_nm=extent_requested_nm,
        rg_guinier_nm=rg_guinier_nm,
    )


def _neg_pen(c: Dict[str, Any]) -> float:
    return _f(c.get("neg_frac"), 0.0) + 0.5 * _f(c.get("neg_mass"), 0.0)


def _wiggle_pen(c: Dict[str, Any]) -> float:
    smooth = _f(c.get("smoothness"), 0.0)
    nsign = _f(c.get("n_sign_runs"), 0.0)
    nmodes = _f(c.get("n_modes"), 1.0)
    pen = 8.0 * max(0.0, smooth - 0.002)
    pen += 0.05 * max(0.0, nsign - 2.0)
    pen += 0.25 * max(0.0, nmodes - 1.0)
    return pen


def _taper_pen(c: Dict[str, Any], *, heavy: bool = True) -> float:
    tail_m = _f(c.get("tail_mass"), 0.0)
    tail_r = _f(c.get("tail_ratio"), 0.0)
    pend = _f(c.get("p_end_norm"), 0.0)
    excess = max(0.0, tail_m - TAIL_MASS_TARGET)
    w = 2.5 if heavy else 1.2
    pen = w * excess
    pen += (1.0 if heavy else 0.5) * max(0.0, tail_r - 0.05)
    pen += 0.5 * pend
    return pen


def shape_only_score(c: Dict[str, Any]) -> float:
    """``S_shape = -(pen_neg + pen_wiggle + pen_taper)`` (higher is better)."""
    return -(_neg_pen(c) + _wiggle_pen(c) + _taper_pen(c, heavy=True))


def chi2_penalty(c: Dict[str, Any]) -> float:
    cm = _f(c.get("chi2_med"))
    if not math.isfinite(cm) or cm <= 0:
        return 2.0
    return float(CHI2_LOG_WEIGHT * max(0.0, math.log10(cm / CHI2_MED_TARGET)))


def extent_penalty(
    c: Dict[str, Any],
    *,
    lo: float,
    hi: float,
    over_weight: float = EXTENT_OVER_WEIGHT,
) -> float:
    rho = _f(c.get("d_over_rg"))
    if not math.isfinite(rho):
        return 2.0
    if rho < lo:
        return float((lo - rho) ** 2)
    if rho > hi:
        return float(over_weight * (rho - hi) ** 2)
    return 0.0


def shape_tight_extent_score(c: Dict[str, Any]) -> float:
    """Monodisperse P(r) product score: soft taper + χ² − extent (hi=5×Rg).

    Interpretable default from AutoGNOM research (phase-6 ``shape_tight_extent``).
    Higher is better.
    """
    return (
        shape_only_score(c)
        - chi2_penalty(c)
        - extent_penalty(c, lo=PR_EXTENT_LO, hi=PR_EXTENT_HI)
    )


def shape_tight_extent_score_dr(c: Dict[str, Any]) -> float:
    """Polydisperse D(R)/Dv(R) score: same shape+χ² family, adapted extent band.

    Uses ``ρ = Rmax / Rg`` with a lower band suitable for size distributions
    (compact-sphere scale), not protein ``Dmax/Rg ∼ 3–5``.
    """
    return (
        shape_only_score(c)
        - chi2_penalty(c)
        - extent_penalty(c, lo=DR_EXTENT_LO, hi=DR_EXTENT_HI)
    )


def pick_best_by_score(
    trials: List[Dict[str, Any]],
    score_fn,
) -> Optional[Dict[str, Any]]:
    """Argmax ``score_fn`` over trials with ``ok`` truthy; attaches ``score``."""
    best = None
    best_s = float("-inf")
    for t in trials:
        if not t.get("ok"):
            continue
        s = float(score_fn(t))
        if not math.isfinite(s):
            continue
        if s > best_s:
            best_s = s
            best = dict(t)
            best["score"] = s
    return best
