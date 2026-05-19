"""
Guinier analysis for SAXS: Rg, I(0), region finding, and adaptive selection.
"""
from typing import Optional, Any, Dict, List, Tuple
import os
import re
import subprocess
import tempfile

import numpy as np


# --- Constants ---
GUINIER_QRG_VALIDATION_K = 1.0
GUINIER_VALIDATION_MIN_POINTS = 2
GUINIER_CLASSIFICATION_R2_MIN = 0.85
GUINIER_UPTURN_DOWNTURN_FRACTION = 0.85

# Adaptive Guinier enumeration / selection
ADAPTIVE_N_MIN = 4
ADAPTIVE_MAX_PTS = 80
ADAPTIVE_I_START_Q_MAX_NM = 1.0
ADAPTIVE_I_START_INDEX_MAX = 50
ADAPTIVE_SELECTION_R2_MIN = 0.5
ADAPTIVE_VALIDATED_STRONG_R2 = 0.85


def parse_autorg_output(text: str, q: np.ndarray) -> Optional[Dict[str, Any]]:
    """
    Parse AUTORG stdout/stderr text (ATSAS) into a structured dict.

    Returns None if Rg cannot be parsed. Handles missing I(0), Quality, and point-range lines.
    Point indices in the output are treated as 1-based line/point numbers in the input .dat file.

    Keys when successful (some values may be None):
      Rg, I0, n_points, fit_quality, guinier_interval (q_min, q_max) | None,
      first_point_1based, last_point_1based (ints or None).
    """
    if not (text or "").strip():
        return None
    q = np.asarray(q, dtype=float)
    n_total = int(len(q))

    mr = re.search(r"Rg\s*=\s*([\d\.\+\-eE]+)", text)
    if not mr:
        return None
    try:
        autorg_rg = float(mr.group(1))
    except ValueError:
        return None

    mi0 = re.search(r"I\(0\)\s*=\s*([\d\.\+\-eE]+)", text)
    mq = re.search(r"Quality:\s*([\d\.\+\-eE]+)", text)
    mpts = re.search(r"Points\s+(\d+)\s+to\s+(\d+)\s+\((\d+)\s+total\)", text)

    autorg_i0 = float(mi0.group(1)) if mi0 else None
    autorg_quality: Optional[float] = None
    if mq:
        try:
            autorg_quality = float(mq.group(1))
        except ValueError:
            autorg_quality = None
    autorg_n_pts = int(mpts.group(3)) if mpts else None

    q_min_autorg: Optional[float] = None
    q_max_autorg: Optional[float] = None
    first_point_1based: Optional[int] = None
    last_point_1based: Optional[int] = None

    if mpts is not None:
        try:
            i1, i2 = int(mpts.group(1)), int(mpts.group(2))
        except ValueError:
            i1, i2 = 0, 0
        if i1 > 0 and i2 > 0:
            first_point_1based = max(1, min(i1, n_total))
            last_point_1based = max(1, min(i2, n_total))
            j1 = max(0, min(i1 - 1, n_total - 1))
            j2 = max(0, min(i2 - 1, n_total - 1))
            if j1 <= j2:
                q_min_autorg = float(q[j1])
                q_max_autorg = float(q[j2])

    interval: Optional[Tuple[float, float]] = None
    if q_min_autorg is not None and q_max_autorg is not None:
        interval = (q_min_autorg, q_max_autorg)

    return {
        "Rg": autorg_rg,
        "I0": autorg_i0,
        "n_points": autorg_n_pts,
        "fit_quality": autorg_quality,
        "guinier_interval": interval,
        "first_point_1based": first_point_1based,
        "last_point_1based": last_point_1based,
    }


