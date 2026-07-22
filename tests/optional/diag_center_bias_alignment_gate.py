"""
Diagnose systematic center bias on a single image by testing a simple hypothesis:

  H1: Non-ring gradients (or asymmetric ring coverage) still influence the objective.
      If we gate points by *radial alignment* w.r.t. a candidate center c0,
      the objective should become less biased.

This script does NOT modify any existing implementation. It recomputes the
stage-1 objective pieces from `tests/optional/test_center_grad_ls_v0.py` and
evaluates:
  - objective at ring center vs stage1 center
  - objective with an alignment gate a = |v·u(c0)| >= tau
  - simple left/right imbalance metrics on the gated, high-weight set

Run:
  /home/mikl/.conda/envs/LLMAssistant/bin/python tests/optional/diag_center_bias_alignment_gate.py \
    --image data/calib_benchmark/0000_AgBh200_135.6.tif \
    --config-path data/calib_benchmark_200_to_500/config.conf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Tuple

import numpy as np

WORKSPACE_ROOT = Path("/home/mikl/KurchatovCoop")
sys.path.insert(0, str(WORKSPACE_ROOT / "autosaxs" / "src"))

from autosaxs.core.utils import load_config, read_from_tiff  # type: ignore
from autosaxs.skill.calibrate.autocalib import autocalib_ring_analysis, _log1p_image  # type: ignore
from tests.optional import test_center_grad_ls_v0 as base  # type: ignore


def _recompute_stage1_arrays(img: np.ndarray, *, mean_filter_size: int, coherence_sigma: float, stride: int):
    img = np.asarray(img, dtype=float)
    H, W = img.shape
    J = _log1p_image(img)
    J_smooth = base._nanmean_box_filter(J, size=mean_filter_size)
    gx, gy = base._nan_safe_central_grad(J_smooth)
    finite_g = np.isfinite(gx) & np.isfinite(gy)

    m = np.sqrt(gx * gx + gy * gy)
    eps_dir = 1e-12
    inv = 1.0 / np.maximum(m, eps_dir)
    vx = gx * inv
    vy = gy * inv

    kappa = base._structure_tensor_coherence(gx, gy, sigma=coherence_sigma)
    m_vals = m[finite_g]
    k_vals = kappa[finite_g]

    def sigmoid(x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=float)
        x = np.clip(x, -50.0, 50.0)
        return 1.0 / (1.0 + np.exp(-x))

    b_m = float(np.quantile(m_vals, 0.80))
    b_k = float(np.quantile(k_vals, 0.50))
    w_target = 0.90
    logit = float(np.log(w_target / (1.0 - w_target)))
    m_hi = float(np.quantile(m_vals, 0.95))
    k_hi = float(np.quantile(k_vals, 0.75))
    sigma_m = float((m_hi - b_m) / max(logit, 1e-12))
    sigma_k = float((k_hi - b_k) / max(logit, 1e-12))

    if not np.isfinite(sigma_m) or sigma_m <= 0.0:
        sigma_m = float(np.maximum(np.std(m_vals), 1e-12))
    if not np.isfinite(sigma_k) or sigma_k <= 0.0:
        sigma_k = float(max(np.std(k_vals), 1e-6))

    w = np.zeros((H, W), dtype=float)
    w[finite_g] = sigmoid((m[finite_g] - b_m) / sigma_m) * sigmoid((kappa[finite_g] - b_k) / sigma_k)

    grid = np.zeros_like(finite_g, dtype=bool)
    grid[::stride, ::stride] = True
    use = finite_g & grid

    ys, xs = np.nonzero(use)
    vx_s = vx[use]
    vy_s = vy[use]
    w_s = w[use]

    sigma_d = float(np.log1p(float(np.hypot(H, W)) / 5.0))
    if not np.isfinite(sigma_d) or sigma_d <= 0.0:
        sigma_d = 1.0

    return (H, W), (ys.astype(float), xs.astype(float)), (vx_s, vy_s), w_s, sigma_d


def _objective_huber_log1p(
    *,
    cy: float,
    cx: float,
    ys: np.ndarray,
    xs: np.ndarray,
    vx_s: np.ndarray,
    vy_s: np.ndarray,
    w_s: np.ndarray,
    sigma_d: float,
    gate_mask: np.ndarray | None = None,
) -> float:
    d = np.abs((cx - xs) * vy_s - (cy - ys) * vx_s)
    r = np.log1p(d)
    hub = base._huber_rho(r, sigma_d)
    if gate_mask is not None:
        hub = hub[gate_mask]
        wv = w_s[gate_mask]
    else:
        wv = w_s
    return float(np.mean(wv * hub))


def _alignment_gate(
    *,
    cy0: float,
    cx0: float,
    ys: np.ndarray,
    xs: np.ndarray,
    vx_s: np.ndarray,
    vy_s: np.ndarray,
    tau: float,
) -> np.ndarray:
    dx = xs - cx0
    dy = ys - cy0
    rr = np.hypot(dx, dy) + 1e-12
    ux = dx / rr
    uy = dy / rr
    dot = vx_s * ux + vy_s * uy
    a = np.abs(dot)
    return a >= float(tau)


def _left_right_stats(*, cy: float, cx: float, ys: np.ndarray, xs: np.ndarray, vx_s: np.ndarray, vy_s: np.ndarray, w_s: np.ndarray, mask: np.ndarray):
    signed = (cx - xs) * vy_s - (cy - ys) * vx_s  # signed cross residual
    hi = mask
    left = hi & (xs < cx)
    right = hi & (xs >= cx)
    def stats(m):
        if int(np.sum(m)) == 0:
            return (0, float("nan"), float("nan"), float("nan"))
        s = signed[m]
        return (int(np.sum(m)), float(np.mean(s)), float(np.median(s)), float(np.mean(np.abs(s))))
    return {"left": stats(left), "right": stats(right)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnose center bias via radial-alignment gating.")
    ap.add_argument("--image", type=str, required=True)
    ap.add_argument("--config-path", type=str, required=True)
    ap.add_argument("--mean-filter-size", type=int, default=25)
    ap.add_argument("--coherence-sigma", type=float, default=3.0)
    ap.add_argument("--stride", type=int, default=2)
    ap.add_argument("--taus", type=str, default="0.97,0.98,0.985,0.99,0.995")
    args = ap.parse_args()

    img_path = WORKSPACE_ROOT / args.image
    cfg_path = WORKSPACE_ROOT / args.config_path

    cfg = load_config(str(cfg_path))
    ring = autocalib_ring_analysis(
        str(img_path),
        cfg,
        plots_out_dir=WORKSPACE_ROOT / "debug" / "tmp_bias_diag_align",
        plot_stem=Path(args.image).stem,
        calibration_curve_plot_path=WORKSPACE_ROOT / "debug" / "tmp_bias_diag_align" / f"{Path(args.image).stem}_ring_curve.png",
        mask_path=None,
    )
    cy_r = float(ring["center_y_px"])
    cx_r = float(ring["center_x_px"])

    img = read_from_tiff(img_path)
    _shape, (ys, xs), (vx_s, vy_s), w_s, sigma_d = _recompute_stage1_arrays(
        img,
        mean_filter_size=int(args.mean_filter_size),
        coherence_sigma=float(args.coherence_sigma),
        stride=int(args.stride),
    )

    # Stage-1 estimate from the original implementation (no refine).
    est1, _dbg1 = base.estimate_center_grad_line_robust_v0(
        img,
        mean_filter_size=int(args.mean_filter_size),
        coherence_sigma=float(args.coherence_sigma),
        stride=int(args.stride),
        refine=False,
        maxiter=60,
    )
    cy1, cx1 = map(float, est1.center_yx)

    print("ring_center_yx", (cy_r, cx_r))
    print("stage1_center_yx", (cy1, cx1), "err(dy,dx)", (cy1 - cy_r, cx1 - cx_r))

    base_ring = _objective_huber_log1p(
        cy=cy_r, cx=cx_r, ys=ys, xs=xs, vx_s=vx_s, vy_s=vy_s, w_s=w_s, sigma_d=sigma_d
    )
    base_stage1 = _objective_huber_log1p(
        cy=cy1, cx=cx1, ys=ys, xs=xs, vx_s=vx_s, vy_s=vy_s, w_s=w_s, sigma_d=sigma_d
    )
    print("obj_base ring", base_ring, "stage1", base_stage1)

    # Gate by alignment wrt stage1 center and re-evaluate objective at both centers.
    taus = [float(t.strip()) for t in args.taus.split(",") if t.strip()]
    for tau in taus:
        gate = _alignment_gate(cy0=cy1, cx0=cx1, ys=ys, xs=xs, vx_s=vx_s, vy_s=vy_s, tau=tau)
        n = int(np.sum(gate))
        if n < 200:
            print(f"tau={tau:.3f} gate_n={n} (too few), skip")
            continue
        obj_ring = _objective_huber_log1p(
            cy=cy_r, cx=cx_r, ys=ys, xs=xs, vx_s=vx_s, vy_s=vy_s, w_s=w_s, sigma_d=sigma_d, gate_mask=gate
        )
        obj_stage1 = _objective_huber_log1p(
            cy=cy1, cx=cx1, ys=ys, xs=xs, vx_s=vx_s, vy_s=vy_s, w_s=w_s, sigma_d=sigma_d, gate_mask=gate
        )

        # Left/right imbalance on high-weight subset within gate.
        wh = float(np.quantile(w_s[np.isfinite(w_s)], 0.8))
        hi = gate & np.isfinite(w_s) & (w_s >= wh)
        lr = _left_right_stats(cy=cy1, cx=cx1, ys=ys, xs=xs, vx_s=vx_s, vy_s=vy_s, w_s=w_s, mask=hi)
        print(
            f"tau={tau:.3f} gate_n={n} obj_ring={obj_ring:.6g} obj_stage1={obj_stage1:.6g} "
            f"LR_left(n,mean,med,meanabs)={lr['left']} LR_right(n,mean,med,meanabs)={lr['right']}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

