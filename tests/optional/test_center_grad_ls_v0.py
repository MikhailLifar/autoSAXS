"""
Estimate SAXS beam center from gradient consensus using robust line-distance objective (v0).

Intuition:
  - Each pixel with a reliable intensity gradient defines a line through the pixel
    parallel to the gradient direction.
  - The true beam center is the point in R^2 that is "most likely" to lie on (near)
    most such lines.
Robustification:
  - Use coherence (structure-tensor) to downweight locally incoherent gradients.
  - Use a robust loss rho (Huber) with sigma estimated from data.
Simplifications for v0 (per request):
  - No external masking stage.
  - No explicit m_min limitation.
  - No extra refinement stage after the initial robust optimization.

Outputs (per image):
  - coherence map
  - weight map
  - gradient magnitude map
  - residual distance map at the estimated center
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import scipy.ndimage as ndi
from scipy.optimize import minimize

from autosaxs.core.utils import read_from_tiff
from autosaxs.skill.calibrate.autocalib import _log1p_image  # reuse canonical log+NaN handling


WORKSPACE_ROOT = Path("/home/mikl/KurchatovCoop")
REPOS_DIR = WORKSPACE_ROOT / "repos"


@dataclass(frozen=True)
class CenterEstimate:
    center_yx: Tuple[float, float]
    ok: bool
    reason: str
    n_used: int
    n_total: int
    sigma_px: float
    objective: float


def _nan_safe_central_grad(J: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    Central differences computed only where required neighbors are finite.
    This handles NaN stripes without introducing spurious gradients.
    """
    J = np.asarray(J, dtype=float)
    H, W = J.shape
    gx = np.full((H, W), np.nan, dtype=float)  # dJ/dx
    gy = np.full((H, W), np.nan, dtype=float)  # dJ/dy

    # x-gradient: requires (y, x-1) and (y, x+1) finite
    left = J[:, :-2]
    right = J[:, 2:]
    ok_x = np.isfinite(left) & np.isfinite(right)
    gx[:, 1:-1][ok_x] = 0.5 * (right[ok_x] - left[ok_x])

    # y-gradient: requires (y-1, x) and (y+1, x) finite
    up = J[:-2, :]
    down = J[2:, :]
    ok_y = np.isfinite(up) & np.isfinite(down)
    gy[1:-1, :][ok_y] = 0.5 * (down[ok_y] - up[ok_y])

    return gx, gy


def _nanmean_box_filter(J: np.ndarray, size: int = 25) -> np.ndarray:
    """
    NaN-aware mean (box) filter of given size.
    If a window has no finite values, output is NaN.
    """
    J = np.asarray(J, dtype=float)
    finite = np.isfinite(J)
    if not np.any(finite):
        return np.full_like(J, np.nan, dtype=float)
    J0 = np.where(finite, J, 0.0)
    num = ndi.uniform_filter(J0, size=size, mode="nearest")
    den = ndi.uniform_filter(finite.astype(float), size=size, mode="nearest")
    out = np.full_like(J, np.nan, dtype=float)
    ok = den > 0
    out[ok] = num[ok] / den[ok]
    out[~finite] = np.nan
    return out


def _iqr_upper(x: np.ndarray, k: float = 1.5) -> float:
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    if x.size == 0:
        return float("nan")
    q1 = float(np.quantile(x, 0.25))
    q3 = float(np.quantile(x, 0.75))
    return q3 + k * (q3 - q1)


def _huber_rho(z: np.ndarray, k: float) -> np.ndarray:
    """
    Huber loss for nonnegative z.
    rho(z) = 0.5 z^2                if z <= k
          = k (z - 0.5 k)        if z > k
    """
    z = np.asarray(z, dtype=float)
    out = np.empty_like(z, dtype=float)
    m = z <= k
    out[m] = 0.5 * z[m] ** 2
    out[~m] = float(k) * (z[~m] - 0.5 * float(k))
    return out