def run_autorg_atsas(atsas_dat_path: str, q: np.ndarray) -> Optional[Dict[str, Any]]:
    """
    Run ATSAS ``autorg`` on an existing ATSAS-format .dat file and parse its output.

    Return value matches :func:`parse_autorg_output` (or None if Rg is absent).
    """
    if not atsas_dat_path or not os.path.isfile(atsas_dat_path):
        return None
    try:
        proc = subprocess.run(
            ["autorg", atsas_dat_path],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    content = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return parse_autorg_output(content, q)


def _validation_r2_guinier(
    q: np.ndarray,
    I: np.ndarray,
    Rg: float,
    I0: float,
    k: float = GUINIER_QRG_VALIDATION_K,
    min_pts: Optional[int] = None,
) -> Optional[float]:
    """
    R² between Guinier line ln(I0) - (Rg²/3)*q² and actual ln(I) on [q_max/2, q_max],
    where q_max = k/Rg (Guinier valid for q*Rg <= k). Returns None if too few points.
    """
    if Rg <= 0 or I0 <= 0 or len(q) == 0:
        return None
    n_min = min_pts if min_pts is not None else GUINIER_VALIDATION_MIN_POINTS
    q_max = k / Rg
    q_lo, q_hi = q_max / 2.0, q_max
    mask = (q >= q_lo) & (q <= q_hi) & (I > 0)
    qv = q[mask]
    Iv = I[mask]
    if len(qv) < n_min:
        return None
    y_actual = np.log(Iv)
    y_fit = np.log(I0) - (Rg ** 2 / 3.0) * (qv ** 2)
    ss_res = np.sum((y_actual - y_fit) ** 2)
    ss_tot = np.sum((y_actual - np.mean(y_actual)) ** 2)
    if ss_tot <= 0:
        return None
    return float(1.0 - ss_res / ss_tot)


def _upturn_downturn_nonparametric(
    x: np.ndarray, y: np.ndarray, Rg: float
) -> Optional[str]:
    """
    Non-parametric upturn/downturn: for each point i, compare y[i] to the line through
    the next point (x[i+1], y[i+1]) with slope from the Guinier approximation, -Rg²/3
    (in q² vs ln(I) space). Returns "upturn" | "downturn" | None.
    """
    n = len(x)
    if n < 2 or Rg <= 0:
        return None
    slope = -(Rg ** 2) / 3.0
    above = 0
    below = 0
    total = 0
    for i in range(n - 1):
        y_line = y[i + 1] + slope * (x[i] - x[i + 1])
        total += 1
        if y[i] > y_line:
            above += 1
        elif y[i] < y_line:
            below += 1
    if total == 0:
        return None
    if above / total >= GUINIER_UPTURN_DOWNTURN_FRACTION:
        return "upturn"
    if below / total >= GUINIER_UPTURN_DOWNTURN_FRACTION:
        return "downturn"
    return None


def _classification_guinier(
    q: np.ndarray,
    I: np.ndarray,
    Rg: float,
    I0: float,
    k: float = GUINIER_QRG_VALIDATION_K,
) -> Optional[str]:
    """
    Classify behavior in [0, q_max/2] for the best Rg approximation.
    Returns "linear" | "upturn" | "downturn" | "chaotic" or None if not enough points.
    """
    if Rg <= 0 or I0 <= 0 or len(q) == 0:
        return None
    q_max = k / Rg
    q_hi = q_max / 2.0
    mask = (q >= 0) & (q <= q_hi) & (I > 0)
    qc = q[mask]
    Ic = I[mask]
    if len(qc) < GUINIER_VALIDATION_MIN_POINTS:
        return None
    y_actual = np.log(Ic)
    y_fit = np.log(I0) - (Rg ** 2 / 3.0) * (qc ** 2)
    residuals = y_actual - y_fit
    ss_res = np.sum(residuals ** 2)
    ss_tot = np.sum((y_actual - np.mean(y_actual)) ** 2)
    r2 = (1.0 - ss_res / ss_tot) if ss_tot > 0 else None
    if r2 is not None and r2 >= GUINIER_CLASSIFICATION_R2_MIN:
        return "linear"
    xc = qc ** 2
    np_result = _upturn_downturn_nonparametric(xc, y_actual, Rg)
    if np_result is not None:
        return np_result
    return "chaotic"


def _guinier_fit_n_points(
    q: np.ndarray, I: np.ndarray, n_pts: int
) -> Optional[Dict[str, Any]]:
    """Fit Guinier ln(I) = ln(I0) - (Rg²/3)*q² on the first n_pts points. Returns dict or None."""
    if len(q) < n_pts or np.any(I[:n_pts] <= 0):
        return None
    qn = q[:n_pts]
    In = I[:n_pts]
    x = qn ** 2
    y = np.log(In)
    try:
        coeffs = np.polyfit(x, y, 1)
    except Exception:
        return None
    slope, intercept = coeffs[0], coeffs[1]
    if slope >= 0:
        return None
    rg = np.sqrt(-3.0 * slope)
    i0 = np.exp(intercept)
    y_fit = intercept + slope * x
    ss_res = np.sum((y - y_fit) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    return {
        'Rg': float(rg),
        'I0': float(i0),
        'n_points': n_pts,
        'fit_quality': float(r2),
        'guinier_interval': (float(qn[0]), float(qn[-1])),
    }


def _fit_guinier_interval_raw(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray],
    i_start: int,
    n_pts: int,
    *,
    require_negative_slope: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    Fit Guinier on q[i_start : i_start + n_pts] without q·Rg or R² gates.

    Returns dict with rg, i0, q_min, q_max, r_squared (interval R²), n_points,
    sigma_rg, sigma_i0, i_start; or None if polyfit fails or I≤0 in window.
    If require_negative_slope is False, Rg uses |slope| when slope ≥ 0 (degenerate fallback).
    """
    n = len(q)
    if i_start + n_pts > n:
        return None
    q_sub = q[i_start : i_start + n_pts]
    I_sub = I[i_start : i_start + n_pts]
    if np.any(I_sub <= 0):
        return None
    sig_sub = sigma[i_start : i_start + n_pts] if sigma is not None else None
    x = q_sub ** 2
    y = np.log(I_sub)
    if sig_sub is not None and np.all(sig_sub > 0):
        w = (I_sub / sig_sub) ** 2
    else:
        w = None
    try:
        if w is not None:
            coeffs = np.polyfit(x, y, 1, w=w)
        else:
            coeffs = np.polyfit(x, y, 1)
    except Exception:
        return None
    slope, intercept = coeffs[0], coeffs[1]
    if require_negative_slope and slope >= 0:
        return None
    if slope < 0:
        rg = float(np.sqrt(-3.0 * slope))
    else:
        rg = float(np.sqrt(3.0 * abs(slope)))
    i0 = float(np.exp(intercept))
    if rg <= 0 or i0 <= 0 or not np.isfinite(rg) or not np.isfinite(i0):
        return None
    y_fit = intercept + slope * x
    ss_res = np.sum((y - y_fit) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = float(1.0 - (ss_res / ss_tot)) if ss_tot > 0 else 0.0
    dof = n_pts - 2
    sigma_rg: float = np.nan
    sigma_i0: float = np.nan
    if dof > 0 and ss_res >= 0 and slope < 0:
        res_var = ss_res / dof
        x_mean = np.mean(x)
        sxx = np.sum((x - x_mean) ** 2)
        if sxx > 0:
            var_slope = res_var / sxx
            var_intercept = res_var * (1.0 / n_pts + x_mean ** 2 / sxx)
            sigma_rg = 0.5 * (3.0 / rg) * (var_slope ** 0.5) if slope != 0 else np.nan
            sigma_i0 = i0 * (var_intercept ** 0.5)
    return {
        "rg": rg,
        "i0": i0,
        "q_min": float(q_sub[0]),
        "q_max": float(q_sub[-1]),
        "r_squared": r2,
        "interval_r2": r2,
        "n_points": n_pts,
        "i_start": i_start,
        "sigma_rg": float(sigma_rg) if not np.isnan(sigma_rg) else None,
        "sigma_i0": float(sigma_i0) if not np.isnan(sigma_i0) else None,
    }


def _fit_guinier_interval(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray],
    i_start: int,
    n_pts: int,
    qrg_max: float,
    r2_min: float,
) -> Optional[Dict]:
    """
    Fit Guinier on q[i_start : i_start + n_pts]. Returns dict with rg, i0, q_min, q_max,
    r_squared, n_points, sigma_rg, sigma_i0; or None if fit invalid or filters not passed.
    """
    cand = _fit_guinier_interval_raw(q, I, sigma, i_start, n_pts)
    if cand is None:
        return None
    if cand["q_max"] * cand["rg"] > qrg_max:
        return None
    if cand["r_squared"] < r2_min:
        return None
    out = dict(cand)
    out.pop("interval_r2", None)
    out.pop("i_start", None)
    return out


def _validation_r2_or_nan(
    q: np.ndarray,
    I: np.ndarray,
    Rg: float,
    I0: float,
    k: float = GUINIER_QRG_VALIDATION_K,
) -> float:
    """validation_r2 on [q_max/2, q_max]; NaN if fewer than GUINIER_VALIDATION_MIN_POINTS in band."""
    val = _validation_r2_guinier(
        q, I, Rg, I0, k=k, min_pts=GUINIER_VALIDATION_MIN_POINTS
    )
    if val is None:
        return float("nan")
    return float(val)


def _adaptive_i_start_allowed(q: np.ndarray, i_start: int) -> bool:
    return float(q[i_start]) < ADAPTIVE_I_START_Q_MAX_NM or i_start < ADAPTIVE_I_START_INDEX_MAX


def _enumerate_adaptive_candidates(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray] = None,
    *,
    n_min: int = ADAPTIVE_N_MIN,
    max_pts: int = ADAPTIVE_MAX_PTS,
) -> List[Dict[str, Any]]:
    """All Guinier fits for constrained i_start and n_pts in [n_min, min(max_pts, n-i_start)]."""
    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    if sigma is not None:
        sigma = np.asarray(sigma, dtype=float)
    valid = I > 0
    if np.sum(valid) < n_min:
        return []
    q, I = q[valid], I[valid]
    if sigma is not None:
        sigma = sigma[valid]
    n = len(q)
    candidates: List[Dict[str, Any]] = []
    for i_start in range(0, max(0, n - n_min + 1)):
        if not _adaptive_i_start_allowed(q, i_start):
            continue
        for n_pts in range(n_min, min(max_pts, n - i_start) + 1):
            fit = _fit_guinier_interval_raw(q, I, sigma, i_start, n_pts)
            if fit is None:
                continue
            fit = dict(fit)
            fit["validation_r2"] = _validation_r2_or_nan(q, I, fit["rg"], fit["i0"])
            candidates.append(fit)
    return candidates


def _candidate_tie_key(c: Dict[str, Any]) -> Tuple[float, int, int, float]:
    """Tie-break: larger n_pts, smaller i_start, smaller q_min."""
    return (c["n_points"], -c["i_start"], -c["q_min"])


def _select_adaptive_candidate(
    candidates: List[Dict[str, Any]],
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Pick winner per adaptive spec. Returns (candidate, selection_mode).
    selection_mode: validation_r2 | interval_r2 | best_available
    """
    if not candidates:
        return None, "best_available"

    def key_validation(c: Dict[str, Any]) -> Tuple[float, float, int, int, float]:
        return (c["validation_r2"], *_candidate_tie_key(c))

    def key_interval(c: Dict[str, Any]) -> Tuple[float, float, int, int, float]:
        return (c["interval_r2"], *_candidate_tie_key(c))

    def combined_metric(c: Dict[str, Any]) -> float:
        v = c["validation_r2"]
        if np.isnan(v):
            return float(c["interval_r2"])
        return float(max(v, c["interval_r2"]))

    def key_combined(c: Dict[str, Any]) -> Tuple[float, float, int, int, float]:
        return (combined_metric(c), *_candidate_tie_key(c))

    with_finite_val = [c for c in candidates if not np.isnan(c["validation_r2"])]
    if with_finite_val:
        max_val = max(c["validation_r2"] for c in with_finite_val)
        if max_val >= ADAPTIVE_SELECTION_R2_MIN:
            return max(with_finite_val, key=key_validation), "validation_r2"

    max_interval = max(c["interval_r2"] for c in candidates)
    if max_interval >= ADAPTIVE_SELECTION_R2_MIN:
        return max(candidates, key=key_interval), "interval_r2"

    return max(candidates, key=key_combined), "best_available"


def _quality_class_from_selection(
    selection_mode: str,
    validation_r2: float,
    interval_r2: float,
    *,
    degenerate: bool = False,
) -> str:
    if degenerate:
        return "degenerate"
    if selection_mode == "validation_r2":
        if not np.isnan(validation_r2) and validation_r2 >= ADAPTIVE_VALIDATED_STRONG_R2:
            return "validated_strong"
        return "validated"
    if selection_mode == "interval_r2":
        return "interval_only"
    return "weak"


def _degenerate_adaptive_fallback(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray],
    *,
    n_min: int = ADAPTIVE_N_MIN,
) -> Dict[str, Any]:
    """Always produce a minimal adaptive result when enumeration finds no physical fits."""
    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    valid = I > 0
    if np.sum(valid) < n_min:
        raise ValueError(
            f"guinier adaptive: need at least {n_min} points with I>0, got {int(np.sum(valid))}"
        )
    q, I = q[valid], I[valid]
    if sigma is not None:
        sigma = np.asarray(sigma, dtype=float)[valid]

    res = _guinier_fit_n_points(q, I, n_min)
    if res is not None:
        rg = float(res["Rg"])
        i0 = float(res["I0"])
        q_min, q_max = res["guinier_interval"]
        interval_r2 = float(res["fit_quality"])
    else:
        fit = _fit_guinier_interval_raw(
            q, I, sigma, 0, n_min, require_negative_slope=False
        )
        if fit is None:
            raise ValueError("guinier adaptive: degenerate fallback could not fit data")
        rg = fit["rg"]
        i0 = fit["i0"]
        q_min, q_max = fit["q_min"], fit["q_max"]
        interval_r2 = fit["interval_r2"]

    val_r2 = _validation_r2_or_nan(q, I, rg, i0)
    return {
        "rg": rg,
        "i0": i0,
        "q_min": float(q_min),
        "q_max": float(q_max),
        "n_points": n_min,
        "i_start": 0,
        "interval_r2": interval_r2,
        "validation_r2": val_r2,
        "r_squared": interval_r2,
        "sigma_rg": None,
        "sigma_i0": None,
        "rg_min": rg,
        "rg_max": rg,
        "selection_mode": "best_available",
        "quality_class": "degenerate",
        "fit_quality": interval_r2 if np.isnan(val_r2) else float(max(val_r2, interval_r2)),
        "n_candidates": 0,
        "degenerate": True,
    }


def run_adaptive_guinier(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """
    Adaptive Guinier: constrained sliding windows, min/max Rg span, validation-first selection.

    Always returns rg, interval, fit_quality, quality_class, classification, rg_min, rg_max.
    """
    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    if sigma is not None:
        sigma = np.asarray(sigma, dtype=float)

    candidates = _enumerate_adaptive_candidates(q, I, sigma)
    degenerate = False

    if candidates:
        rg_min = float(min(c["rg"] for c in candidates))
        rg_max = float(max(c["rg"] for c in candidates))
        chosen, selection_mode = _select_adaptive_candidate(candidates)
        assert chosen is not None
    else:
        degenerate = True
        fb = _degenerate_adaptive_fallback(q, I, sigma)
        chosen = fb
        selection_mode = fb["selection_mode"]
        rg_min = fb["rg_min"]
        rg_max = fb["rg_max"]
        candidates = []

    rg = chosen["rg"]
    i0 = chosen["i0"]
    val_r2 = chosen.get("validation_r2", float("nan"))
    interval_r2 = chosen.get("interval_r2", chosen.get("r_squared", 0.0))
    if isinstance(val_r2, float) and np.isnan(val_r2):
        val_r2_out: Optional[float] = None
    else:
        val_r2_out = float(val_r2)

    if selection_mode == "validation_r2" and not np.isnan(val_r2):
        fit_quality = float(val_r2)
    else:
        fit_quality = float(interval_r2)

    quality_class = _quality_class_from_selection(
        selection_mode, float(val_r2) if not np.isnan(val_r2) else float("nan"), float(interval_r2),
        degenerate=degenerate,
    )
    classification = _classification_guinier(q, I, rg, i0)

    return {
        "Rg": rg,
        "I0": i0,
        "n_points": chosen["n_points"],
        "fit_quality": fit_quality,
        "guinier_interval": (chosen["q_min"], chosen["q_max"]),
        "interval_r2": float(interval_r2),
        "validation_r2": val_r2_out,
        "sigma_rg": chosen.get("sigma_rg"),
        "sigma_i0": chosen.get("sigma_i0"),
        "rg_min": rg_min,
        "rg_max": rg_max,
        "selection_mode": selection_mode,
        "quality_class": quality_class,
        "classification": classification,
        "n_candidates": len(candidates),
        "i_start": chosen.get("i_start"),
    }


def find_guinier_region(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray] = None,
    n_min: int = 5,
    qrg_max: float = 1.3,
    r2_min: float = 0.9,
    max_pts: int = 80,
    try_sliding: bool = True,
) -> Optional[Dict]:
    """
    Find the Guinier region and fit Rg, I(0).
    ln(I) = ln(I0) - (Rg²/3)*q²; valid for q*Rg < ~1.3.

    Tries contiguous ranges (try_sliding); among fits with q_max*Rg < qrg_max
    and R² >= r2_min, selects the one with the *largest* number of points.

    Returns dict with keys: rg, i0, q_min, q_max, r_squared, n_points, sigma_rg, sigma_i0;
    or None if no valid fit.
    """
    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    if sigma is not None:
        sigma = np.asarray(sigma, dtype=float)
    valid = I > 0
    if np.sum(valid) < n_min:
        return None
    q, I = q[valid], I[valid]
    if sigma is not None:
        sigma = sigma[valid]
    n = len(q)

    best = None
    best_n_pts = -1
    starts = [0] if not try_sliding else range(0, max(1, n - n_min + 1))
    for i_start in starts:
        for n_pts in range(n_min, min(n - i_start, max_pts) + 1):
            cand = _fit_guinier_interval(q, I, sigma, i_start, n_pts, qrg_max, r2_min)
            if cand is not None and (best is None or n_pts > best_n_pts):
                best = cand
                best_n_pts = n_pts
    return best


def get_guinier_candidates(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray] = None,
    n_min: int = 4,
    qrg_max: float = 1.3,
    r2_min: float = 0.88,
    max_pts: int = 80,
    try_sliding: bool = True,
) -> List[Dict]:
    """
    Return all Guinier fits that pass q*Rg <= qrg_max and fit R² >= r2_min.
    Sliding window with n_min points; useful for adaptive selection by validation R².
    """
    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    if sigma is not None:
        sigma = np.asarray(sigma, dtype=float)
    valid = I > 0
    if np.sum(valid) < n_min:
        return []
    q, I = q[valid], I[valid]
    if sigma is not None:
        sigma = sigma[valid]
    n = len(q)
    results = []
    starts = [0] if not try_sliding else range(0, max(1, n - n_min + 1))
    for i_start in starts:
        for n_pts in range(n_min, min(n - i_start, max_pts) + 1):
            cand = _fit_guinier_interval(q, I, sigma, i_start, n_pts, qrg_max, r2_min)
            if cand is not None:
                results.append(cand)
    return results


