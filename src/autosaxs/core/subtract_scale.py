"""Buffer-knee detection and min-ratio scale for SAXS buffer subtraction.

Shared by ``autosaxs.skill.subtract`` and guisaxs liveview (auto ``q_min``/``q_max``).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Tuple

import numpy as np


@dataclass(frozen=True)
class KneeInfo:
    """Detector-knee location on a buffer curve."""

    q_knee: float
    q_peak: float
    score: float
    template_half_width_q: float


def _rolling_median(y: np.ndarray, window: int) -> np.ndarray:
    y = np.asarray(y, dtype=float)
    n = int(y.size)
    win = max(3, int(window))
    if win % 2 == 0:
        win += 1
    half = win // 2
    out = np.empty(n, dtype=float)
    for i in range(n):
        out[i] = float(np.median(y[max(0, i - half) : min(n, i + half + 1)]))
    return out


def _snr_weights(I: np.ndarray, sigma: Optional[np.ndarray]) -> np.ndarray:
    I = np.asarray(I, dtype=float)
    if sigma is None:
        return np.ones_like(I)
    s = np.asarray(sigma, dtype=float)
    w = np.where((s > 0.0) & np.isfinite(s) & np.isfinite(I) & (I > 0.0), I / s, 0.0)
    pos = w > 0.0
    if np.any(pos):
        w = np.clip(w, 0.0, float(np.percentile(w[pos], 95)))
    return w


def detect_buffer_knee(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray] = None,
    *,
    template_q_fraction: float = 0.05,
    search_from_q_fraction: float = 0.40,
    search_to_q_fraction: float = 0.90,
    smooth_points_fraction: float = 0.02,
) -> KneeInfo:
    """
    Locate the high-q detector knee on a **buffer** curve.

    SNR-weighted descending-edge convolution on rolling-median ``log I``:
    score = mean(logI left) − mean(logI right). Absolute cliff height is kept
    (unlike unit-normalized NCC), so the real intensity drop outranks noisy
    tail edges. ``q_knee`` is slightly before the strongest edge (pre-knee cut).
    """
    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    ok = np.isfinite(q) & np.isfinite(I) & (I > 0.0)
    qq = q[ok]
    II = I[ok]
    if qq.size < 32:
        q_hi = float(np.max(q)) if q.size else 0.0
        return KneeInfo(q_knee=q_hi, q_peak=q_hi, score=0.0, template_half_width_q=0.0)

    sig: Optional[np.ndarray] = None
    if sigma is not None:
        sig = np.asarray(sigma, dtype=float)
        if sig.shape != q.shape:
            raise ValueError("sigma must match q shape for knee detection")
        sig = sig[ok]
    w = _snr_weights(II, sig)

    logIs = _rolling_median(np.log(II), max(5, int(round(float(smooth_points_fraction) * qq.size))))
    n = max(21, int(round(float(template_q_fraction) * qq.size)))
    if n % 2 == 0:
        n += 1
    half = n // 2

    score = np.full(qq.size, np.nan, dtype=float)
    for i in range(half, int(qq.size) - half):
        sl_l = slice(i - half, i)
        sl_r = slice(i, i + half)
        wl = np.where(np.isfinite(w[sl_l]) & (w[sl_l] > 0.0), w[sl_l], 0.0)
        wr = np.where(np.isfinite(w[sl_r]) & (w[sl_r] > 0.0), w[sl_r], 0.0)
        if float(wl.sum()) <= 0.0 or float(wr.sum()) <= 0.0:
            continue
        left = float(np.sum(wl * logIs[sl_l]) / wl.sum())
        right = float(np.sum(wr * logIs[sl_r]) / wr.sum())
        score[i] = left - right

    q_lo = float(qq.min() + float(search_from_q_fraction) * (qq.max() - qq.min()))
    q_hi = float(qq.min() + float(search_to_q_fraction) * (qq.max() - qq.min()))
    searchable = (qq >= q_lo) & (qq <= q_hi) & np.isfinite(score)
    if not np.any(searchable):
        q_end = float(np.max(qq))
        return KneeInfo(q_knee=q_end, q_peak=q_end, score=0.0, template_half_width_q=0.0)

    idx_peak = int(np.flatnonzero(searchable)[int(np.nanargmax(score[searchable]))])
    q_peak = float(qq[idx_peak])
    q_knee = float(qq[max(0, idx_peak - max(1, half // 3))])
    half_w = float(qq[min(len(qq) - 1, idx_peak + half)] - qq[max(0, idx_peak - half)])
    return KneeInfo(
        q_knee=q_knee,
        q_peak=q_peak,
        score=float(score[idx_peak]),
        template_half_width_q=half_w,
    )


def pre_knee_q_range(
    q: np.ndarray,
    q_knee: float,
    *,
    pre_knee_fraction: float = 0.35,
) -> Tuple[float, float]:
    """``[q_min, q_max]`` = last ``pre_knee_fraction`` of ``[min(q), q_knee]``."""
    q = np.asarray(q, dtype=float)
    q_lo_data = float(np.min(q))
    q_hi = float(q_knee)
    if q_hi <= q_lo_data:
        raise ValueError(f"knee q={q_hi} must exceed data q_min={q_lo_data}")
    frac = float(pre_knee_fraction)
    if not np.isfinite(frac) or frac <= 0.0 or frac > 1.0:
        raise ValueError(f"pre_knee_fraction must be in (0, 1], got {pre_knee_fraction!r}")
    q_lo = q_lo_data + (1.0 - frac) * (q_hi - q_lo_data)
    return q_lo, q_hi


def pre_knee_q_window(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray] = None,
    *,
    pre_knee_fraction: float = 0.35,
    **knee_kwargs: Any,
) -> Tuple[float, float, KneeInfo]:
    """Detect buffer knee and return ``(q_min, q_max, knee_info)``."""
    knee = detect_buffer_knee(q, I, sigma, **knee_kwargs)
    q_min, q_max = pre_knee_q_range(q, knee.q_knee, pre_knee_fraction=pre_knee_fraction)
    return q_min, q_max, knee


def pre_knee_q_window_from_dat(
    path: str,
    *,
    pre_knee_fraction: float = 0.35,
    **knee_kwargs: Any,
) -> Tuple[float, float, KneeInfo]:
    """Load a buffer ``.dat`` and return auto ``(q_min, q_max, knee_info)``."""
    from autosaxs.core.utils import read_saxs

    q, I, sigma, _ = read_saxs(path)
    return pre_knee_q_window(
        np.asarray(q, dtype=float),
        np.asarray(I, dtype=float),
        None if sigma is None else np.asarray(sigma, dtype=float),
        pre_knee_fraction=pre_knee_fraction,
        **knee_kwargs,
    )


def minimal_ratio_scale(
    q: np.ndarray,
    I_sample: np.ndarray,
    I_buffer: np.ndarray,
    *,
    sigma_sample: Optional[np.ndarray] = None,
    sigma_buffer: Optional[np.ndarray] = None,
    q_min: float,
    q_max: float,
    window_q_fraction: float = 0.05,
    approach_factor: float = 0.99,
    snr_min: float = 2.0,
) -> Tuple[float, Dict[str, Any]]:
    """
    Scale = ``approach_factor * min(sliding-window median ratios)`` in ``[q_min, q_max]``.

    Uses raw intensities (no Whittaker). Points below ``snr_min`` are dropped when
    both sigmas are provided. Returns ``(scale, diagnostics)``.
    """
    q = np.asarray(q, dtype=float)
    I_s = np.asarray(I_sample, dtype=float)
    I_b = np.asarray(I_buffer, dtype=float)
    if q.shape != I_s.shape or q.shape != I_b.shape:
        raise ValueError("q, I_sample, I_buffer must share the same shape")

    frac = float(window_q_fraction)
    if not np.isfinite(frac) or frac <= 0.0:
        raise ValueError(f"window_q_fraction must be finite and > 0, got {window_q_fraction!r}")

    q_lo = float(q_min)
    q_hi = float(q_max)
    if q_hi <= q_lo:
        raise ValueError(f"q range must have positive span, got [{q_lo}, {q_hi}]")

    mask = (q >= q_lo) & (q <= q_hi)
    q_sel = q[mask]
    Is_sel = I_s[mask]
    Ib_sel = I_b[mask]
    point_ok = (Ib_sel > 0.0) & (Is_sel >= 0.0) & np.isfinite(Is_sel) & np.isfinite(Ib_sel)

    snr_gate = float(snr_min)
    if snr_gate > 0.0 and sigma_sample is not None and sigma_buffer is not None:
        ss = np.asarray(sigma_sample, dtype=float)[mask]
        sb = np.asarray(sigma_buffer, dtype=float)[mask]
        snr_s = np.where(ss > 0.0, Is_sel / ss, 0.0)
        snr_b = np.where(sb > 0.0, Ib_sel / sb, 0.0)
        point_ok = point_ok & (snr_s >= snr_gate) & (snr_b >= snr_gate)

    q_sel = q_sel[point_ok]
    Is_sel = Is_sel[point_ok]
    Ib_sel = Ib_sel[point_ok]
    n = int(q_sel.size)
    if n < 4:
        raise ValueError(f"minimal_ratio needs >= 4 valid points in band, got {n}")

    ratios = Is_sel / Ib_sel
    win_dq = frac * (q_hi - q_lo)
    window_medians: list[float] = []
    window_centers: list[float] = []
    for i in range(n):
        q_start = float(q_sel[i])
        in_win = (q_sel >= q_start) & (q_sel <= q_start + win_dq)
        wr = ratios[in_win]
        wr = wr[wr > 0.0]
        if wr.size < 2:
            continue
        window_medians.append(float(np.median(wr)))
        window_centers.append(q_start + 0.5 * win_dq)
    if not window_medians:
        raise ValueError("minimal_ratio: no valid sliding windows")

    meds = np.asarray(window_medians, dtype=float)
    centers = np.asarray(window_centers, dtype=float)
    imin = int(np.argmin(meds))
    scale = float(approach_factor) * float(meds[imin])
    diag = {
        "q_min": q_lo,
        "q_max": q_hi,
        "n_windows": int(meds.size),
        "n_points": n,
        "contact_q": float(centers[imin]),
        "contact_ratio": float(meds[imin]),
        "snr_min": snr_gate,
        "window_q_fraction": frac,
        "approach_factor": float(approach_factor),
    }
    return scale, diag


def knee_info_as_dict(knee: KneeInfo) -> Dict[str, Any]:
    return asdict(knee)
