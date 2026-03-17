"""
Guinier analysis for SAXS: Rg, I(0), region finding, and adaptive selection.
"""
from typing import Optional, Any, Dict, List
import os
import re
import tempfile

import numpy as np


# --- Constants ---
ADAPTIVE_QRG_MAX = 1.35
ADAPTIVE_R2_MIN = 0.88
GUINIER_QRG_VALIDATION_K = 1.0
GUINIER_VALIDATION_MIN_POINTS = 2
ADAPTIVE_VALIDATION_MIN_POINTS = 4
GUINIER_CLASSIFICATION_R2_MIN = 0.85
GUINIER_UPTURN_DOWNTURN_FRACTION = 0.85


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
    n = len(q)
    if i_start + n_pts > n:
        return None
    q_sub = q[i_start : i_start + n_pts]
    I_sub = I[i_start : i_start + n_pts]
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
    if slope >= 0:
        return None
    rg = np.sqrt(-3.0 * slope)
    i0 = np.exp(intercept)
    if q_sub[-1] * rg > qrg_max:
        return None
    y_fit = intercept + slope * x
    ss_res = np.sum((y - y_fit) ** 2)
    ss_tot = np.sum((y - np.mean(y)) ** 2)
    r2 = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0
    if r2 < r2_min:
        return None
    dof = n_pts - 2
    if dof > 0 and ss_res >= 0:
        res_var = ss_res / dof
        x_mean = np.mean(x)
        sxx = np.sum((x - x_mean) ** 2)
        if sxx > 0:
            var_slope = res_var / sxx
            var_intercept = res_var * (1.0 / n_pts + x_mean ** 2 / sxx)
            sigma_rg = 0.5 * (3.0 / rg) * (var_slope ** 0.5) if slope != 0 else np.nan
            sigma_i0 = i0 * (var_intercept ** 0.5)
        else:
            sigma_rg = sigma_i0 = np.nan
    else:
        sigma_rg = sigma_i0 = np.nan
    return {
        'rg': float(rg),
        'i0': float(i0),
        'q_min': float(q_sub[0]),
        'q_max': float(q_sub[-1]),
        'r_squared': float(r2),
        'n_points': n_pts,
        'sigma_rg': float(sigma_rg) if not np.isnan(sigma_rg) else None,
        'sigma_i0': float(sigma_i0) if not np.isnan(sigma_i0) else None,
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

    The final answer is always the adaptive method: sliding-window Guinier fits (n_min=4)
    with filters q*Rg <= qrg_max and fit R² >= r2_min; among candidates passing both,
    the one with the best validation R² on [q_max/2, q_max] (q_max = k/Rg, k=1) is chosen,
    with at least 4 points required in the validation interval.

    Classification (for the chosen approximation, in [0, q_max/2]):
      "linear" | "upturn" | "downturn" | "chaotic".

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
        - 'chosen': always 'adaptive' when adaptive result is present.
        - 'chosen_Rg', 'chosen_I0', 'chosen_quality', 'chosen_n_points', 'chosen_interval'
        - 'chosen_validation_r2', 'classification'
    """
    from .utils import write_saxs_atsas_format

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
    if path_for_autorg is None:
        fd, path_for_autorg = tempfile.mkstemp(suffix='.dat', prefix='autosaxs_guinier_')
        try:
            os.close(fd)
            write_saxs_atsas_format(path_for_autorg, q, I, sigma)
        except Exception:
            path_for_autorg = None
    if path_for_autorg is not None and os.path.exists(path_for_autorg):
        tmp_autorg = path_for_autorg + '_autorg.tmp'
        try:
            cmd = f'autorg "{path_for_autorg}"'
            os.system(f'{cmd} > "{tmp_autorg}" 2>&1')
            if os.path.exists(tmp_autorg):
                with open(tmp_autorg, 'r') as f:
                    content = f.read()
                mr = re.search(r'Rg\s+=\s+([\d\.]+)', content)
                mi0 = re.search(r'I\(0\)\s+=\s+([\d\.]+)', content)
                mq = re.search(r'Quality:\s+([\d\.]+)', content)
                mpts = re.search(r'Points\s+(\d+)\s+to\s+(\d+)\s+\((\d+)\s+total\)', content)
                if mr:
                    autorg_rg = float(mr.group(1))
                    autorg_i0 = float(mi0.group(1)) if mi0 else None
                    autorg_quality = float(mq.group(1)) if mq else None
                    autorg_n_pts = int(mpts.group(3)) if mpts else None
                    q_min_autorg = q_max_autorg = None
                    if mpts and autorg_n_pts is not None:
                        i1, i2 = int(mpts.group(1)), int(mpts.group(2))
                        j1 = max(0, min(i1 - 1, n_total - 1))
                        j2 = max(0, min(i2 - 1, n_total - 1))
                        if j1 <= j2:
                            q_min_autorg = float(q[j1])
                            q_max_autorg = float(q[j2])
                    out['autorg'] = {
                        'Rg': autorg_rg,
                        'I0': autorg_i0,
                        'n_points': autorg_n_pts,
                        'fit_quality': autorg_quality,
                        'guinier_interval': (q_min_autorg, q_max_autorg) if (q_min_autorg is not None and q_max_autorg is not None) else None,
                    }
        finally:
            if path_for_autorg == atsas_dat_path:
                pass
            else:
                try:
                    if os.path.exists(path_for_autorg):
                        os.remove(path_for_autorg)
                    if os.path.exists(tmp_autorg):
                        os.remove(tmp_autorg)
                except OSError:
                    pass

    # --- 4. Adaptive: sliding-window fits, select by best validation R² on [q_max/2, q_max] ---
    candidates = get_guinier_candidates(
        q, I, sigma=sigma,
        n_min=4,
        qrg_max=ADAPTIVE_QRG_MAX,
        r2_min=ADAPTIVE_R2_MIN,
        max_pts=80,
        try_sliding=True,
    )
    best_adaptive = None
    best_val_r2 = None
    for cand in candidates:
        rg, i0 = cand['rg'], cand['i0']
        if rg is None or i0 is None or i0 <= 0:
            continue
        val_r2 = _validation_r2_guinier(
            q, I, rg, i0, k=GUINIER_QRG_VALIDATION_K, min_pts=ADAPTIVE_VALIDATION_MIN_POINTS
        )
        if val_r2 is not None and (best_val_r2 is None or val_r2 > best_val_r2):
            best_val_r2 = val_r2
            best_adaptive = cand
    if best_adaptive is not None:
        out['adaptive'] = {
            'Rg': best_adaptive['rg'],
            'I0': best_adaptive['i0'],
            'n_points': best_adaptive['n_points'],
            'fit_quality': best_adaptive['r_squared'],
            'guinier_interval': (best_adaptive['q_min'], best_adaptive['q_max']),
            'sigma_rg': best_adaptive.get('sigma_rg'),
            'sigma_i0': best_adaptive.get('sigma_i0'),
            'validation_r2': best_val_r2,
        }

    # --- Final answer: always adaptive when available ---
    if out['adaptive'] is not None:
        r = out['adaptive']
        out['chosen'] = 'adaptive'
        out['chosen_Rg'] = r.get('Rg')
        out['chosen_I0'] = r.get('I0')
        out['chosen_quality'] = r.get('fit_quality')
        out['chosen_n_points'] = r.get('n_points')
        out['chosen_interval'] = r.get('guinier_interval')
        out['chosen_validation_r2'] = r.get('validation_r2')
        rg_ch = out['chosen_Rg']
        i0_ch = out['chosen_I0']
        if rg_ch is not None and i0_ch is not None:
            out['classification'] = _classification_guinier(q, I, rg_ch, i0_ch)
        else:
            out['classification'] = None

    return out