def run_guinier_analysis(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray] = None,
    atsas_dat_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run Guinier analyses (first5, first10, autorg, adaptive) and return a unified result dict.

    The final answer is always the adaptive method (see :func:`run_adaptive_guinier`).

    Parameters
    ----------
    q, I, sigma : array-like
        1D SAXS data (q in nm^-1). sigma can be None.
    atsas_dat_path : str or None
        If provided, this path is used for AUTORG (file must exist with 3-column ATSAS format).
        If None, a temporary file is written for AUTORG.

    Returns
    -------
    dict
        Keys:
        - 'first5', 'first10', 'autorg', 'adaptive': per-method results.
        - 'chosen': always 'adaptive'.
        - 'chosen_Rg', 'chosen_I0', 'chosen_quality', 'chosen_n_points', 'chosen_interval'
        - 'chosen_validation_r2', 'classification', 'quality_class', 'rg_min', 'rg_max'
    """
    from autosaxs.core.utils import write_saxs_atsas_format

    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    sigma = np.asarray(sigma, dtype=float) if sigma is not None else None
    n_total = len(q)

    out: Dict[str, Any] = {
        'first5': None,
        'first10': None,
        'autorg': None,
        'adaptive': None,
        'chosen': None,
        'chosen_Rg': None,
        'chosen_I0': None,
        'chosen_quality': None,
        'chosen_n_points': None,
        'chosen_interval': None,
        'chosen_validation_r2': None,
        'classification': None,
        'quality_class': None,
        'selection_mode': None,
        'rg_min': None,
        'rg_max': None,
    }

    # --- 1. Fit first 5 points ---
    res5 = _guinier_fit_n_points(q, I, 5)
    if res5 is not None:
        out['first5'] = res5

    # --- 2. Fit first 10 points ---
    res10 = _guinier_fit_n_points(q, I, 10)
    if res10 is not None:
        out['first10'] = res10

    # --- 3. AUTORG ---
    path_for_autorg = atsas_dat_path
    tmp_autorg_created = False
    if path_for_autorg is None:
        fd, path_for_autorg = tempfile.mkstemp(suffix=".dat", prefix="autosaxs_guinier_")
        try:
            os.close(fd)
            write_saxs_atsas_format(path_for_autorg, q, I, sigma)
            tmp_autorg_created = True
        except Exception:
            path_for_autorg = None
    if path_for_autorg is not None and os.path.exists(path_for_autorg):
        try:
            parsed = run_autorg_atsas(path_for_autorg, q)
            if parsed is not None:
                # Legacy shape: omit internal DATGNOM indices from the nested summary if desired;
                # keeping full parsed dict is backward compatible plus extra keys.
                out["autorg"] = {
                    "Rg": parsed["Rg"],
                    "I0": parsed.get("I0"),
                    "n_points": parsed.get("n_points"),
                    "fit_quality": parsed.get("fit_quality"),
                    "guinier_interval": parsed.get("guinier_interval"),
                    "first_point_1based": parsed.get("first_point_1based"),
                    "last_point_1based": parsed.get("last_point_1based"),
                }
        finally:
            if tmp_autorg_created and path_for_autorg != atsas_dat_path:
                try:
                    if os.path.exists(path_for_autorg):
                        os.remove(path_for_autorg)
                except OSError:
                    pass

    # --- 4. Adaptive (rewritten selection; always returns) ---
    try:
        out['adaptive'] = run_adaptive_guinier(q, I, sigma=sigma)
    except ValueError:
        out['adaptive'] = None

    if out['adaptive'] is not None:
        r = out['adaptive']
        out['chosen'] = 'adaptive'
        out['chosen_Rg'] = r.get('Rg')
        out['chosen_I0'] = r.get('I0')
        out['chosen_quality'] = r.get('fit_quality')
        out['chosen_n_points'] = r.get('n_points')
        out['chosen_interval'] = r.get('guinier_interval')
        out['chosen_validation_r2'] = r.get('validation_r2')
        out['classification'] = r.get('classification')
        out['quality_class'] = r.get('quality_class')
        out['selection_mode'] = r.get('selection_mode')
        out['rg_min'] = r.get('rg_min')
        out['rg_max'] = r.get('rg_max')

    return out
