"""
Test-copy variant of the gradient-line beam-center estimator.

Goal:
  Keep the current algorithm shape, but test a simpler hypothesis about weights:

    Instead of sigmoid((m-b)/s) * sigmoid((k-b)/s) (which is ~0.25 at m=k=b),
    use "soft-quantized" positive weights:

      w = relu(sigmoid((m-b_m)/sigma_m) - 0.5) * relu(sigmoid((k-b_k)/sigma_k) - 0.5)

  This makes weights ~0 for sub-threshold values and avoids giving baseline 0.5
  to strictly-positive m/k values.

This file is intentionally a separate test copy so we don't lose the current
almost-hit-it implementation.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import scipy.ndimage as ndi
from scipy.optimize import minimize

WORKSPACE_ROOT = Path("/home/mikl/KurchatovCoop")
REPOS_DIR = WORKSPACE_ROOT / "repos"
sys.path.insert(0, str(REPOS_DIR))

from autosaxs.core.utils import read_from_tiff, load_config  # type: ignore
from autosaxs.skill.calibrate.autocalib import autocalib_ring_analysis  # type: ignore
from autosaxs.skill.calibrate.autocalib import _log1p_image  # type: ignore


@dataclass(frozen=True)
class CenterEstimate:
    center_yx: Tuple[float, float]
    ok: bool
    reason: str
    n_used: int
    n_total: int
    objective: float


def _nan_safe_central_grad(J: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    J = np.asarray(J, dtype=float)
    H, W = J.shape
    gx = np.full((H, W), np.nan, dtype=float)
    gy = np.full((H, W), np.nan, dtype=float)

    left = J[:, :-2]
    right = J[:, 2:]
    ok_x = np.isfinite(left) & np.isfinite(right)
    gx[:, 1:-1][ok_x] = 0.5 * (right[ok_x] - left[ok_x])

    up = J[:-2, :]
    down = J[2:, :]
    ok_y = np.isfinite(up) & np.isfinite(down)
    gy[1:-1, :][ok_y] = 0.5 * (down[ok_y] - up[ok_y])
    return gx, gy


def _nanmean_box_filter(J: np.ndarray, size: int = 25) -> np.ndarray:
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


def _huber_rho(z: np.ndarray, k: float) -> np.ndarray:
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
    kappa = num / (Sxx + Syy + eps)
    kappa[~np.isfinite(kappa)] = 0.0
    return np.clip(kappa, 0.0, 1.0)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    x = np.clip(x, -50.0, 50.0)
    return 1.0 / (1.0 + np.exp(-x))


def _relu(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    return np.maximum(x, 0.0)


def estimate_center_softquant(
    img_raw: np.ndarray,
    *,
    mean_filter_size: int = 25,
    coherence_sigma: float = 3.0,
    stride: int = 2,
    b_m_quantile: float = 0.80,
    b_k_quantile: float = 0.50,
    w_target: float = 0.90,
    m_hi_quantile: float = 0.95,
    k_hi_quantile: float = 0.75,
    huber_sigma_d: Optional[float] = None,
    maxiter: int = 60,
    eps_dir: float = 1e-12,
) -> Tuple[CenterEstimate, Dict[str, np.ndarray]]:
    img_raw = np.asarray(img_raw, dtype=float)
    H, W = img_raw.shape

    J = _log1p_image(img_raw)
    J_smooth = _nanmean_box_filter(J, size=mean_filter_size)
    gx, gy = _nan_safe_central_grad(J_smooth)
    finite_g = np.isfinite(gx) & np.isfinite(gy)
    n_total = int(np.sum(finite_g))
    if n_total == 0:
        est = CenterEstimate((float("nan"), float("nan")), False, "no gradients", 0, 0, float("nan"))
        return est, {"J": J, "J_smooth": J_smooth, "gx": gx, "gy": gy}

    m = np.sqrt(gx * gx + gy * gy)
    inv = 1.0 / np.maximum(m, eps_dir)
    vx = gx * inv
    vy = gy * inv

    kappa = _structure_tensor_coherence(gx, gy, sigma=coherence_sigma)
    m_vals = m[finite_g]
    k_vals = kappa[finite_g]

    b_m = float(np.quantile(m_vals, b_m_quantile))
    b_k = float(np.quantile(k_vals, b_k_quantile))
    logit = float(np.log(w_target / (1.0 - w_target)))
    m_hi = float(np.quantile(m_vals, m_hi_quantile))
    k_hi = float(np.quantile(k_vals, k_hi_quantile))
    sigma_m = float((m_hi - b_m) / max(logit, 1e-12))
    sigma_k = float((k_hi - b_k) / max(logit, 1e-12))
    if not np.isfinite(sigma_m) or sigma_m <= 0:
        sigma_m = float(np.maximum(np.std(m_vals), 1e-12))
    if not np.isfinite(sigma_k) or sigma_k <= 0:
        sigma_k = float(max(np.std(k_vals), 1e-6))

    # Soft-quantized weights.
    wm = _relu(_sigmoid((m - b_m) / sigma_m) - 0.5)
    wk = _relu(_sigmoid((kappa - b_k) / sigma_k) - 0.5)
    w = np.zeros((H, W), dtype=float)
    w[finite_g] = (wm[finite_g] * wk[finite_g])

    # Subsample for objective.
    grid = np.zeros_like(finite_g, dtype=bool)
    grid[::stride, ::stride] = True
    use = finite_g & grid
    n_used = int(np.sum(use))
    if n_used < 200:
        est = CenterEstimate((float("nan"), float("nan")), False, "too few points", n_used, n_total, float("nan"))
        dbg = {"J": J, "J_smooth": J_smooth, "gx": gx, "gy": gy, "m": m, "kappa": kappa, "w": w}
        return est, dbg

    ys, xs = np.nonzero(use)
    vx_s = vx[use]
    vy_s = vy[use]
    w_s = w[use]
    if not np.isfinite(np.sum(w_s)) or float(np.sum(w_s)) <= 0.0:
        w_s = np.ones_like(w_s, dtype=float)

    # Init guess: weighted centroid
    w_sum = float(np.sum(w_s))
    cy0 = float(np.sum(ys.astype(float) * w_s) / w_sum)
    cx0 = float(np.sum(xs.astype(float) * w_s) / w_sum)

    sigma_d = (
        float(huber_sigma_d)
        if huber_sigma_d is not None
        else float(np.log1p(float(np.hypot(H, W)) / 5.0))
    )
    if not np.isfinite(sigma_d) or sigma_d <= 0.0:
        sigma_d = 1.0

    def objective(cy_cx: np.ndarray) -> float:
        cy = float(cy_cx[0])
        cx = float(cy_cx[1])
        d = np.abs((cx - xs.astype(float)) * vy_s - (cy - ys.astype(float)) * vx_s)
        r = np.log1p(d)
        hub = _huber_rho(r, sigma_d)
        return float(np.mean(w_s * hub))

    res = minimize(
        objective,
        x0=np.asarray([cy0, cx0], dtype=float),
        method="Powell",
        options={"maxiter": int(maxiter), "xtol": 1e-3, "ftol": 1e-6, "disp": False},
    )
    cy = float(res.x[0]) if np.all(np.isfinite(res.x)) and res.x.shape == (2,) else float("nan")
    cx = float(res.x[1]) if np.all(np.isfinite(res.x)) and res.x.shape == (2,) else float("nan")
    ok = bool(res.success) and np.isfinite(cy) and np.isfinite(cx)
    est = CenterEstimate((cy, cx), ok, "ok" if ok else "failed", n_used, n_total, float(res.fun) if np.isfinite(res.fun) else float("nan"))

    # Full-res distance map for plotting.
    yy, xx = np.indices((H, W), dtype=float)
    d_map = np.abs((cx - xx) * np.where(np.isfinite(vy), vy, 0.0) - (cy - yy) * np.where(np.isfinite(vx), vx, 0.0))
    d_map[~finite_g] = np.nan

    dbg = {
        "J": J,
        "J_smooth": J_smooth,
        "gx": gx,
        "gy": gy,
        "m": m,
        "kappa": kappa,
        "w": w,
        "d": d_map,
        "bm": np.asarray([b_m], dtype=float),
        "bk": np.asarray([b_k], dtype=float),
        "sigma_m": np.asarray([sigma_m], dtype=float),
        "sigma_k": np.asarray([sigma_k], dtype=float),
        "sigma_d": np.asarray([sigma_d], dtype=float),
        "objective": np.asarray([float(res.fun) if np.isfinite(res.fun) else np.nan], dtype=float),
        "ys": ys.astype(float),
        "xs": xs.astype(float),
        "vx_s": vx_s.astype(float),
        "vy_s": vy_s.astype(float),
        "w_s": w_s.astype(float),
    }
    return est, dbg


def _save_plot(img_raw: np.ndarray, est: CenterEstimate, dbg: Dict[str, np.ndarray], *, out_path: Path) -> None:
    J0 = _log1p_image(img_raw)
    kappa = dbg["kappa"]
    w = dbg["w"]
    m = dbg["m"]
    d = dbg["d"]
    cy, cx = est.center_yx

    fig, axs = plt.subplots(2, 3, figsize=(16, 11))
    axs = np.asarray(axs).reshape(2, 3)
    fig.suptitle(f"{out_path.stem} ok={est.ok} center=({cy:.1f},{cx:.1f})", fontsize=12)

    axs[0, 0].imshow(J0, origin="lower", cmap="viridis")
    axs[0, 0].set_title("original log1p")

    im1 = axs[0, 1].imshow(kappa, origin="lower", cmap="magma")
    axs[0, 1].set_title("coherence")
    fig.colorbar(im1, ax=axs[0, 1], fraction=0.046, pad=0.04)

    im2 = axs[0, 2].imshow(w, origin="lower", cmap="viridis")
    axs[0, 2].set_title("w = relu(sigmoid-0.5)^2")
    fig.colorbar(im2, ax=axs[0, 2], fraction=0.046, pad=0.04)

    im3 = axs[1, 0].imshow(np.log1p(np.maximum(m, 0.0)), origin="lower", cmap="magma")
    axs[1, 0].set_title("grad magnitude log1p")
    fig.colorbar(im3, ax=axs[1, 0], fraction=0.046, pad=0.04)

    im4 = axs[1, 1].imshow(np.log1p(np.maximum(d, 0.0)), origin="lower", cmap="magma")
    axs[1, 1].set_title("distance map log1p")
    fig.colorbar(im4, ax=axs[1, 1], fraction=0.046, pad=0.04)

    axs[1, 2].axis("off")

    if np.isfinite(cy) and np.isfinite(cx):
        for ax in (axs[0, 0], axs[0, 1], axs[0, 2], axs[1, 0], axs[1, 1]):
            ax.plot([cx], [cy], "r+", markersize=12, markeredgewidth=3)
            ax.axhline(cy, color="r", lw=1, alpha=0.25)
            ax.axvline(cx, color="r", lw=1, alpha=0.25)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def main() -> int:
    p = argparse.ArgumentParser(description="Soft-quantized weight test for center detection.")
    p.add_argument("--image", type=str, required=True)
    p.add_argument("--out-dir", type=str, default="debug_center_grad_ls_v0_softquant")
    p.add_argument("--mean-filter-size", type=int, default=25)
    p.add_argument("--coherence-sigma", type=float, default=3.0)
    p.add_argument("--stride", type=int, default=2)
    p.add_argument("--maxiter", type=int, default=60)
    p.add_argument("--b-m-quantile", type=float, default=0.80)
    p.add_argument("--b-k-quantile", type=float, default=0.50)
    p.add_argument("--w-target", type=float, default=0.90)
    p.add_argument("--m-hi-quantile", type=float, default=0.95)
    p.add_argument("--k-hi-quantile", type=float, default=0.75)
    p.add_argument("--config-path", type=str, default="", help="Optional ring-analysis config to print reference center.")
    args = p.parse_args()

    img_path = Path(args.image)
    img = read_from_tiff(img_path)
    est, dbg = estimate_center_softquant(
        img,
        mean_filter_size=args.mean_filter_size,
        coherence_sigma=args.coherence_sigma,
        stride=args.stride,
        maxiter=args.maxiter,
        b_m_quantile=float(args.b_m_quantile),
        b_k_quantile=float(args.b_k_quantile),
        w_target=float(args.w_target),
        m_hi_quantile=float(args.m_hi_quantile),
        k_hi_quantile=float(args.k_hi_quantile),
    )

    cy, cx = map(float, est.center_yx)
    obj = float(np.asarray(dbg.get("objective", np.nan)))
    print(
        f"{img_path.name}: ok={est.ok} center_yx=({cy:.2f},{cx:.2f}) obj={obj:.6g} "
        f"sigma_d={float(dbg['sigma_d']):.3g} bm={float(dbg['bm']):.3g} bk={float(dbg['bk']):.3g} "
        f"b_m_q={args.b_m_quantile:g} b_k_q={args.b_k_quantile:g} w_target={args.w_target:g}"
    )

    if args.config_path:
        cfg = load_config(args.config_path)
        ring = autocalib_ring_analysis(
            str(img_path),
            cfg,
            plots_out_dir=WORKSPACE_ROOT / "debug" / "tmp_ring_softquant",
            plot_stem=img_path.stem,
            calibration_curve_plot_path=WORKSPACE_ROOT / "debug" / "tmp_ring_softquant" / f"{img_path.stem}_ring_curve.png",
            mask_path=None,
        )
        cy_r = float(ring["center_y_px"])
        cx_r = float(ring["center_x_px"])
        ys = np.asarray(dbg["ys"], dtype=float)
        xs = np.asarray(dbg["xs"], dtype=float)
        vx_s = np.asarray(dbg["vx_s"], dtype=float)
        vy_s = np.asarray(dbg["vy_s"], dtype=float)
        w_s = np.asarray(dbg["w_s"], dtype=float)
        sigma_d = float(np.asarray(dbg["sigma_d"]))
        d = np.abs((cx_r - xs) * vy_s - (cy_r - ys) * vx_s)
        r = np.log1p(d)
        hub = _huber_rho(r, sigma_d)
        obj_ring = float(np.mean(w_s * hub))
        print(
            f"ring_center_yx=({cy_r:.2f},{cx_r:.2f}) err_dy={cy-cy_r:.2f} err_dx={cx-cx_r:.2f} obj_at_ring={obj_ring:.6g}"
        )

    out_dir = WORKSPACE_ROOT / "debug" / args.out_dir
    out_path = out_dir / f"{img_path.stem}_softquant.png"
    _save_plot(img, est, dbg, out_path=out_path)
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

