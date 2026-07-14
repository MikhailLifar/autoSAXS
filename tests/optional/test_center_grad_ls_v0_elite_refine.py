"""
Experimental variant of gradient-line center estimation.

Purpose:
  - Keep the existing stage-1 objective (sigmoid(m)*sigmoid(k)*Huber(log1p(d))).
  - Modify stage-2 refinement:
      sigma_d_ref is computed from distances at the stage-1 guess, but ONLY
      over an "elite" subset of points chosen by their weight magnitude.
      The stage-2 objective is also evaluated over only that elite subset.

This is intended to fix systematic bias cases (e.g. tilted / imperfect ring geometry)
by making the "majority voting of the elite" selection consistent in both
sigma selection and optimization.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import scipy.ndimage as ndi
from scipy.optimize import minimize


WORKSPACE_ROOT = Path("/home/mikl/KurchatovCoop")
REPOS_DIR = WORKSPACE_ROOT / "repos"
sys.path.insert(0, str(REPOS_DIR))

from autosaxs.core.utils import read_from_tiff, load_config  # type: ignore
from autosaxs.skill.calibrate.autocalib import autocalib_ring_analysis  # type: ignore

# Reuse the canonical gradient/weight helpers from the existing test implementation.
from tests.optional import test_center_grad_ls_v0 as base  # type: ignore


@dataclass(frozen=True)
class CenterEstimate:
    center_yx: Tuple[float, float]
    ok: bool
    reason: str
    n_used: int
    n_total: int
    sigma_d_stage1: float
    sigma_d_stage2: float
    objective1: float
    objective2: float


def _sigmoid_stable(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    x = np.clip(x, -50.0, 50.0)
    return 1.0 / (1.0 + np.exp(-x))


def estimate_center_grad_ls_v0_elite_refine(
    img_raw: np.ndarray,
    *,
    mean_filter_size: int = 25,
    stride: int = 2,
    p: float = 2.0,
    coherence_sigma: float = 3.0,
    beta_coh: float = 2.0,
    b_m_quantile: float = 0.80,
    b_k_quantile: float = 0.50,
    m_hi_quantile: float = 0.95,
    k_hi_quantile: float = 0.75,
    w_target: float = 0.90,
    refine_d_quantile: float = 0.9,
    elite_weight_quantile: float = 0.5,
    maxiter: int = 60,
    refine_maxiter: int = 40,
    huber_k: float = 1.345,
    angular_lambda: float = 0.0,
    stage1_multistart_radius_px: float = 0.0,
    stage1_multistart_step_px: float = 20.0,
    stage2_multistart_radius_px: float = 0.0,
    stage2_multistart_step_px: float = 5.0,
) -> Tuple[CenterEstimate, Dict[str, float]]:
    """
    Returns:
      - est: centers for stage-2
      - dbg: selected scalars for quick diagnostics
    """
    img_raw = np.asarray(img_raw, dtype=float)
    H, W = img_raw.shape

    # (1) Log transform + smooth
    J = base._log1p_image(img_raw)
    J_smooth = base._nanmean_box_filter(J, size=mean_filter_size)

    # (2) Gradients
    gx, gy = base._nan_safe_central_grad(J_smooth)

    finite_g = np.isfinite(gx) & np.isfinite(gy)
    n_total = int(np.sum(finite_g))
    if n_total == 0:
        est = CenterEstimate(
            (float("nan"), float("nan")),
            ok=False,
            reason="no finite gradients",
            n_used=0,
            n_total=0,
            sigma_d_stage1=float("nan"),
            sigma_d_stage2=float("nan"),
            objective1=float("nan"),
            objective2=float("nan"),
        )
        return est, {}

    # (3) Gradient direction (unit-like)
    m2 = np.sqrt(gx * gx + gy * gy)
    eps_dir = 1e-12
    inv = 1.0 / np.maximum(m2, eps_dir)
    vx = gx * inv
    vy = gy * inv

    # (4) Gradient magnitude (m)
    mp = np.full((H, W), np.nan, dtype=float)
    if p == 0.0 or p == 2.0:
        mp[finite_g] = m2[finite_g]
    else:
        mp[finite_g] = (np.abs(gx[finite_g]) ** p + np.abs(gy[finite_g]) ** p) ** (1.0 / p)

    # (5) Coherence map (kappa)
    kappa = base._structure_tensor_coherence(gx, gy, sigma=coherence_sigma)
    m_vals = mp[finite_g]
    k_vals = kappa[finite_g]
    if m_vals.size == 0 or k_vals.size == 0:
        est = CenterEstimate(
            (float("nan"), float("nan")),
            ok=False,
            reason="no finite m/k values",
            n_used=0,
            n_total=n_total,
            sigma_d_stage1=float("nan"),
            sigma_d_stage2=float("nan"),
            objective1=float("nan"),
            objective2=float("nan"),
        )
        return est, {}

    # (6) Sigmoid gates for m and k (same heuristic as base script)
    def _sigmoid(x: np.ndarray) -> np.ndarray:
        return _sigmoid_stable(x)

    b_m = float(np.quantile(m_vals, b_m_quantile))
    b_k = float(np.quantile(k_vals, b_k_quantile))

    logit = float(np.log(w_target / (1.0 - w_target)))
    m_hi = float(np.quantile(m_vals, m_hi_quantile))
    k_hi = float(np.quantile(k_vals, k_hi_quantile))
    sigma_m = float((m_hi - b_m) / max(logit, 1e-12))
    sigma_k = float((k_hi - b_k) / max(logit, 1e-12))

    if not np.isfinite(sigma_m) or sigma_m <= 0.0:
        sigma_m = float(np.maximum(np.std(m_vals), 1e-12))
    if not np.isfinite(sigma_k) or sigma_k <= 0.0:
        sigma_k = float(max(np.std(k_vals), 1e-6))

    wm = np.zeros((H, W), dtype=float)
    wk = np.zeros((H, W), dtype=float)
    w = np.zeros((H, W), dtype=float)
    wm[finite_g] = _sigmoid((mp[finite_g] - b_m) / sigma_m)
    wk[finite_g] = _sigmoid((kappa[finite_g] - b_k) / sigma_k)
    w[finite_g] = wm[finite_g] * (wk[finite_g] ** beta_coh)

    # (7) Subsample
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
            sigma_d_stage1=float("nan"),
            sigma_d_stage2=float("nan"),
            objective1=float("nan"),
            objective2=float("nan"),
        )
        return est, {}

    ys, xs = np.nonzero(use)
    vx_s = vx[use]
    vy_s = vy[use]
    w_s = w[use]

    # Avoid all-zero weights
    if not np.isfinite(np.sum(w_s)) or float(np.sum(w_s)) <= 0.0:
        w_s = np.ones_like(w_s, dtype=float)

    # Initial center guess: weighted centroid
    w_sum = float(np.sum(w_s))
    cy0 = float(np.sum(ys.astype(float) * w_s) / w_sum)
    cx0 = float(np.sum(xs.astype(float) * w_s) / w_sum)

    # Stage-1 sigma_d (fixed by image size)
    sigma_d1 = float(np.log1p(float(np.hypot(H, W)) / 5.0))
    if not np.isfinite(sigma_d1) or sigma_d1 <= 0.0:
        sigma_d1 = 1.0

    def objective1(cy_cx: np.ndarray) -> float:
        cy = float(cy_cx[0])
        cx = float(cy_cx[1])
        d = np.abs((cx - xs.astype(float)) * vy_s - (cy - ys.astype(float)) * vx_s)
        r = np.log1p(np.asarray(d, dtype=float))
        hub = base._huber_rho(r, sigma_d1)
        return float(np.mean(w_s * hub))

    cy_start, cx_start = cy0, cx0
    if stage1_multistart_radius_px and stage1_multistart_radius_px > 0.0:
        r = float(stage1_multistart_radius_px)
        step = max(1.0, float(stage1_multistart_step_px))
        offs = np.arange(-r, r + 1e-9, step, dtype=float)
        best_val = float("inf")
        best = (cy0, cx0)
        for dy in offs:
            for dx in offs:
                cy_cand = cy0 + dy
                cx_cand = cx0 + dx
                v = objective1(np.asarray([cy_cand, cx_cand], dtype=float))
                if np.isfinite(v) and v < best_val:
                    best_val = v
                    best = (cy_cand, cx_cand)
        cy_start, cx_start = float(best[0]), float(best[1])

    res1 = minimize(
        objective1,
        x0=np.asarray([cy_start, cx_start], dtype=float),
        method="Powell",
        options={"maxiter": int(maxiter), "xtol": 1e-3, "ftol": 1e-6, "disp": False},
    )

    cy_guess = float(res1.x[0]) if np.all(np.isfinite(res1.x)) and res1.x.shape == (2,) else float("nan")
    cx_guess = float(res1.x[1]) if np.all(np.isfinite(res1.x)) and res1.x.shape == (2,) else float("nan")
    obj1 = float(res1.fun) if np.isfinite(res1.fun) else float("nan")

    if not np.isfinite(cy_guess) or not np.isfinite(cx_guess):
        est = CenterEstimate(
            (float("nan"), float("nan")),
            ok=False,
            reason="stage1 optimization failed",
            n_used=n_used,
            n_total=n_total,
            sigma_d_stage1=sigma_d1,
            sigma_d_stage2=float("nan"),
            objective1=obj1,
            objective2=float("nan"),
        )
        return est, {}

    # ---- Stage-2: elite-majority refinement with elite-consistent sigma ----
    # Compute elite mask by weight quantile (weights are already sigmoid-gated).
    w_thresh = float(np.quantile(w_s, elite_weight_quantile))
    elite_mask = np.isfinite(w_s) & (w_s >= w_thresh)
    if int(np.sum(elite_mask)) < 50:
        # If elite selection is too small, fall back to all points.
        elite_mask = np.ones_like(w_s, dtype=bool)

    d_guess = np.abs(
        (cx_guess - xs.astype(float)) * vy_s - (cy_guess - ys.astype(float)) * vx_s
    )
    finite_d = np.isfinite(d_guess)
    elite_mask = elite_mask & finite_d
    if int(np.sum(elite_mask)) > 0:
        d_guess_elite = d_guess[elite_mask]
    else:
        d_guess_elite = d_guess[finite_d]

    q_ref = float(np.quantile(d_guess_elite, refine_d_quantile))
    logit_09 = float(np.log(0.9 / 0.1))
    sigma_d2 = float(q_ref / logit_09) if np.isfinite(q_ref) and q_ref > 0.0 else sigma_d1
    if not np.isfinite(sigma_d2) or sigma_d2 <= 0.0:
        sigma_d2 = sigma_d1

    def objective2(cy_cx: np.ndarray) -> float:
        cy = float(cy_cx[0])
        cx = float(cy_cx[1])
        d = np.abs((cx - xs.astype(float)) * vy_s - (cy - ys.astype(float)) * vx_s)
        z = d / float(sigma_d2)
        s = _sigmoid_stable(z)
        elite_w = w_s[elite_mask]
        elite_s = s[elite_mask]
        # Optional angular penalty: gradient direction should align with the radial direction
        # from the candidate center to the pixel.
        if angular_lambda != 0.0:
            dx = xs.astype(float)[elite_mask] - cx
            dy = ys.astype(float)[elite_mask] - cy
            r = np.hypot(dx, dy) + 1e-12
            ux = dx / r
            uy = dy / r
            dot = vx_s[elite_mask] * ux + vy_s[elite_mask] * uy
            ang_pen = 1.0 - np.clip(dot * dot, 0.0, 1.0)  # 0 when parallel, up to 1 when orthogonal
        else:
            ang_pen = 0.0
        base_term = elite_s - 0.5
        total = base_term + float(angular_lambda) * ang_pen
        return float(np.sum(elite_w * total) / max(float(np.sum(elite_w)), 1e-12))

    cy_start2, cx_start2 = cy_guess, cx_guess
    if stage2_multistart_radius_px and stage2_multistart_radius_px > 0.0:
        r2 = float(stage2_multistart_radius_px)
        step2 = max(1.0, float(stage2_multistart_step_px))
        offs2 = np.arange(-r2, r2 + 1e-9, step2, dtype=float)
        best_val2 = float("inf")
        best2 = (cy_guess, cx_guess)
        for dy in offs2:
            for dx in offs2:
                cy_cand = cy_guess + dy
                cx_cand = cx_guess + dx
                v2 = objective2(np.asarray([cy_cand, cx_cand], dtype=float))
                if np.isfinite(v2) and v2 < best_val2:
                    best_val2 = v2
                    best2 = (cy_cand, cx_cand)
        cy_start2, cx_start2 = float(best2[0]), float(best2[1])

    res2 = minimize(
        objective2,
        x0=np.asarray([cy_start2, cx_start2], dtype=float),
        method="Powell",
        options={"maxiter": int(refine_maxiter), "xtol": 1e-3, "ftol": 1e-6, "disp": False},
    )

    cy_ref = float(res2.x[0]) if np.all(np.isfinite(res2.x)) and res2.x.shape == (2,) else float("nan")
    cx_ref = float(res2.x[1]) if np.all(np.isfinite(res2.x)) and res2.x.shape == (2,) else float("nan")
    obj2 = float(res2.fun) if np.isfinite(res2.fun) else float("nan")

    ok2 = bool(res2.success) and np.isfinite(cy_ref) and np.isfinite(cx_ref)
    reason2 = "ok" if ok2 else "refinement failed/non-finite"

    est = CenterEstimate(
        (cy_ref, cx_ref),
        ok=ok2,
        reason=reason2,
        n_used=n_used,
        n_total=n_total,
        sigma_d_stage1=sigma_d1,
        sigma_d_stage2=sigma_d2,
        objective1=obj1,
        objective2=obj2,
    )

    dbg = {
        "w_thresh": w_thresh,
        "elite_count": float(np.sum(elite_mask)),
        "q_ref": q_ref,
        "sigma_d2": sigma_d2,
        "sigma_d1": sigma_d1,
        "center_yx_stage1": float(cy_guess),
        "center_x_stage1": float(cx_guess),
        "stage1_start_yx": float(cy_start),
        "stage1_start_x": float(cx_start),
        "stage2_start_yx": float(cy_start2),
        "stage2_start_x": float(cx_start2),
        "b_m": b_m,
        "b_k": b_k,
        "sigma_m": sigma_m,
        "sigma_k": sigma_k,
    }
    return est, dbg


def main() -> int:
    parser = argparse.ArgumentParser(description="Elite-consistent refinement variant for center detection (gradient-line).")
    parser.add_argument("--image", type=str, required=True, help="Path to input .tif")
    parser.add_argument("--config-path", type=str, default="", help="Optional autocalib_ring_analysis config.conf for comparison.")
    parser.add_argument("--out-dir", type=str, default="debug_center_grad_ls_v0_elite_refine", help="Output directory (plots only if implemented).")

    parser.add_argument("--mean-filter-size", type=int, default=25)
    parser.add_argument("--stride", type=int, default=2)
    parser.add_argument("--p", type=float, default=2.0)
    parser.add_argument("--coherence-sigma", type=float, default=3.0)
    parser.add_argument("--beta-coh", type=float, default=2.0)

    parser.add_argument("--b-m-quantile", type=float, default=0.80, help="Quantile for b_m")
    parser.add_argument("--b-k-quantile", type=float, default=0.50, help="Quantile for b_k")
    parser.add_argument("--m-hi-quantile", type=float, default=0.95, help="Quantile for m_hi (used for sigma_m)")
    parser.add_argument("--k-hi-quantile", type=float, default=0.75, help="Quantile for k_hi (used for sigma_k)")
    parser.add_argument("--w-target", type=float, default=0.90, help="Weight target at the high-quantile point")

    parser.add_argument("--maxiter", type=int, default=60)
    parser.add_argument("--refine-maxiter", type=int, default=40)
    parser.add_argument("--refine-d-quantile", type=float, default=0.9, help="Quantile of d within elite used to set sigma_d2.")
    parser.add_argument("--elite-weight-quantile", type=float, default=0.5, help="Weight quantile selecting elite subset (e.g. 0.7).")
    parser.add_argument("--huber-k", type=float, default=1.345)
    parser.add_argument("--stage1-multistart-radius-px", type=float, default=0.0, help="If >0, try stage1 starts on a grid around the centroid.")
    parser.add_argument("--stage1-multistart-step-px", type=float, default=20.0, help="Step size in px for stage1 multistart grid.")
    parser.add_argument("--stage2-multistart-radius-px", type=float, default=0.0, help="If >0, try stage2 starts on a grid around the stage1 solution.")
    parser.add_argument("--stage2-multistart-step-px", type=float, default=5.0, help="Step size in px for stage2 multistart grid.")
    parser.add_argument("--angular-lambda", type=float, default=0.0, help="Stage-2 angular penalty weight (0 disables).")

    args = parser.parse_args()

    img_path = Path(args.image)
    if not img_path.is_file():
        raise FileNotFoundError(str(img_path))

    cfg = None
    ring_center = None
    if args.config_path:
        cfg = load_config(args.config_path)
        ring_res = autocalib_ring_analysis(
            str(img_path),
            cfg,
            plots_out_dir=WORKSPACE_ROOT / "debug" / "tmp_ring_elite_refine",
            plot_stem=img_path.stem,
            calibration_curve_plot_path=WORKSPACE_ROOT / "debug" / "tmp_ring_elite_refine" / f"{img_path.stem}_ring_curve.png",
            mask_path=None,
        )
        ring_center = (float(ring_res.get("center_y_px")), float(ring_res.get("center_x_px")))

    img = read_from_tiff(img_path)
    est, dbg = estimate_center_grad_ls_v0_elite_refine(
        img,
        mean_filter_size=args.mean_filter_size,
        stride=args.stride,
        p=args.p,
        coherence_sigma=args.coherence_sigma,
        beta_coh=args.beta_coh,
        b_m_quantile=float(args.b_m_quantile),
        b_k_quantile=float(args.b_k_quantile),
        m_hi_quantile=float(args.m_hi_quantile),
        k_hi_quantile=float(args.k_hi_quantile),
        w_target=float(args.w_target),
        refine_d_quantile=args.refine_d_quantile,
        elite_weight_quantile=args.elite_weight_quantile,
        maxiter=args.maxiter,
        refine_maxiter=args.refine_maxiter,
        huber_k=args.huber_k,
        angular_lambda=float(args.angular_lambda),
        stage1_multistart_radius_px=float(args.stage1_multistart_radius_px),
        stage1_multistart_step_px=float(args.stage1_multistart_step_px),
        stage2_multistart_radius_px=float(args.stage2_multistart_radius_px),
        stage2_multistart_step_px=float(args.stage2_multistart_step_px),
    )

    cy, cx = map(float, est.center_yx)
    print(f"image={img_path.name} ok={est.ok} center_yx=({cy:.2f},{cx:.2f}) reason={est.reason}")
    print(
        f"stage1(sigma_d1)={dbg.get('sigma_d1', np.nan):.3g} "
        f"stage2(sigma_d2)={dbg.get('sigma_d2', np.nan):.3g} "
        f"q_ref={dbg.get('q_ref', np.nan):.3g} "
        f"elite_w_thresh={dbg.get('w_thresh', np.nan):.3g} elite_count={int(dbg.get('elite_count', 0.0))} "
        f"stage1_center=({dbg.get('center_yx_stage1', np.nan):.2f},{dbg.get('center_x_stage1', np.nan):.2f})"
    )

    if ring_center is not None:
        cy_r, cx_r = ring_center
        print(f"ring_center=({cy_r:.2f},{cx_r:.2f}) err_dx={cx-cx_r:.2f} err_dy={cy-cy_r:.2f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