def _structure_tensor_coherence(
    gx: np.ndarray,
    gy: np.ndarray,
    *,
    sigma: float = 3.0,
    eps: float = 1e-12,
) -> np.ndarray:
    """
    Coherence from the local structure tensor of the gradient field.

    Uses smoothed second moments:
      Sxx = <gx^2>, Syy = <gy^2>, Sxy = <gx*gy>
    Coherence:
      kappa = (lambda1 - lambda2) / (lambda1 + lambda2)
            = sqrt((Sxx-Syy)^2 + 4 Sxy^2) / (Sxx + Syy)
    """
    finite = np.isfinite(gx) & np.isfinite(gy)
    if not np.any(finite):
        return np.zeros_like(gx, dtype=float)

    gx0 = np.where(finite, gx, 0.0)
    gy0 = np.where(finite, gy, 0.0)
    mask = finite.astype(float)

    den = ndi.gaussian_filter(mask, sigma=sigma, mode="nearest")
    den = np.maximum(den, eps)

    Sxx = ndi.gaussian_filter(gx0 * gx0, sigma=sigma, mode="nearest") / den
    Syy = ndi.gaussian_filter(gy0 * gy0, sigma=sigma, mode="nearest") / den
    Sxy = ndi.gaussian_filter(gx0 * gy0, sigma=sigma, mode="nearest") / den

    num = np.sqrt((Sxx - Syy) ** 2 + 4.0 * (Sxy**2))
    den2 = Sxx + Syy + eps
    kappa = num / den2
    kappa[~np.isfinite(kappa)] = 0.0
    return np.clip(kappa, 0.0, 1.0)


