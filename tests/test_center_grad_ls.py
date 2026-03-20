"""
Debug/test script: estimate SAXS beam center from raw image gradients.

Goal: provide a simple way to test a new center estimator that does NOT rely on
global quantiles / explicit ring detection.

Algorithm (high level):
- Work in log-intensity domain: J = log1p(I + offset) over finite pixels.
- Compute NaN-safe central-difference gradients (gx, gy).
- Robustify by FILTERING (not capping) extreme gradient magnitudes using an
  IQR-derived upper bound; those pixels contribute zero weight.
- Accumulate "votes" for candidate centers along gradient rays; extract center
  from the vote map via top-quantile weighted centroid (more stable than argmax
  when the peak is broad/flat).

The script uses the same data directory as repos/tests/test.ipynb and saves
similar matplotlib visualizations as PNGs.

Run:
  /home/mikl/.conda/envs/LLMAssistant/bin/python repos/tests/test_center_grad_ls.py
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import scipy.ndimage as ndi


WORKSPACE_ROOT = Path("/home/mikl/KurchatovCoop")
REPOS_DIR = WORKSPACE_ROOT / "repos"

if str(REPOS_DIR) not in sys.path:
    sys.path.insert(0, str(REPOS_DIR))

from autosaxs.utils import read_from_tiff  # noqa: E402


@dataclass(frozen=True)
class CenterEstimate:
    center_yx: Tuple[float, float]
    ok: bool
    reason: str
    n_used: int
    n_total: int


def _log1p_image(img: np.ndarray) -> np.ndarray:
    """Log transform over finite values; keep NaNs as NaNs."""
    img = np.asarray(img, dtype=float)
    finite = np.isfinite(img)
    if not np.any(finite):
        return np.full_like(img, np.nan, dtype=float)
    # If data contains negative values, shift so log1p is defined everywhere finite.
    # vmin = float(np.min(img[finite]))
    # offset = max(0.0, -vmin)
    offset = 0.0
    out = np.full_like(img, np.nan, dtype=float)
    out[finite] = np.log1p(img[finite] + offset)
    return out


def _nan_safe_central_grad(J: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """
    Central differences, computed only where the needed neighbors are finite.
    This handles horizontal/vertical NaN stripes without needing a mask.
    """
    J = np.asarray(J, dtype=float)
    H, W = J.shape
    gx = np.full((H, W), np.nan, dtype=float)
    gy = np.full((H, W), np.nan, dtype=float)

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
    NaN-aware mean (box) filter of given odd/even size.

    Computes: mean of finite values within the window. If a window has no finite
    values, output is NaN.
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
    # Requirement: preserve original NaN pixels as NaN after filtering.
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

def estimate_center_grad_vote(
    img_raw: np.ndarray,
    *,
    iqr_k: float = 1.5,
    p: float = 2.0,
    t_min: float = 20.0,
    t_max: Optional[float] = None,
    t_step: float = 2.0,
    stride: int = 2,
    eps_dir: float = 1e-12,
) -> Tuple[CenterEstimate, dict]:
    """
    Estimate beam center via accumulation (majority voting) along gradient directions.

    Pipeline:
    - J = log1p(raw + offset) on finite pixels
    - mean filter 25×25 on J (NaN-aware), preserve original NaNs
    - NaN-safe central gradients (gx, gy)
    - robustly FILTER extreme gradient magnitudes using an IQR upper bound (on Lp magnitude)
    - for each valid pixel, vote along the (inward) gradient ray for candidate centers

    Center extraction from vote map:
    - Votes are accumulated into an image-sized map V (then Gaussian-smoothed).
    - The center is computed as a top-quantile (q=0.999) weighted centroid of V.
      This is intentionally more stable than argmax when V has a broad/flat peak region.

    Notes:
    - Direction is normalized with L2 norm for stability.
    - Weighting uses Lp magnitude (p>=0). p=0 -> uniform weights.
    """
    if p < 0.0:
        raise ValueError("p must be >= 0.0")
    if stride < 1:
        raise ValueError("stride must be >= 1")
    if t_step <= 0:
        raise ValueError("t_step must be > 0")

    J = _log1p_image(img_raw)
    J_smooth = _nanmean_box_filter(J, size=25)
    gx, gy = _nan_safe_central_grad(J_smooth)

    H, W = J.shape
    if t_max is None:
        t_max = 0.6 * float(min(H, W))

    finite_g = np.isfinite(gx) & np.isfinite(gy)
    m2 = np.full_like(J, np.nan, dtype=float)
    m2[finite_g] = np.sqrt(gx[finite_g] ** 2 + gy[finite_g] ** 2)

    mp = np.full_like(J, np.nan, dtype=float)
    if p == 0.0 or p == 2.0:
        mp[finite_g] = m2[finite_g]
    else:
        mp[finite_g] = (np.abs(gx[finite_g]) ** p + np.abs(gy[finite_g]) ** p) ** (1.0 / p)

    cap = _iqr_upper(mp, k=iqr_k)
    if not np.isfinite(cap):
        est = CenterEstimate((float("nan"), float("nan")), ok=False, reason="no finite gradients", n_used=0, n_total=int(np.sum(finite_g)))
        return est, {"J": J, "J_smooth": J_smooth, "gx": gx, "gy": gy, "m": mp, "w": np.zeros_like(J), "cap": cap, "p": p, "V": np.zeros_like(J)}

    # Valid voting pixels: finite gradient, not extreme magnitude, and nonzero direction
    use = finite_g & np.isfinite(mp) & (mp <= cap) & np.isfinite(m2) & (m2 > 0)

    # Subsample on a grid for speed (still "majority voting").
    grid = np.zeros_like(use)
    grid[::stride, ::stride] = True
    use = use & grid

    n_total = int(np.sum(finite_g))
    n_used = int(np.sum(use))
    if n_used < 1000:
        est = CenterEstimate((float("nan"), float("nan")), ok=False, reason=f"too few voting pixels (n={n_used})", n_used=n_used, n_total=n_total)
        w_map = np.zeros_like(J, dtype=float)
        w_map[use] = (1.0 if p == 0.0 else mp[use])
        return est, {"J": J, "J_smooth": J_smooth, "gx": gx, "gy": gy, "m": mp, "w": w_map, "cap": cap, "p": p, "V": np.zeros_like(J)}

    ys, xs = np.nonzero(use)
    gxs = gx[use]
    gys = gy[use]
    ms_dir = m2[use]
    ms_w = mp[use]

    inv = 1.0 / np.maximum(ms_dir, eps_dir)
    ux = gxs * inv
    uy = gys * inv
    w = np.ones_like(ms_w) if p == 0.0 else ms_w

    # Precompute steps (reused by both passes).
    t_vals = np.arange(float(t_min), float(t_max) + 1e-9, float(t_step), dtype=np.float64)
    if t_vals.size == 0:
        raise ValueError("invalid t_min/t_max/t_step (no t values)")

    def _center_from_votes_top_quantile(
        V: np.ndarray, *, q: float = 0.999
    ) -> Tuple[float, float]:
        """
        Robust center estimate from a (smoothed) vote map using a top-quantile weighted centroid.

        This is intentionally more stable than argmax when V has a broad/flat peak region.
        """
        Vf = np.asarray(V, dtype=np.float64)
        pos = np.isfinite(Vf) & (Vf > 0)
        if not np.any(pos):
            idx0 = int(np.nanargmax(Vf))
            cy0, cx0 = divmod(idx0, W)
            return float(cy0), float(cx0)

        thr = float(np.quantile(Vf[pos], q))
        sel = pos & (Vf >= thr)
        if not np.any(sel):
            idx0 = int(np.nanargmax(Vf))
            cy0, cx0 = divmod(idx0, W)
            return float(cy0), float(cx0)

        yy, xx = np.nonzero(sel)
        ww = Vf[sel]
        sw = float(np.sum(ww))
        if not np.isfinite(sw) or sw <= 0.0:
            idx0 = int(np.nanargmax(Vf))
            cy0, cx0 = divmod(idx0, W)
            return float(cy0), float(cx0)

        cy = float(np.sum(yy.astype(np.float64) * ww) / sw)
        cx = float(np.sum(xx.astype(np.float64) * ww) / sw)
        return cy, cx

    def _accumulate_vote(
        ys0: np.ndarray,
        xs0: np.ndarray,
        ux0: np.ndarray,
        uy0: np.ndarray,
        w0: np.ndarray,
    ) -> Tuple[np.ndarray, Tuple[float, float]]:
        """Accumulate votes and return (V_smooth, center_yx)."""
        V0 = np.zeros((H, W), dtype=np.float64)
        # Vote along both +/- rays; sign ambiguity cancels out via majority voting.
        for t in t_vals:
            for sgn in (-1.0, 1.0):
                cy = ys0 + sgn * (-t * uy0)
                cx = xs0 + sgn * (-t * ux0)
                iy = np.rint(cy).astype(np.int32)
                ix = np.rint(cx).astype(np.int32)
                ok = (iy >= 0) & (iy < H) & (ix >= 0) & (ix < W)
                if not np.any(ok):
                    continue
                np.add.at(V0, (iy[ok], ix[ok]), w0[ok])
        V0s = ndi.gaussian_filter(V0, sigma=3.0, mode="nearest")
        c0 = _center_from_votes_top_quantile(V0s, q=0.999)
        return V0s, c0

    V1, c1 = _accumulate_vote(ys, xs, ux, uy, w)
    est = CenterEstimate(c1, ok=True, reason="ok", n_used=n_used, n_total=n_total)

    w_map = np.zeros_like(J, dtype=float)
    w_map[use] = w
    return est, {
        "J": J,
        "J_smooth": J_smooth,
        "gx": gx,
        "gy": gy,
        "m": mp,
        "w": w_map,
        "cap": cap,
        "p": p,
        "V": V1,
    }


def _save_debug_plot(
    img_raw: np.ndarray,
    est: CenterEstimate,
    dbg: dict,
    *,
    tiff_path: Path,
    out_path: Path,
) -> None:
    J = dbg["J"]
    J_smooth = dbg.get("J_smooth")
    m = dbg["m"]
    w = dbg["w"]
    V = dbg.get("V")
    cap = dbg.get("cap", float("nan"))
    p = dbg.get("p", None)

    fig, axs = plt.subplots(2, 2, figsize=(32, 24))
    title = (
        f"{tiff_path.name}\n"
        f"center_yx={est.center_yx if est.ok else 'N/A'} | ok={est.ok} | {est.reason} | "
        f"used={est.n_used}/{est.n_total} | cap={cap:.3g}"
        + (f" | p={p:g}" if p is not None else "")
    )
    fig.suptitle(title, fontsize=12)

    # (0,0) smoothed log image with estimated center
    axs[0, 0].imshow(J_smooth if J_smooth is not None else J, cmap="viridis", origin="lower")
    axs[0, 0].set_title("log1p(raw) smoothed (mean 25×25)")
    axs[0, 0].set_xlabel("Pixel X")
    axs[0, 0].set_ylabel("Pixel Y")
    if est.ok:
        cy, cx = est.center_yx
        axs[0, 0].plot([cx], [cy], "r+", markersize=20, markeredgewidth=3)
        axs[0, 0].axhline(cy, color="r", lw=1, alpha=0.35)
        axs[0, 0].axvline(cx, color="r", lw=1, alpha=0.35)

    # (0,1) gradient magnitude (log-scaled for visibility)
    axs[0, 1].imshow(np.log1p(m), cmap="magma", origin="lower")
    axs[0, 1].set_title("log1p(|∇ log1p(I)|)")
    axs[0, 1].set_xlabel("Pixel X")
    axs[0, 1].set_ylabel("Pixel Y")

    # (1,0) vote accumulator if available, else weights used
    if V is not None:
        axs[1, 0].imshow(np.log1p(V), cmap="magma", origin="lower")
        axs[1, 0].set_title("log1p(votes accumulator)")
    else:
        axs[1, 0].imshow(np.log1p(w), cmap="magma", origin="lower")
        axs[1, 0].set_title("log1p(weight) (0 = filtered out / invalid)")
    axs[1, 0].set_xlabel("Pixel X")
    axs[1, 0].set_ylabel("Pixel Y")

    # (1,1) unsmoothed log image for reference
    axs[1, 1].imshow(J, cmap="viridis", origin="lower")
    axs[1, 1].set_title("log1p(raw) (unsmoothed reference)")
    axs[1, 1].set_xlabel("Pixel X")
    axs[1, 1].set_ylabel("Pixel Y")

    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Estimate beam center from gradients (debug script).")
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Process at most N images (default: 10). Use 0 for all.",
    )
    parser.add_argument(
        "--iqr-k",
        type=float,
        default=1.5,
        help="IQR multiplier for upper gradient-magnitude filter (default: 1.5).",
    )
    parser.add_argument(
        "--p",
        type=float,
        default=2.0,
        help="Lp norm exponent for gradient re-weighting (p >= 0). p=0 => uniform weights. Default: 2.",
    )
    parser.add_argument(
        "--t-min",
        type=float,
        default=20.0,
        help="Vote ray minimum distance in pixels (vote method). Default: 20.",
    )
    parser.add_argument(
        "--t-max",
        type=float,
        default=0.0,
        help="Vote ray maximum distance in pixels (vote method). 0 => auto. Default: 0.",
    )
    parser.add_argument(
        "--t-step",
        type=float,
        default=2.0,
        help="Vote ray step in pixels (vote method). Default: 2.",
    )
    parser.add_argument(
        "--stride",
        type=int,
        default=2,
        help="Use every Nth pixel for voting (vote method). Default: 2.",
    )
    args = parser.parse_args()

    data_dir = WORKSPACE_ROOT / "data" / "AgBh"
    out_dir_center = WORKSPACE_ROOT / "debug" / "debug_center_grad_vote"
    out_dir_center.mkdir(parents=True, exist_ok=True)

    pattern = re.compile(r".*_AgBh\d+.*\.tif$", re.IGNORECASE)
    all_tifs = sorted(data_dir.glob("*.tif"))
    tif_paths = [p for p in all_tifs if pattern.match(p.name)]
    print(f"DATA_DIR = {data_dir}")
    print(f"Matched *_AgBh<digits>*.tif: {len(tif_paths)}")

    if not tif_paths:
        raise FileNotFoundError(f"No matching .tif found in {data_dir}")

    if args.limit and args.limit > 0:
        tif_paths = tif_paths[: args.limit]

    for tif_path in tif_paths:
        img = read_from_tiff(tif_path)

        est, dbg = estimate_center_grad_vote(
            img,
            iqr_k=args.iqr_k,
            p=args.p,
            t_min=args.t_min,
            t_max=None if args.t_max <= 0 else args.t_max,
            t_step=args.t_step,
            stride=args.stride,
        )
        out_path_center = out_dir_center / f"{tif_path.stem}_center.png"
        _save_debug_plot(img, est, dbg, tiff_path=tif_path, out_path=out_path_center)
        msg = (
            f"{tif_path.name}: ok={est.ok} center_yx={est.center_yx} "
            f"used={est.n_used}/{est.n_total} -> {out_path_center}"
        )
        print(msg, flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

