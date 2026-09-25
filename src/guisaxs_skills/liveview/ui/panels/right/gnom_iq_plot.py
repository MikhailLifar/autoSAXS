"""I(q) GNOM fit overlay: full curve with gray outside the fit interval."""

from __future__ import annotations

import os
from typing import Optional, Tuple

import numpy as np

from autosaxs.core.gnom import parse_gnom_out


def _fit_q_bounds(parsed: dict) -> Tuple[Optional[float], Optional[float]]:
    ar = parsed.get("angular_range")
    if isinstance(ar, (tuple, list)) and len(ar) == 2:
        try:
            q0, q1 = float(ar[0]), float(ar[1])
            if np.isfinite(q0) and np.isfinite(q1) and q1 > q0:
                return q0, q1
        except (TypeError, ValueError):
            pass
    iq = parsed.get("iq_table")
    if iq and len(iq) >= 1:
        qf = np.asarray(iq[0], dtype=float)
        m = np.isfinite(qf)
        if m.any():
            return float(np.nanmin(qf[m])), float(np.nanmax(qf[m]))
    return None, None


def draw_gnom_iq_on_ax(
    ax,
    gnom_out_path: str,
    *,
    profile_path: str = "",
) -> Optional[str]:
    """
    Draw experimental I(q) + GNOM fit on ``ax``.

    When ``profile_path`` is available, the full curve is shown: points inside the
    GNOM angular range are opaque, outside are transparent gray. Without a profile,
    only the ``.out`` I(q) table is drawn.

    Returns an error status string, or None on success.
    """
    if not gnom_out_path or not os.path.isfile(gnom_out_path):
        return "No GNOM .out"
    try:
        parsed = parse_gnom_out(gnom_out_path)
        iq = parsed.get("iq_table")
    except Exception:
        return "GNOM parse error"
    if not iq or len(iq) != 4:
        return "No I(q) table in .out"
    q_fit, i_exp_fit, _sigma, i_fit = (np.asarray(a, dtype=float) for a in iq)
    m_fit = np.isfinite(q_fit) & np.isfinite(i_fit) & (i_fit > 0)
    if not m_fit.any():
        return "Empty GNOM I(q)"

    q_lo, q_hi = _fit_q_bounds(parsed)
    ax.clear()

    prof = (profile_path or "").strip()
    drew_exp = False
    if prof and os.path.isfile(prof):
        try:
            from autosaxs.core.utils import ensure_q_nm, load_saxs_1d_any

            q_full, I_full, _sig = load_saxs_1d_any(prof)
            q_full, I_full, _sig = ensure_q_nm(q_full, I_full, _sig)
            q_full = np.asarray(q_full, dtype=float)
            I_full = np.asarray(I_full, dtype=float)
            m = np.isfinite(q_full) & np.isfinite(I_full) & (I_full > 0)
            if m.any():
                qq, ii = q_full[m], I_full[m]
                if q_lo is not None and q_hi is not None:
                    inside = (qq >= q_lo) & (qq <= q_hi)
                else:
                    inside = np.ones_like(qq, dtype=bool)
                if (~inside).any():
                    ax.scatter(
                        qq[~inside],
                        ii[~inside],
                        s=8,
                        alpha=0.25,
                        c="0.55",
                        label="exp (out)",
                        zorder=1,
                    )
                if inside.any():
                    ax.scatter(
                        qq[inside],
                        ii[inside],
                        s=8,
                        alpha=0.85,
                        c="C0",
                        label="exp",
                        zorder=2,
                    )
                drew_exp = True
        except Exception:
            drew_exp = False

    if not drew_exp:
        m_exp = np.isfinite(q_fit) & np.isfinite(i_exp_fit) & (i_exp_fit > 0)
        if m_exp.any():
            ax.scatter(q_fit[m_exp], i_exp_fit[m_exp], s=8, alpha=0.7, c="C0", label="exp", zorder=2)

    ax.plot(q_fit[m_fit], i_fit[m_fit], "r-", lw=1.2, label="GNOM", zorder=3)
    ax.set_yscale("log")
    ax.set_xlabel("q (nm⁻¹)")
    ax.set_ylabel("I")
    ax.legend(fontsize=7)
    ax.grid(True, alpha=0.2)
    return None


def draw_gnom_residuals_on_ax(ax, gnom_out_path: str) -> Optional[str]:
    """
    Draw ``ΔI / (σ + 0.1)`` vs q from a GNOM ``.out`` I(q) table.

    Matches the residual panel written by fit_distances / fit_sizes
    (``write_iq_fit_comparison_png``). Returns an error status string, or None.
    """
    if not gnom_out_path or not os.path.isfile(gnom_out_path):
        return "No GNOM .out"
    try:
        parsed = parse_gnom_out(gnom_out_path)
        iq = parsed.get("iq_table")
    except Exception:
        return "GNOM parse error"
    if not iq or len(iq) != 4:
        return "No I(q) table in .out"
    q, i_exp, sigma, i_fit = (np.asarray(a, dtype=float) for a in iq)
    m = (
        np.isfinite(q)
        & np.isfinite(i_exp)
        & np.isfinite(i_fit)
        & np.isfinite(sigma)
        & (sigma >= 0)
    )
    if not m.any():
        # Fall back without requiring finite sigma (use |I|+0.1 like viewer.py).
        m = np.isfinite(q) & np.isfinite(i_exp) & np.isfinite(i_fit)
        if not m.any():
            return "Empty residuals"
        qq, ye, yf = q[m], i_exp[m], i_fit[m]
        denom = np.abs(ye) + 0.1
        ylabel = r"$\Delta I/(|I|+0.1)$"
    else:
        qq, ye, yf, sig = q[m], i_exp[m], i_fit[m], sigma[m]
        if np.any(sig > 0):
            denom = np.abs(sig) + 0.1
            ylabel = r"$\Delta I/(\sigma+0.1)$"
        else:
            denom = np.abs(ye) + 0.1
            ylabel = r"$\Delta I/(|I|+0.1)$"
    with np.errstate(divide="ignore", invalid="ignore"):
        resid = (ye - yf) / denom
    ax.clear()
    ax.plot(qq, resid, "C1-", lw=1.0)
    ax.axhline(0.0, color="0.5", lw=0.8)
    ax.set_xlim(float(qq.min()), float(qq.max()))
    ax.set_xlabel("q (nm⁻¹)")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.25)
    return None
