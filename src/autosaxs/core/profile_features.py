"""
Profile feature helpers shared by ``fit_porod``, ``analyze_loglog``, and
``fit_sizes`` (power-law exponent advisory).

Units: ``q`` in nm⁻¹, lengths in nm. Math SSOT for log–log / Porod slopes —
skills only publish passport fields; do not fork formulas in skill modules.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    from scipy.optimize import curve_fit
except Exception:  # pragma: no cover
    curve_fit = None  # type: ignore


# Soft dimensionality bands (Li 2016 −1/−2/−4; Putnam d_f; soft fractal band).
_TAG_BANDS: Tuple[Tuple[str, float, float], ...] = (
    ("rod_like", -1.0, 0.35),
    ("disk_like", -2.0, 0.35),
    ("coil_like", -1.67, 0.25),  # good-solvent coil ≈ −5/3
    ("compact_porod_like", -4.0, 0.5),
)


def _finite_positive_mask(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray, Optional[np.ndarray], int]:
    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    n_raw = int(q.size)
    valid = np.isfinite(q) & np.isfinite(I) & (q > 0) & (I > 0)
    n_drop = int(n_raw - int(valid.sum()))
    q_o = q[valid]
    I_o = I[valid]
    sig_o: Optional[np.ndarray] = None
    if sigma is not None:
        sigma = np.asarray(sigma, dtype=float)
        if sigma.shape == q.shape:
            sig_o = sigma[valid]
            # Non-positive / non-finite σ → unweighted for those points later.
    idx = np.argsort(q_o)
    q_o = q_o[idx]
    I_o = I_o[idx]
    if sig_o is not None:
        sig_o = sig_o[idx]
    return q_o, I_o, sig_o, n_drop


def _loglog_ols(
    q: np.ndarray,
    I: np.ndarray,
    *,
    sigma: Optional[np.ndarray] = None,
) -> Optional[Dict[str, float]]:
    """Ordinary least-squares of log I vs log q (optional inverse-variance on log I)."""
    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    if q.size < 2:
        return None
    x = np.log(q)
    y = np.log(I)
    w: Optional[np.ndarray] = None
    if sigma is not None:
        sigma = np.asarray(sigma, dtype=float)
        good = np.isfinite(sigma) & (sigma > 0) & (I > 0)
        if int(good.sum()) >= 2:
            # σ_logI ≈ σ_I / I
            w = np.zeros_like(y)
            w[good] = I[good] / sigma[good]
            # Fall back to unweighted if too few weighted points.
            if int(good.sum()) < max(2, q.size // 2):
                w = None
    try:
        if w is not None:
            coeffs = np.polyfit(x, y, 1, w=w)
        else:
            coeffs = np.polyfit(x, y, 1)
    except Exception:
        return None
    alpha = float(coeffs[0])
    logA = float(coeffs[1])
    A = float(np.exp(logA))
    y_hat = alpha * x + logA
    ss_res = float(np.sum((y - y_hat) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan")
    return {
        "alpha": alpha,
        "amplitude": A,
        "log_amplitude": logA,
        "fit_r2": r2,
        "n_points": float(q.size),
        "q_min_nm": float(q[0]),
        "q_max_nm": float(q[-1]),
    }


def powerlaw_exponent(
    q: np.ndarray,
    I: np.ndarray,
    *,
    sigma: Optional[np.ndarray] = None,
    q_min: Optional[float] = None,
    q_max: Optional[float] = None,
    last_fraction: float = 0.3,
    n_min: int = 5,
) -> Optional[Dict[str, Any]]:
    """
    Single-window high-q power-law slope: I ∝ q^α on log–log axes.

    Window: explicit ``[q_min, q_max]`` when given; otherwise the rightmost
    ``last_fraction`` of positive-I points (clamped to at least ``n_min``).

    Returns dict with ``alpha``, ``amplitude`` (A in I≈A q^α), ``fit_r2``,
    ``q_min_nm``, ``q_max_nm``, ``n_points``, ``n_dropped_nonpositive``; or None.
    """
    q_o, I_o, sig_o, n_drop = _finite_positive_mask(q, I, sigma)
    if q_o.size < n_min:
        return None
    if q_min is not None or q_max is not None:
        mask = np.ones(q_o.size, dtype=bool)
        if q_min is not None:
            mask &= q_o >= float(q_min)
        if q_max is not None:
            mask &= q_o <= float(q_max)
        q_w, I_w = q_o[mask], I_o[mask]
        sig_w = sig_o[mask] if sig_o is not None else None
    else:
        frac = float(last_fraction)
        if not np.isfinite(frac) or frac <= 0:
            frac = 0.3
        n_take = max(n_min, int(np.ceil(frac * q_o.size)))
        n_take = min(n_take, q_o.size)
        q_w, I_w = q_o[-n_take:], I_o[-n_take:]
        sig_w = sig_o[-n_take:] if sig_o is not None else None
    if q_w.size < n_min:
        return None
    fit = _loglog_ols(q_w, I_w, sigma=sig_w)
    if fit is None:
        return None
    return {
        "alpha": float(fit["alpha"]),
        "amplitude": float(fit["amplitude"]),
        "fit_r2": float(fit["fit_r2"]),
        "q_min_nm": float(fit["q_min_nm"]),
        "q_max_nm": float(fit["q_max_nm"]),
        "n_points": int(fit["n_points"]),
        "n_dropped_nonpositive": int(n_drop),
    }


def powerlaw_segments(
    q: np.ndarray,
    I: np.ndarray,
    *,
    sigma: Optional[np.ndarray] = None,
    n_min: int = 8,
    window_sizes: Optional[Sequence[int]] = None,
    max_segments: int = 6,
    merge_alpha_tol: float = 0.35,
) -> Dict[str, Any]:
    """
    Catalogue log–log slope segments across the usable q range.

    Sliding windows (several lengths) → local α; greedy merge of contiguous
    windows with similar α into retained segments. Soft tags applied later by
    :func:`tag_powerlaw_regime`.
    """
    q_o, I_o, sig_o, n_drop = _finite_positive_mask(q, I, sigma)
    empty: Dict[str, Any] = {
        "segments": [],
        "n_dropped_nonpositive": int(n_drop),
        "n_points_usable": int(q_o.size),
    }
    if q_o.size < n_min:
        return empty

    if window_sizes is None:
        # Prefer a few geometrically spaced window lengths.
        sizes = sorted(
            {
                n_min,
                max(n_min, q_o.size // 8),
                max(n_min, q_o.size // 5),
                max(n_min, q_o.size // 3),
            }
        )
        window_sizes = [s for s in sizes if s <= q_o.size]
    else:
        window_sizes = [int(s) for s in window_sizes if int(s) >= n_min and int(s) <= q_o.size]
    if not window_sizes:
        window_sizes = [min(n_min, q_o.size)]

    # Build local slope samples at window midpoints (use largest stable window).
    win = max(window_sizes)
    locals_: List[Dict[str, Any]] = []
    for i0 in range(0, q_o.size - win + 1):
        i1 = i0 + win
        q_w, I_w = q_o[i0:i1], I_o[i0:i1]
        sig_w = sig_o[i0:i1] if sig_o is not None else None
        fit = _loglog_ols(q_w, I_w, sigma=sig_w)
        if fit is None:
            continue
        locals_.append(
            {
                "i0": i0,
                "i1": i1,
                "alpha": float(fit["alpha"]),
                "fit_r2": float(fit["fit_r2"]),
                "q_min_nm": float(fit["q_min_nm"]),
                "q_max_nm": float(fit["q_max_nm"]),
                "n_points": int(fit["n_points"]),
            }
        )
    if not locals_:
        return empty

    # Greedy merge contiguous locals with similar alpha.
    segments: List[Dict[str, Any]] = []
    cur = dict(locals_[0])
    for nxt in locals_[1:]:
        if abs(float(nxt["alpha"]) - float(cur["alpha"])) <= merge_alpha_tol and nxt["i0"] <= cur["i1"]:
            # Extend: refit over union.
            i0 = int(cur["i0"])
            i1 = int(nxt["i1"])
            q_w, I_w = q_o[i0:i1], I_o[i0:i1]
            sig_w = sig_o[i0:i1] if sig_o is not None else None
            fit = _loglog_ols(q_w, I_w, sigma=sig_w)
            if fit is None:
                segments.append(cur)
                cur = dict(nxt)
                continue
            cur = {
                "i0": i0,
                "i1": i1,
                "alpha": float(fit["alpha"]),
                "fit_r2": float(fit["fit_r2"]),
                "q_min_nm": float(fit["q_min_nm"]),
                "q_max_nm": float(fit["q_max_nm"]),
                "n_points": int(fit["n_points"]),
            }
        else:
            segments.append(cur)
            cur = dict(nxt)
    segments.append(cur)

    # Keep highest-|coverage| / best-r2 segments, cap count.
    segments.sort(key=lambda s: (int(s["n_points"]), float(s.get("fit_r2") or 0.0)), reverse=True)
    segments = segments[: max(1, int(max_segments))]
    segments.sort(key=lambda s: float(s["q_min_nm"]))

    out_segs: List[Dict[str, Any]] = []
    for s in segments:
        tag, conf = tag_powerlaw_regime(float(s["alpha"]))
        out_segs.append(
            {
                "q_min_nm": float(s["q_min_nm"]),
                "q_max_nm": float(s["q_max_nm"]),
                "alpha": float(s["alpha"]),
                "n_points": int(s["n_points"]),
                "fit_r2": float(s["fit_r2"]),
                "tag": tag,
                "confidence": float(conf),
            }
        )
    return {
        "segments": out_segs,
        "n_dropped_nonpositive": int(n_drop),
        "n_points_usable": int(q_o.size),
    }


def tag_powerlaw_regime(alpha: float) -> Tuple[str, float]:
    """
    Soft-tag a power-law exponent.

    Returns ``(tag, confidence)`` where tag ∈
    ``rod_like`` / ``disk_like`` / ``coil_like`` / ``compact_porod_like`` /
    ``fractal_like`` / ``unclear``.
    """
    if not np.isfinite(alpha):
        return "unclear", 0.0
    best_tag = "unclear"
    best_dist = float("inf")
    best_tol = 1.0
    for tag, center, tol in _TAG_BANDS:
        dist = abs(alpha - center)
        if dist <= tol and dist < best_dist:
            best_dist = dist
            best_tag = tag
            best_tol = tol
    if best_tag != "unclear":
        conf = float(max(0.0, 1.0 - best_dist / best_tol))
        return best_tag, conf
    # Soft fractal band: |α| in ~(2.3, 3.7) excluding clean −4 / −2.
    if -3.7 <= alpha <= -2.3:
        return "fractal_like", float(max(0.2, 1.0 - abs(alpha + 3.0) / 0.7))
    return "unclear", 0.0


def dominant_mid_q_tag(
    segments: Sequence[Dict[str, Any]],
    *,
    q_lo_frac: float = 0.15,
    q_hi_frac: float = 0.75,
) -> str:
    """Pick a soft routing label from mid-q segments (exclude extremes)."""
    if not segments:
        return "unclear"
    q_mins = [float(s["q_min_nm"]) for s in segments]
    q_maxs = [float(s["q_max_nm"]) for s in segments]
    q_lo = min(q_mins)
    q_hi = max(q_maxs)
    span = q_hi - q_lo
    if span <= 0:
        return str(segments[0].get("tag") or "unclear")
    lo = q_lo + q_lo_frac * span
    hi = q_lo + q_hi_frac * span
    mid = [
        s
        for s in segments
        if float(s["q_max_nm"]) >= lo and float(s["q_min_nm"]) <= hi
    ]
    if not mid:
        mid = list(segments)
    # Prefer non-unclear with highest confidence * n_points.
    scored: List[Tuple[float, str]] = []
    for s in mid:
        tag = str(s.get("tag") or "unclear")
        if tag == "unclear":
            continue
        conf = float(s.get("confidence") or 0.0)
        n = int(s.get("n_points") or 0)
        scored.append((conf * max(n, 1), tag))
    if not scored:
        return "unclear"
    scored.sort(reverse=True)
    return scored[0][1]


def _model_b_a_q_alpha(q: np.ndarray, B: float, A: float, alpha: float) -> np.ndarray:
    return B + A * np.power(q, alpha)


def _model_b_a_q_m4(q: np.ndarray, B: float, A: float) -> np.ndarray:
    return B + A * np.power(q, -4.0)


def fit_porod_asymptote(
    q: np.ndarray,
    I: np.ndarray,
    *,
    sigma: Optional[np.ndarray] = None,
    alpha_mode: str = "free",
    alpha_nominal: float = -4.0,
    fit_background: bool = True,
    q_min: Optional[float] = None,
    q_max: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Background-aware Porod asymptote: I(q) ≈ B + A q^α on a fixed window.

    ``alpha_mode``: ``free`` (α fitted near ``alpha_nominal``) or ``fixed_minus4``
    (α locked to −4; ``porod_constant`` ≡ A when B is fitted so lim q⁴(I−B)=A).

    Prefer weighted fit on original I(q) (Pauw 2013). Returns None if unfit.
    """
    q_o, I_o, sig_o, n_drop = _finite_positive_mask(q, I, sigma)
    if q_min is not None or q_max is not None:
        mask = np.ones(q_o.size, dtype=bool)
        if q_min is not None:
            mask &= q_o >= float(q_min)
        if q_max is not None:
            mask &= q_o <= float(q_max)
        q_o, I_o = q_o[mask], I_o[mask]
        if sig_o is not None:
            sig_o = sig_o[mask]
    if q_o.size < 4:
        return None

    mode = str(alpha_mode or "free").strip().lower()
    if mode in ("fixed", "fixed_minus4", "minus4", "-4"):
        mode = "fixed_minus4"
    else:
        mode = "free"

    # Seed from log–log OLS ignoring B (rough).
    seed = _loglog_ols(q_o, I_o, sigma=sig_o)
    A0 = float(seed["amplitude"]) if seed else float(np.median(I_o * (q_o ** 4)))
    B0 = float(max(0.0, np.min(I_o) * 0.1)) if fit_background else 0.0
    alpha0 = float(seed["alpha"]) if seed else float(alpha_nominal)

    sigma_use: Optional[np.ndarray] = None
    if sig_o is not None:
        good = np.isfinite(sig_o) & (sig_o > 0)
        if int(good.sum()) >= max(4, q_o.size // 2):
            sigma_use = np.where(good, sig_o, np.nanmedian(sig_o[good]))

    try:
        if curve_fit is None:
            raise RuntimeError("scipy.optimize.curve_fit unavailable")
        if mode == "fixed_minus4":
            if fit_background:
                popt, _ = curve_fit(
                    _model_b_a_q_m4,
                    q_o,
                    I_o,
                    p0=(B0, max(A0, 1e-30)),
                    sigma=sigma_use,
                    absolute_sigma=True,
                    maxfev=20000,
                    bounds=([-np.inf, 0.0], [np.inf, np.inf]),
                )
                B, A = float(popt[0]), float(popt[1])
            else:
                # I ≈ A q^{-4}
                def _m(qq, A_):
                    return A_ * np.power(qq, -4.0)

                popt, _ = curve_fit(
                    _m,
                    q_o,
                    I_o,
                    p0=(max(A0, 1e-30),),
                    sigma=sigma_use,
                    absolute_sigma=True,
                    maxfev=20000,
                    bounds=(0.0, np.inf),
                )
                B, A = 0.0, float(popt[0])
            alpha = -4.0
        else:
            if fit_background:
                popt, _ = curve_fit(
                    _model_b_a_q_alpha,
                    q_o,
                    I_o,
                    p0=(B0, max(A0, 1e-30), alpha0),
                    sigma=sigma_use,
                    absolute_sigma=True,
                    maxfev=20000,
                    bounds=([-np.inf, 0.0, -8.0], [np.inf, np.inf, 0.0]),
                )
                B, A, alpha = float(popt[0]), float(popt[1]), float(popt[2])
            else:
                def _m(qq, A_, alpha_):
                    return A_ * np.power(qq, alpha_)

                popt, _ = curve_fit(
                    _m,
                    q_o,
                    I_o,
                    p0=(max(A0, 1e-30), alpha0),
                    sigma=sigma_use,
                    absolute_sigma=True,
                    maxfev=20000,
                    bounds=([0.0, -8.0], [np.inf, 0.0]),
                )
                B, A, alpha = 0.0, float(popt[0]), float(popt[1])
    except Exception:
        # Fallback: pure log–log (B=0).
        if seed is None:
            return None
        B = 0.0
        A = float(seed["amplitude"])
        alpha = float(seed["alpha"]) if mode == "free" else -4.0
        if mode == "fixed_minus4":
            # Re-estimate A from median q^4 I.
            A = float(np.median(I_o * (q_o ** 4)))

    I_fit = B + A * np.power(q_o, alpha)
    ss_res = float(np.sum((I_o - I_fit) ** 2))
    ss_tot = float(np.sum((I_o - np.mean(I_o)) ** 2))
    r2 = float(1.0 - ss_res / ss_tot) if ss_tot > 0 else float("nan")

    # Classical Porod constant: lim q^4 (I − B) when α≈−4 or fixed mode.
    porod_constant: Optional[float] = None
    if mode == "fixed_minus4" or abs(alpha + 4.0) < 0.75:
        porod_constant = float(np.median((I_o - B) * (q_o ** 4)))

    return {
        "alpha": float(alpha),
        "alpha_mode": mode,
        "porod_amplitude": float(A),
        "porod_constant": porod_constant,
        "background": float(B),
        "fit_r2": r2,
        "q_min_nm": float(q_o[0]),
        "q_max_nm": float(q_o[-1]),
        "n_points": int(q_o.size),
        "n_dropped_nonpositive": int(n_drop),
        "I_fit": I_fit,
        "q_fit": q_o.copy(),
        "I_data": I_o.copy(),
    }


def select_porod_window(
    q: np.ndarray,
    I: np.ndarray,
    *,
    sigma: Optional[np.ndarray] = None,
    n_min: int = 5,
    alpha_nominal: float = -4.0,
    alpha_tol: float = 0.5,
    max_pts: int = 60,
    rg_nm: Optional[float] = None,
    qR_min: float = 2.0,
) -> Dict[str, Any]:
    """
    Choose a high-q Porod window (evolves ``find_porod_region`` search).

    When ``rg_nm`` is set, require q ≥ qR_min/Rg. Prefer a rightmost segment
    whose free log–log slope is within ``alpha_tol`` of ``alpha_nominal``.
    If none match, return the full theoretical high-q band with
    ``porod_like=False``.
    """
    q_o, I_o, sig_o, n_drop = _finite_positive_mask(q, I, sigma)
    out: Dict[str, Any] = {
        "n_dropped_nonpositive": int(n_drop),
        "theoretical_range_used": False,
        "theoretical_range_absent": False,
        "porod_like": False,
    }
    if q_o.size < n_min:
        out["theoretical_range_absent"] = True
        return out

    q_work, I_work = q_o, I_o
    sig_work = sig_o
    if rg_nm is not None and float(rg_nm) > 0:
        q_min_theory = float(qR_min) / float(rg_nm)
        out["q_min_required"] = q_min_theory
        out["Rg"] = float(rg_nm)
        if float(q_o[-1]) < q_min_theory:
            out["theoretical_range_absent"] = True
            out["q_max_data"] = float(q_o[-1])
            return out
        mask = q_o >= q_min_theory
        if int(mask.sum()) < n_min:
            out["theoretical_range_absent"] = True
            out["q_max_data"] = float(q_o[-1])
            return out
        q_work, I_work = q_o[mask], I_o[mask]
        sig_work = sig_o[mask] if sig_o is not None else None
        out["theoretical_range_used"] = True

    n = int(q_work.size)
    best: Optional[Dict[str, Any]] = None
    best_err = np.inf
    for n_pts in range(n_min, min(n, max_pts) + 1):
        q_sub = q_work[-n_pts:]
        I_sub = I_work[-n_pts:]
        sig_sub = sig_work[-n_pts:] if sig_work is not None else None
        fit = powerlaw_exponent(
            q_sub,
            I_sub,
            sigma=sig_sub,
            q_min=float(q_sub[0]),
            q_max=float(q_sub[-1]),
            n_min=n_min,
        )
        if fit is None:
            continue
        slope = float(fit["alpha"])
        err = abs(slope - float(alpha_nominal))
        if err > float(alpha_tol):
            continue
        if err < best_err:
            best_err = err
            best = {
                "q_min_nm": float(fit["q_min_nm"]),
                "q_max_nm": float(fit["q_max_nm"]),
                "n_points": int(fit["n_points"]),
                "alpha_loglog": slope,
                "porod_like": True,
            }
    if best is not None:
        out.update(best)
        return out

    # No slope≈nominal window: use full theoretical / available high-q band.
    out.update(
        {
            "q_min_nm": float(q_work[0]),
            "q_max_nm": float(q_work[-1]),
            "n_points": int(q_work.size),
            "porod_like": False,
        }
    )
    seed = powerlaw_exponent(
        q_work,
        I_work,
        sigma=sig_work,
        q_min=float(q_work[0]),
        q_max=float(q_work[-1]),
        n_min=min(n_min, q_work.size),
    )
    if seed is not None:
        out["alpha_loglog"] = float(seed["alpha"])
    return out


def porod_debye_plateau(
    q: np.ndarray,
    I: np.ndarray,
    *,
    q_min: Optional[float] = None,
    q_max: Optional[float] = None,
    n_min: int = 8,
) -> Dict[str, Any]:
    """
    Soft Porod–Debye plateau score on q⁴ I(q) vs q⁴ (Rambo & Tainer 2011).

    Returns ``label`` ∈ ``plateau`` / ``rising`` / ``absent`` / ``unclear``,
    plus ``score`` in [0,1] (higher = flatter high-q tail) and arrays for plots.
    """
    q_o, I_o, _, n_drop = _finite_positive_mask(q, I, None)
    if q_min is not None or q_max is not None:
        mask = np.ones(q_o.size, dtype=bool)
        if q_min is not None:
            mask &= q_o >= float(q_min)
        if q_max is not None:
            mask &= q_o <= float(q_max)
        q_o, I_o = q_o[mask], I_o[mask]
    q4 = q_o ** 4
    y = I_o * q4
    result: Dict[str, Any] = {
        "label": "unclear",
        "score": 0.0,
        "n_dropped_nonpositive": int(n_drop),
        "q": q_o,
        "q4": q4,
        "q4I": y,
    }
    if q_o.size < n_min:
        result["label"] = "absent"
        return result

    # Compare early vs late thirds of the (windowed) curve.
    n = q_o.size
    a = max(3, n // 3)
    y_early = y[:a]
    y_late = y[-a:]
    med_e = float(np.median(y_early))
    med_l = float(np.median(y_late))
    # Relative slope of last third vs q4.
    x_l = q4[-a:]
    try:
        slope = float(np.polyfit(x_l, y_late, 1)[0])
    except Exception:
        slope = float("nan")
    scale = max(abs(med_l), abs(med_e), 1e-30)
    rel_change = (med_l - med_e) / scale
    flatness = float(np.clip(1.0 - abs(rel_change), 0.0, 1.0))
    # Penalize strong positive late slope (rising / no plateau).
    if np.isfinite(slope) and slope > 0:
        flatness *= float(np.clip(1.0 - slope * x_l[-1] / scale, 0.0, 1.0))

    if flatness >= 0.55 and abs(rel_change) < 0.35:
        label = "plateau"
    elif rel_change > 0.35 or (np.isfinite(slope) and slope > 0 and rel_change > 0.15):
        label = "rising"
    elif flatness < 0.25:
        label = "absent"
    else:
        label = "unclear"

    result["label"] = label
    result["score"] = float(flatness)
    result["rel_change"] = float(rel_change)
    return result


def low_q_plateau_hint(
    q: np.ndarray,
    I: np.ndarray,
    *,
    n_min: int = 5,
    frac: float = 0.12,
) -> Dict[str, Any]:
    """Jacques-style soft check: discernable flat region at lowest q on log–log."""
    q_o, I_o, _, n_drop = _finite_positive_mask(q, I, None)
    out: Dict[str, Any] = {
        "seen": False,
        "hint": "absent",
        "n_dropped_nonpositive": int(n_drop),
    }
    if q_o.size < n_min:
        return out
    n_take = max(n_min, int(np.ceil(float(frac) * q_o.size)))
    n_take = min(n_take, q_o.size)
    fit = _loglog_ols(q_o[:n_take], I_o[:n_take])
    if fit is None:
        return out
    alpha = float(fit["alpha"])
    # Near-flat on log–log ⇒ α ≈ 0.
    if abs(alpha) < 0.35 and float(fit["fit_r2"]) > 0.2:
        out["seen"] = True
        out["hint"] = "present"
    elif abs(alpha) < 0.7:
        out["hint"] = "weak"
    else:
        out["hint"] = "absent"
    out["alpha_low_q"] = alpha
    return out