def estimate_center_grad_line_robust_v0(
    img_raw: np.ndarray,
    *,
    mean_filter_size: int = 25,
    stride: int = 2,
    p: float = 2.0,
    iqr_k: float = 1.5,
    coherence_sigma: float = 3.0,
    beta_coh: float = 2.0,
    huber_k: float = 1.345,
    sigma_floor_px: float = 1e-3,
    eps_dir: float = 1e-12,
    maxiter: int = 200,
    refine: bool = True,
    refine_maxiter: Optional[int] = None,
    refine_d_quantile: float = 0.9,
    q_sigma_init_sample: int = 1_0000,
    rng_seed: int = 0,
) -> Tuple[CenterEstimate, Dict[str, np.ndarray]]:
    """
    Robust center estimate using a dimensionless weighted Huber objective:

      E(c) = sum_i w_m,i * w_k,i * Huber( log1p(d_i(c)), sigma_d )

    where:
      - d_i(c) is perpendicular distance from c to the line through pixel i
        parallel to its local gradient direction.
      - w_m,i = sigmoid((m_i - b_m)/sigma_m)
      - w_k,i = sigmoid((k_i - b_k)/sigma_k)
      - sigma_d = log1p( hypot(H, W) / 5 )

    Notes:
      - b_m, b_k, sigma_m, sigma_k are chosen from quantiles of m and k.
      - If `refine=True`, a second stage is run with the "elite majority voting" objective.
    """
    img_raw = np.asarray(img_raw, dtype=float)
    H, W = img_raw.shape

    # (1) Log transform and smoothing
    J = _log1p_image(img_raw)
    J_smooth = _nanmean_box_filter(J, size=mean_filter_size)

    # (2) Gradients
    gx, gy = _nan_safe_central_grad(J_smooth)

    finite_g = np.isfinite(gx) & np.isfinite(gy)
    n_total = int(np.sum(finite_g))
    if n_total == 0:
        est = CenterEstimate(
            (float("nan"), float("nan")),
            ok=False,
            reason="no finite gradients",
            n_used=0,
            n_total=0,
            sigma_px=float("nan"),
            objective=float("nan"),
        )
        dbg = {"J_smooth": J_smooth, "gx": gx, "gy": gy}
        return est, dbg

    # (3) Gradient direction (unit in the limiting case; weight handles zero magnitude)
    m2 = np.sqrt(gx * gx + gy * gy)
    inv = 1.0 / np.maximum(m2, eps_dir)
    vx = gx * inv  # gradient direction x-component (unit-like)
    vy = gy * inv  # gradient direction y-component

    # (4) Gradient magnitude measure (m_i)
    mp = np.full((H, W), np.nan, dtype=float)
    if p == 0.0 or p == 2.0:
        mp[finite_g] = m2[finite_g]
    else:
        mp[finite_g] = (np.abs(gx[finite_g]) ** p + np.abs(gy[finite_g]) ** p) ** (1.0 / p)

    # (5) Coherence map kappa (k_i)
    kappa = _structure_tensor_coherence(gx, gy, sigma=coherence_sigma)
    m_vals = mp[finite_g]
    k_vals = kappa[finite_g]
    if m_vals.size == 0 or k_vals.size == 0:
        est = CenterEstimate(
            (float("nan"), float("nan")),
            ok=False,
            reason="no finite m/k values",
            n_used=0,
            n_total=n_total,
            sigma_px=float("nan"),
            objective=float("nan"),
        )
        dbg = {"J_smooth": J_smooth, "gx": gx, "gy": gy, "m": m2, "mp": mp, "kappa": kappa}
        return est, dbg

    def _sigmoid(x: np.ndarray) -> np.ndarray:
        # Stable sigmoid (avoid overflow in exp for extreme values).
        x = np.asarray(x, dtype=float)
        x = np.clip(x, -50.0, 50.0)
        return 1.0 / (1.0 + np.exp(-x))

    # Quantile-calibrated sigmoid gates.
    # Intent:
    #   - top ~20% of m => sigmoid(m) mostly in upper half
    #   - top ~50% of k => sigmoid(k) mostly in upper half
    b_m = float(np.quantile(m_vals, 0.80))
    b_k = float(np.quantile(k_vals, 0.50))

    # Choose sigma so the upper quantile maps to a fairly high weight.
    # These are hyperparameters but keep the method largely dimensionless.
    w_target = 0.90
    logit = float(np.log(w_target / (1.0 - w_target)))  # e.g. ln(9) for w_target=0.9

    m_hi = float(np.quantile(m_vals, 0.95))
    k_hi = float(np.quantile(k_vals, 0.75))
    sigma_m = float((m_hi - b_m) / max(logit, 1e-12))
    sigma_k = float((k_hi - b_k) / max(logit, 1e-12))

    # Fallbacks if the distribution is degenerate.
    if not np.isfinite(sigma_m) or sigma_m <= 0.0:
        sigma_m = float(np.maximum(np.std(m_vals), 1e-12))
    if not np.isfinite(sigma_k) or sigma_k <= 0.0:
        sigma_k = float(max(np.std(k_vals), 1e-6))

    w = np.zeros((H, W), dtype=float)
    w[finite_g] = _sigmoid((mp[finite_g] - b_m) / sigma_m) * _sigmoid(
        (kappa[finite_g] - b_k) / sigma_k
    )

    # (6) Subsample pixels for optimization and sigma estimation
    grid = np.zeros_like(finite_g, dtype=bool)
    grid[::stride, ::stride] = True
    use = finite_g & grid

    n_used = int(np.sum(use))
    if n_used < 200:
        est = CenterEstimate(
            (float("nan"), float("nan")),
            ok=False,
            reason=f"too few voting pixels (n={n_used})",
            n_used=n_used,
            n_total=n_total,
            sigma_px=float("nan"),
            objective=float("nan"),
        )
        dbg = {
            "J": J,
            "J_smooth": J_smooth,
            "gx": gx,
            "gy": gy,
            "m": m2,
            "mp": mp,
            "kappa": kappa,
            "w": w,
        }
        return est, dbg

    ys, xs = np.nonzero(use)
    vx_s = vx[use]
    vy_s = vy[use]
    w_s = w[use]

    # Avoid all-zero weights.
    w_sum = float(np.sum(w_s))
    if not np.isfinite(w_sum) or w_sum <= 0.0:
        w_s = np.ones_like(w_s, dtype=float)
        w_sum = float(np.sum(w_s))

    # (7) Initial center guess: weighted centroid (in y,x ordering)
    cy0 = float(np.sum(ys.astype(float) * w_s) / w_sum)
    cx0 = float(np.sum(xs.astype(float) * w_s) / w_sum)

    # Distance-scale parameter for Huber on log1p(d).
    sigma_d = float(np.log1p(float(np.hypot(H, W)) / 5.0))
    if not np.isfinite(sigma_d) or sigma_d <= 0.0:
        sigma_d = 1.0

    # (9) Robust objective
    def objective(cy_cx: np.ndarray) -> float:
        cy = float(cy_cx[0])
        cx = float(cy_cx[1])
        # d_i(c): perpendicular distance from c to each gradient-parallel line.
        d = np.abs((cx - xs.astype(float)) * vy_s - (cy - ys.astype(float)) * vx_s)
        d = np.asarray(d, dtype=float)

        # Huber on log1p(d) with threshold sigma_d.
        r = np.log1p(d)
        hub = _huber_rho(r, sigma_d)

        val = float(np.mean(w_s * hub))
        if not np.isfinite(val):
            return float("inf")
        return val

    # Optimize (no explicit refinement stage; this is the only optimization pass).
    res = minimize(
        objective,
        x0=np.asarray([cy0, cx0], dtype=float),
        method="Powell",
        options={"maxiter": int(maxiter), "xtol": 1e-3, "ftol": 1e-6, "disp": False},
    )

    cy_opt = float(res.x[0]) if np.all(np.isfinite(res.x)) and res.x.shape == (2,) else float("nan")
    cx_opt = float(res.x[1]) if np.all(np.isfinite(res.x)) and res.x.shape == (2,) else float("nan")

    cy_guess, cx_guess = cy_opt, cx_opt
    obj1 = float(res.fun) if np.isfinite(res.fun) else float("nan")

    # Refinement stage (elite majority voting).
    cy_ref, cx_ref = cy_guess, cx_guess
    sigma_d_ref = float(sigma_d)
    obj2 = obj1
    ok2 = bool(res.success) and np.isfinite(cy_guess) and np.isfinite(cx_guess)
    reason2 = "ok" if res.success else "ok (non-success but finite)"
    q_ref = float("nan")

    if refine and np.isfinite(cy_guess) and np.isfinite(cx_guess):
        # Use first-stage distances to set sigma_d_ref.
        d_guess = np.abs(
            (cx_guess - xs.astype(float)) * vy_s - (cy_guess - ys.astype(float)) * vx_s
        )
        d_guess = d_guess[np.isfinite(d_guess)]
        if d_guess.size > 0:
            q_ref = float(np.quantile(d_guess, refine_d_quantile))
            logit_09 = float(np.log(0.9 / 0.1))
            if (
                np.isfinite(q_ref)
                and q_ref > 0.0
                and np.isfinite(logit_09)
                and logit_09 > 0.0
            ):
                sigma_d_ref = q_ref / logit_09

        if refine_maxiter is None:
            refine_maxiter_eff = max(40, int(maxiter // 2))
        else:
            refine_maxiter_eff = int(refine_maxiter)

        def _sigmoid_stable(x: np.ndarray) -> np.ndarray:
            x = np.asarray(x, dtype=float)
            x = np.clip(x, -50.0, 50.0)
            return 1.0 / (1.0 + np.exp(-x))

        def objective_ref(cy_cx: np.ndarray) -> float:
            cy = float(cy_cx[0])
            cx = float(cy_cx[1])
            d = np.abs((cx - xs.astype(float)) * vy_s - (cy - ys.astype(float)) * vx_s)
            z = d / float(sigma_d_ref)
            s = _sigmoid_stable(z)
            # Negative when d is small, positive when d is large.
            val = float(np.sum(w_s * (s - 0.5)))
            if not np.isfinite(val):
                return float("inf")
            return val

        res2 = minimize(
            objective_ref,
            x0=np.asarray([cy_guess, cx_guess], dtype=float),
            method="Powell",
            options={"maxiter": int(refine_maxiter_eff), "xtol": 1e-3, "ftol": 1e-6, "disp": False},
        )

        cy_ref = float(res2.x[0]) if np.all(np.isfinite(res2.x)) and res2.x.shape == (2,) else float("nan")
        cx_ref = float(res2.x[1]) if np.all(np.isfinite(res2.x)) and res2.x.shape == (2,) else float("nan")
        obj2 = float(res2.fun) if np.isfinite(res2.fun) else float("nan")
        ok2 = bool(res2.success) and np.isfinite(cy_ref) and np.isfinite(cx_ref)
        reason2 = "refined ok" if res2.success else "refined non-success but finite"

    if not np.isfinite(cy_ref) or not np.isfinite(cx_ref):
        est = CenterEstimate(
            (float("nan"), float("nan")),
            ok=False,
            reason="optimization failed/refinement failed",
            n_used=n_used,
            n_total=n_total,
            sigma_px=float(sigma_d_ref) if np.isfinite(sigma_d_ref) else float("nan"),
            objective=float("nan"),
        )
    else:
        est = CenterEstimate(
            (cy_ref, cx_ref),
            ok=ok2,
            reason=reason2,
            n_used=n_used,
            n_total=n_total,
            sigma_px=float(sigma_d_ref),
            objective=obj2,
        )

    # (10) Diagnostic maps (full-resolution)
    cy_plot, cx_plot = est.center_yx
    # residual distance map d(y,x) = |(cx-x)*vy - (cy-y)*vx|
    yy, xx = np.indices((H, W), dtype=float)
    vx_full = np.where(np.isfinite(vx), vx, 0.0)
    vy_full = np.where(np.isfinite(vy), vy, 0.0)
    d_map = np.abs((cx_plot - xx) * vy_full - (cy_plot - yy) * vx_full)
    d_map[~finite_g] = np.nan

    dbg = {
        "J": J,
        "J_smooth": J_smooth,
        "gx": gx,
        "gy": gy,
        "m": m2,
        "mp": mp,
        "kappa": kappa,
        "w": w,
        "d": d_map,
        "center_yx": np.asarray([cy_plot, cx_plot], dtype=float),
        "use_mask": use,
        "bm": np.asarray([b_m], dtype=float),
        "bk": np.asarray([b_k], dtype=float),
        "sigma_m": np.asarray([sigma_m], dtype=float),
        "sigma_k": np.asarray([sigma_k], dtype=float),
        # Stage-1 Huber distance scale.
        "sigma_d_huber": np.asarray([sigma_d], dtype=float),
        # Stage-2 refinement distance scale.
        "sigma_d": np.asarray([sigma_d_ref], dtype=float),
        "center_yx_initial": np.asarray([cy_guess, cx_guess], dtype=float),
        "refine_d_quantile": np.asarray([refine_d_quantile], dtype=float),
        "q_d_guess": np.asarray([q_ref], dtype=float) if "q_ref" in locals() else np.asarray([np.nan], dtype=float),
        "objective1": np.asarray([obj1], dtype=float),
        "objective2": np.asarray([obj2], dtype=float),
    }
    return est, dbg


def _save_debug_plot_maps(
    img_raw: np.ndarray,
    est: CenterEstimate,
    dbg: Dict[str, np.ndarray],
    *,
    out_path: Path,
) -> None:
    J_smooth = dbg.get("J_smooth")
    # Raw gradient components for quiver visualization.
    gx = dbg.get("gx")
    gy = dbg.get("gy")
    kappa = dbg.get("kappa")
    w = dbg.get("w")
    m = dbg.get("m")
    d = dbg.get("d")

    # Keep the requested maps, but also show the original image for context.
    # Layout:
    #   (0,0) original log1p image (with center overlay)
    #   (0,1) coherence map
    #   (0,2) weight map
    #   (1,0) gradient magnitude map
    #   (1,1) residual distance map
    #   (1,2) unused (kept for easy expansion/debug)
    fig, axs = plt.subplots(2, 3, figsize=(16, 11))
    axs = np.asarray(axs).reshape(2, 3)

    cy, cx = est.center_yx
    title = f"{out_path.stem}\nok={est.ok} center_yx=({cy:.1f},{cx:.1f}) sigma_d={est.sigma_px:.2g}"
    fig.suptitle(title, fontsize=12)

    # (0,0) Original log1p image
    J0 = _log1p_image(img_raw)
    axs[0, 0].imshow(J0, origin="lower", cmap="viridis")
    axs[0, 0].set_title("original log1p image")
    axs[0, 0].plot([cx], [cy], "r+", markersize=12, markeredgewidth=3)
    axs[0, 0].axhline(cy, color="r", lw=1, alpha=0.25)
    axs[0, 0].axvline(cx, color="r", lw=1, alpha=0.25)

    # Coherence map
    im0 = axs[0, 1].imshow(kappa, origin="lower", cmap="magma")
    axs[0, 1].set_title("coherence (structure tensor)")
    fig.colorbar(im0, ax=axs[0, 1], fraction=0.046, pad=0.04)

    # Gradient magnitude map
    grad_vis = m
    im2 = axs[1, 0].imshow(np.log1p(np.maximum(grad_vis, 0.0)), origin="lower", cmap="magma")
    axs[1, 0].set_title("gradient magnitude (log1p)")
    fig.colorbar(im2, ax=axs[1, 0], fraction=0.046, pad=0.04)

    # Weight map
    im1 = axs[0, 2].imshow(w, origin="lower", cmap="viridis")
    axs[0, 2].set_title("weight map (sigmoid(m) * sigmoid(kappa))")
    fig.colorbar(im1, ax=axs[0, 2], fraction=0.046, pad=0.04)

    # Residual distance map
    resid_vis = d
    im3 = axs[1, 1].imshow(np.log1p(np.maximum(resid_vis, 0.0)), origin="lower", cmap="magma")
    axs[1, 1].set_title("residual distance to gradient-parallel line (log1p)")
    fig.colorbar(im3, ax=axs[1, 1], fraction=0.046, pad=0.04)

    # (1,2) Gradient vector field (quiver) on a coarse grid.
    # Sample frequency: one arrow per ~50 pixels along each axis.
    # Each arrow direction/magnitude is the average (gx, gy) over a 7x7 neighborhood.
    if gx is not None and gy is not None and gx.shape == img_raw.shape and gy.shape == img_raw.shape:
        H, W = img_raw.shape
        step = 50
        neigh = 7
        half = neigh // 2
        ys = np.arange(0, H, step, dtype=int)
        xs = np.arange(0, W, step, dtype=int)

        # Compute mean gradients in each neighborhood.
        U = np.full((ys.size, xs.size), np.nan, dtype=float)  # x-component
        V = np.full((ys.size, xs.size), np.nan, dtype=float)  # y-component
        M = np.full((ys.size, xs.size), np.nan, dtype=float)  # magnitude

        gx_arr = np.asarray(gx, dtype=float)
        gy_arr = np.asarray(gy, dtype=float)

        for iy, y in enumerate(ys):
            y0 = max(0, int(y - half))
            y1 = min(H, int(y + half + 1))
            for ix, x in enumerate(xs):
                x0 = max(0, int(x - half))
                x1 = min(W, int(x + half + 1))
                patch_gx = gx_arr[y0:y1, x0:x1]
                patch_gy = gy_arr[y0:y1, x0:x1]
                finite = np.isfinite(patch_gx) & np.isfinite(patch_gy)
                if not np.any(finite):
                    continue
                gx_m = float(np.mean(patch_gx[finite]))
                gy_m = float(np.mean(patch_gy[finite]))
                U[iy, ix] = gx_m
                V[iy, ix] = gy_m
                M[iy, ix] = float(np.hypot(gx_m, gy_m))

        fin = np.isfinite(M) & (M > 0)
        axs[1, 2].imshow(J0, origin="lower", cmap="viridis")
        axs[1, 2].set_title("gradient vectors (mean over 7x7)")
        axs[1, 2].set_xlabel("Pixel X")
        axs[1, 2].set_ylabel("Pixel Y")

        if np.any(fin):
            max_m = float(np.max(M[fin]))
            min_m = float(np.min(M[fin]))
            max_arrow_len = 20.0
            min_arrow_len = 10.0

            if np.isfinite(max_m) and max_m > 0:
                if np.isfinite(min_m) and max_m > min_m:
                    # Map magnitudes linearly into [min_arrow_len, max_arrow_len]
                    # so arrows are never too short to see.
                    arrow_lengths = np.full_like(M, np.nan, dtype=float)
                    arrow_lengths[fin] = min_arrow_len + (M[fin] - min_m) * (
                        (max_arrow_len - min_arrow_len) / (max_m - min_m)
                    )
                else:
                    arrow_lengths = np.full_like(M, np.nan, dtype=float)
                    arrow_lengths[fin] = max_arrow_len
            else:
                arrow_lengths = np.full_like(M, np.nan, dtype=float)

            U_plot = np.zeros_like(U, dtype=float)
            V_plot = np.zeros_like(V, dtype=float)
            with np.errstate(invalid="ignore", divide="ignore"):
                U_plot[fin] = (U[fin] / M[fin]) * arrow_lengths[fin]
                V_plot[fin] = (V[fin] / M[fin]) * arrow_lengths[fin]

            # Quiver expects flattened arrays.
            Yg, Xg = np.meshgrid(ys, xs, indexing="ij")
            axs[1, 2].quiver(
                Xg[fin],
                Yg[fin],
                U_plot[fin],
                V_plot[fin],
                angles="xy",
                scale_units="xy",
                scale=1.0,
                width=0.0030,
                color="red",
                alpha=0.55,
            )

    # Overlay estimated center on map panels.
    if np.isfinite(cy) and np.isfinite(cx):
        for ax in (axs[0, 0], axs[0, 1], axs[0, 2], axs[1, 0], axs[1, 1], axs[1, 2]):
            ax.plot([cx], [cy], "r+", markersize=12, markeredgewidth=3)
            ax.axhline(cy, color="r", lw=1, alpha=0.25)
            ax.axvline(cx, color="r", lw=1, alpha=0.25)

    # Additionally overlay the first-stage center on the original-image panel only.
    center_yx_initial = dbg.get("center_yx_initial")
    if center_yx_initial is not None and np.asarray(center_yx_initial).shape == (2,):
        cy_i, cx_i = float(center_yx_initial[0]), float(center_yx_initial[1])
        if np.isfinite(cy_i) and np.isfinite(cx_i):
            axs[0, 0].plot([cx_i], [cy_i], "y+", markersize=12, markeredgewidth=3)
            axs[0, 0].axhline(cy_i, color="y", lw=1, alpha=0.25)
            axs[0, 0].axvline(cx_i, color="y", lw=1, alpha=0.25)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Robust gradient-line beam center estimate (v0).")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N images (0=all).")
    parser.add_argument("--stride", type=int, default=2, help="Subsample stride for optimization (default: 2).")
    parser.add_argument("--p", type=float, default=2.0, help="Gradient magnitude weight exponent logic (default: 2).")
    parser.add_argument("--iqr-k", type=float, default=1.5, help="IQR multiplier for capping gradient magnitudes.")
    parser.add_argument("--mean-filter-size", type=int, default=25, help="Mean filter size (default: 25).")
    parser.add_argument("--coherence-sigma", type=float, default=3.0, help="Gaussian sigma for structure tensor coherence.")
    parser.add_argument("--beta-coh", type=float, default=2.0, help="Coherence power in weights (default: 2).")
    parser.add_argument("--huber-k", type=float, default=1.345, help="Huber parameter in z-units (default: 1.345).")
    parser.add_argument("--maxiter", type=int, default=200, help="Powell max iterations (default: 200).")
    parser.add_argument("--refine-maxiter", type=int, default=None, help="Powell max iterations for refinement stage.")
    parser.add_argument("--refine-d-quantile", type=float, default=0.9, help="Quantile of d at stage-1 center used to set sigma_d_ref (default: 0.9).")
    parser.add_argument("--no-refine", action="store_true", help="Disable the refinement stage.")
    parser.add_argument("--out-dir", type=str, default="debug_center_grad_ls_v0_sigmoid_huber_refined", help="Output directory for PNGs.")
    args = parser.parse_args()

    data_dir = WORKSPACE_ROOT / "data" / "calib_benchmark"
    out_dir = WORKSPACE_ROOT / "debug" / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    tif_paths = sorted(data_dir.glob("*.tif"))
    if not tif_paths:
        raise FileNotFoundError(f"No tif files found in {data_dir}")
    if args.limit and args.limit > 0:
        tif_paths = tif_paths[: args.limit]

    print(f"DATA_DIR={data_dir} N={len(tif_paths)}")

    for tif_path in tif_paths:
        img = read_from_tiff(tif_path)
        est, dbg = estimate_center_grad_line_robust_v0(
            img,
            mean_filter_size=args.mean_filter_size,
            stride=args.stride,
            p=args.p,
            iqr_k=args.iqr_k,
            coherence_sigma=args.coherence_sigma,
            beta_coh=args.beta_coh,
            huber_k=args.huber_k,
            maxiter=args.maxiter,
            refine=(not args.no_refine),
            refine_maxiter=args.refine_maxiter,
            refine_d_quantile=float(args.refine_d_quantile),
        )

        out_path = out_dir / f"{tif_path.stem}_center_maps.png"
        _save_debug_plot_maps(img, est, dbg, out_path=out_path)
        cy, cx = est.center_yx
        sigma_d = float(np.asarray(dbg.get("sigma_d", np.nan)))
        bm = float(np.asarray(dbg.get("bm", np.nan)))
        bk = float(np.asarray(dbg.get("bk", np.nan)))
        sigma_m = float(np.asarray(dbg.get("sigma_m", np.nan)))
        sigma_k = float(np.asarray(dbg.get("sigma_k", np.nan)))
        refine_q = float(np.asarray(dbg.get("refine_d_quantile", np.nan)))
        q_d_guess = float(np.asarray(dbg.get("q_d_guess", np.nan)))
        msg = (
            f"{tif_path.name}: ok={est.ok} center_yx=({cy:.2f},{cx:.2f}) "
            f"sigma_d={sigma_d:.3g} objective={est.objective:.3g} "
            f"bm={bm:.3g} bk={bk:.3g} sigma_m={sigma_m:.3g} sigma_k={sigma_k:.3g} "
            f"refine_q={refine_q:.3g} q_d_guess={q_d_guess:.3g} "
            f"used={est.n_used}/{est.n_total} -> {out_path}"
        )
        print(msg, flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

