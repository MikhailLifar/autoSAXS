"""
Histogram + per-bin DBSCAN beam-center preprocessing (v0).

Generalizes the ring-analysis pixel-selection idea to arbitrary isotropic SAXS images:
  1. log1p the image
  2. Drop dim edge pixels with log1p(I) < frac * Q_q(log1p(I)) (default frac=0.25, q=0.995)
  3. Assign each remaining pixel to one of `n_bins` uniform bins in log1p-intensity
  4. Within each bin, run DBSCAN in (x, y) to separate spatial segments and drop noise
  5. Fit circles per cluster, filter by R² (same as `ring_analysis`)
  6. Initial center = median of fitted circle centers; global refine with the same objective

This script mirrors `test_center_grad_ls_v0.py` and writes debug plots:
  - initial log1p image (with fitted center marked)
  - intensity-bin map (colored by bin id)
  - DBSCAN clusters overlay (colored by global cluster id)
  - kept-cluster pixels only (rejected pixels masked)

Clustering is grouped by bin via argsort (one sort over all pixels) so we avoid
repeated full-image masks per bin.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap

from autosaxs.core.utils import read_from_tiff
from autosaxs.skill.calibrate.autocalib import (
    _estimate_center_from_circles,
    _fit_circles_from_dbscan,
    _global_refine_center_from_clusters,
    _log1p_image,
)


WORKSPACE_ROOT = Path("/home/mikl/KurchatovCoop")
REPOS_DIR = WORKSPACE_ROOT / "repos"


@dataclass(frozen=True)
class HistDbscanCenterResult:
    ok: bool
    reason: str
    n_finite: int
    n_bins_used: int
    n_clusters: int
    n_clustered_points: int
    n_noise_points: int
    n_kept_clusters: int
    center_yx: Tuple[float, float]
    center_init_yx: Tuple[float, float]


def _dim_edge_cut_mask(
    J: np.ndarray,
    *,
    intensity_floor_frac: float = 0.25,
    intensity_floor_quantile: float = 0.995,
) -> Tuple[np.ndarray, float, float]:
    """
    Keep pixels with log1p(I) >= frac * Q_q(log1p(I)); cut dim edge background.

    Returns:
        use_mask, threshold, quantile_value
    """
    J = np.asarray(J, dtype=float)
    finite = np.isfinite(J)
    if not np.any(finite):
        return np.zeros_like(J, dtype=bool), float("nan"), float("nan")
    vals = J[finite].astype(np.float64, copy=False)
    q_val = float(np.quantile(vals, float(intensity_floor_quantile)))
    thresh = float(intensity_floor_frac) * q_val
    use = finite & (J >= thresh)
    return use, thresh, q_val


def _histogram_edges(J: np.ndarray, *, n_bins: int, use_mask: Optional[np.ndarray] = None) -> np.ndarray:
    """Uniform bin edges on selected log1p values."""
    J = np.asarray(J, dtype=float)
    if use_mask is None:
        sel = np.isfinite(J)
    else:
        sel = np.asarray(use_mask, dtype=bool) & np.isfinite(J)
    if not np.any(sel):
        return np.linspace(0.0, 1.0, int(n_bins) + 1, dtype=float)
    vals = J[sel].astype(np.float64, copy=False)
    vmin = float(np.min(vals))
    vmax = float(np.max(vals))
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmax <= vmin:
        vmax = vmin + 1.0
    return np.linspace(vmin, vmax, int(n_bins) + 1, dtype=float)


def _assign_bin_map(
    J: np.ndarray,
    edges: np.ndarray,
    *,
    use_mask: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Assign bin ids to selected finite pixels.

    Returns:
        bin_map: (H, W) int image, -1 where unused / not finite
        xs, ys, bin_idx: 1D arrays for selected pixels only
    """
    J = np.asarray(J, dtype=float)
    if use_mask is None:
        sel = np.isfinite(J)
    else:
        sel = np.asarray(use_mask, dtype=bool) & np.isfinite(J)
    ys, xs = np.nonzero(sel)
    vals = J[sel].astype(np.float64, copy=False)

    n_bins = int(edges.size) - 1
    bin_idx = np.digitize(vals, edges, right=False) - 1
    bin_idx = np.clip(bin_idx, 0, n_bins - 1)

    bin_map = np.full(J.shape, -1, dtype=np.int32)
    bin_map[ys, xs] = bin_idx.astype(np.int32, copy=False)
    return bin_map, xs.astype(np.int32, copy=False), ys.astype(np.int32, copy=False), bin_idx.astype(np.int32, copy=False)


def _dbscan_clusters_per_bin(
    xs: np.ndarray,
    ys: np.ndarray,
    bin_idx: np.ndarray,
    *,
    n_bins: int,
    dbscan_eps: float,
    dbscan_min_samples: int,
    min_bin_points: int,
    max_bin_points: int,
) -> Tuple[np.ndarray, np.ndarray, List[int], Dict[str, object]]:
    """
    Run DBSCAN independently in each intensity bin.

    Uses argsort(bin_idx) to partition pixels without rebuilding masks per bin.
    Global cluster labels are contiguous from 0; noise is -1.
    """
    n_pts = int(xs.size)
    if n_pts == 0:
        return (
            np.zeros((0, 2), dtype=np.float64),
            np.zeros((0,), dtype=np.int32),
            [],
            {"bins_skipped_sparse": 0, "bins_skipped_dense": 0, "bins_clustered": 0},
        )

    from sklearn.cluster import DBSCAN  # type: ignore

    order = np.argsort(bin_idx, kind="stable")
    bin_sorted = bin_idx[order]
    xs_sorted = xs[order]
    ys_sorted = ys[order]

    # Bin boundaries in the sorted arrays.
    counts = np.bincount(bin_sorted, minlength=int(n_bins))
    starts = np.zeros(int(n_bins) + 1, dtype=np.int64)
    starts[1:] = np.cumsum(counts)

    points_all: List[np.ndarray] = []
    labels_all: List[np.ndarray] = []
    bins_used: List[int] = []
    next_label = 0
    bins_skipped_sparse = 0
    bins_skipped_dense = 0
    bins_clustered = 0

    dbscan = DBSCAN(eps=float(dbscan_eps), min_samples=int(dbscan_min_samples))

    for b in range(int(n_bins)):
        s = int(starts[b])
        e = int(starts[b + 1])
        n_in_bin = e - s
        if n_in_bin < int(min_bin_points):
            bins_skipped_sparse += 1
            continue
        if n_in_bin > int(max_bin_points):
            bins_skipped_dense += 1
            continue

        xs_b = xs_sorted[s:e]
        ys_b = ys_sorted[s:e]

        pts = np.column_stack(
            [xs_b.astype(np.float64, copy=False), ys_b.astype(np.float64, copy=False)]
        )
        local_labels = dbscan.fit_predict(pts)

        # Remap local cluster ids to global ids; keep noise as -1.
        uniq = np.unique(local_labels)
        remap = {int(lab): -1 if int(lab) == -1 else next_label + i for i, lab in enumerate(uniq) if int(lab) != -1}
        n_new = len(remap)
        if n_new > 0:
            next_label += n_new

        global_labels = np.full(local_labels.shape, -1, dtype=np.int32)
        for lab, glab in remap.items():
            global_labels[local_labels == lab] = int(glab)

        points_all.append(pts)
        labels_all.append(global_labels)
        bins_used.append(int(b))
        bins_clustered += 1

    if not points_all:
        return (
            np.zeros((0, 2), dtype=np.float64),
            np.zeros((0,), dtype=np.int32),
            [],
            {
                "bins_skipped_sparse": bins_skipped_sparse,
                "bins_skipped_dense": bins_skipped_dense,
                "bins_clustered": bins_clustered,
            },
        )

    points_xy = np.vstack(points_all)
    labels = np.concatenate(labels_all).astype(np.int32, copy=False)
    dbg = {
        "bins_skipped_sparse": bins_skipped_sparse,
        "bins_skipped_dense": bins_skipped_dense,
        "bins_clustered": bins_clustered,
        "bin_counts": counts,
    }
    return points_xy, labels, bins_used, dbg


def _kept_cluster_intensity_image(
    J: np.ndarray,
    points_xy: np.ndarray,
    labels: np.ndarray,
    kept_labels: np.ndarray,
) -> np.ma.MaskedArray:
    """Show log1p intensity only for pixels in kept clusters; mask everything else."""
    J_kept = np.ma.masked_all(J.shape, dtype=float)
    if points_xy.size == 0 or labels.size == 0 or kept_labels.size == 0:
        return J_kept
    keep = np.isin(labels, kept_labels)
    if not np.any(keep):
        return J_kept
    xs = points_xy[keep, 0].astype(np.int32, copy=False)
    ys = points_xy[keep, 1].astype(np.int32, copy=False)
    J_kept[ys, xs] = J[ys, xs]
    return J_kept


def estimate_center_hist_dbscan_v0(
    img_raw: np.ndarray,
    *,
    n_bins: int = 20,
    dbscan_eps: float = 5.0,
    dbscan_min_samples: int = 10,
    min_bin_points: Optional[int] = None,
    max_bin_points: int = 250_000,
    circle_r2_min: float = 0.5,
    global_refine_bounds_half_width_px: float = 50.0,
    intensity_floor_frac: float = 0.25,
    intensity_floor_quantile: float = 0.995,
) -> Tuple[HistDbscanCenterResult, Dict[str, object]]:
    """
    Histogram binning + per-bin DBSCAN + ring_analysis-style center detection.
    """
    img_raw = np.asarray(img_raw, dtype=float)
    J = _log1p_image(img_raw)
    finite = np.isfinite(J)
    n_finite = int(np.sum(finite))
    if n_finite == 0:
        res = HistDbscanCenterResult(
            ok=False,
            reason="no finite pixels",
            n_finite=0,
            n_bins_used=0,
            n_clusters=0,
            n_clustered_points=0,
            n_noise_points=0,
            n_kept_clusters=0,
            center_yx=(float("nan"), float("nan")),
            center_init_yx=(float("nan"), float("nan")),
        )
        return res, {"J": J}

    use_mask, floor_thresh, floor_q = _dim_edge_cut_mask(
        J,
        intensity_floor_frac=float(intensity_floor_frac),
        intensity_floor_quantile=float(intensity_floor_quantile),
    )
    n_used = int(np.sum(use_mask))
    if n_used == 0:
        res = HistDbscanCenterResult(
            ok=False,
            reason="no pixels above intensity floor",
            n_finite=n_finite,
            n_bins_used=0,
            n_clusters=0,
            n_clustered_points=0,
            n_noise_points=0,
            n_kept_clusters=0,
            center_yx=(float("nan"), float("nan")),
            center_init_yx=(float("nan"), float("nan")),
        )
        return res, {
            "J": J,
            "use_mask": use_mask,
            "intensity_floor_thresh": floor_thresh,
            "intensity_floor_quantile_value": floor_q,
        }

    if min_bin_points is None:
        min_bin_points = int(dbscan_min_samples)

    edges = _histogram_edges(J, n_bins=int(n_bins), use_mask=use_mask)
    bin_map, xs, ys, bin_idx = _assign_bin_map(J, edges, use_mask=use_mask)
    points_xy, labels, bins_used, cluster_dbg = _dbscan_clusters_per_bin(
        xs,
        ys,
        bin_idx,
        n_bins=int(n_bins),
        dbscan_eps=float(dbscan_eps),
        dbscan_min_samples=int(dbscan_min_samples),
        min_bin_points=int(min_bin_points),
        max_bin_points=int(max_bin_points),
    )

    n_noise = int(np.sum(labels == -1)) if labels.size else 0
    n_clustered = int(np.sum(labels != -1)) if labels.size else 0
    cluster_ids = sorted(int(x) for x in np.unique(labels) if int(x) != -1)
    n_clusters = len(cluster_ids)

    image_shape = (int(img_raw.shape[0]), int(img_raw.shape[1]))
    circles, kept_labels, circle_r2_by_label = _fit_circles_from_dbscan(
        points_xy,
        labels,
        r2_min=float(circle_r2_min),
        image_shape=image_shape,
    )
    center_init_y, center_init_x = _estimate_center_from_circles(circles)
    center_ref_y, center_ref_x = _global_refine_center_from_clusters(
        points_xy,
        labels,
        kept_labels,
        init_center_yx=(center_init_y, center_init_x),
        bounds_half_width_px=float(global_refine_bounds_half_width_px),
    )

    n_kept = int(kept_labels.size)
    center_ok = (
        n_kept > 0
        and np.isfinite(center_ref_y)
        and np.isfinite(center_ref_x)
    )
    if not center_ok:
        reason = "no accepted clusters" if n_kept == 0 else "invalid refined center"
    elif n_clusters == 0 or n_clustered == 0:
        reason = "no clusters found"
        center_ok = False
    else:
        reason = "ok"

    res = HistDbscanCenterResult(
        ok=center_ok,
        reason=reason,
        n_finite=n_finite,
        n_bins_used=len(bins_used),
        n_clusters=n_clusters,
        n_clustered_points=n_clustered,
        n_noise_points=n_noise,
        n_kept_clusters=n_kept,
        center_yx=(float(center_ref_y), float(center_ref_x)),
        center_init_yx=(float(center_init_y), float(center_init_x)),
    )
    dbg: Dict[str, object] = {
        "J": J,
        "use_mask": use_mask,
        "intensity_floor_thresh": floor_thresh,
        "intensity_floor_quantile_value": floor_q,
        "n_used": n_used,
        "edges": edges,
        "bin_map": bin_map,
        "points_xy": points_xy,
        "labels": labels,
        "bins_used": bins_used,
        "circles": circles,
        "kept_labels": kept_labels,
        "circle_r2_by_label": circle_r2_by_label,
        "J_kept": _kept_cluster_intensity_image(J, points_xy, labels, kept_labels),
        **cluster_dbg,
    }
    return res, dbg


def _overlay_center_markers(
    ax: plt.Axes,
    *,
    center_init_yx: Tuple[float, float],
    center_refined_yx: Tuple[float, float],
) -> None:
    cy_i, cx_i = center_init_yx
    cy_r, cx_r = center_refined_yx
    if np.isfinite(cy_i) and np.isfinite(cx_i):
        ax.scatter([cx_i], [cy_i], marker="x", s=120, c="yellow", linewidths=2.5, zorder=5)
    if np.isfinite(cy_r) and np.isfinite(cx_r):
        ax.scatter([cx_r], [cy_r], marker="+", s=200, c="red", linewidths=3.5, zorder=6)
        ax.axhline(cy_r, color="r", lw=1, alpha=0.25, zorder=4)
        ax.axvline(cx_r, color="r", lw=1, alpha=0.25, zorder=4)


def _save_debug_plot(
    img_raw: np.ndarray,
    result: HistDbscanCenterResult,
    dbg: Dict[str, object],
    *,
    out_path: Path,
) -> None:
    J = np.asarray(dbg["J"], dtype=float)
    bin_map = np.asarray(dbg["bin_map"], dtype=int)
    points_xy = np.asarray(dbg.get("points_xy", np.zeros((0, 2))), dtype=float)
    labels = np.asarray(dbg.get("labels", np.zeros((0,), dtype=int)), dtype=int)
    J_kept = dbg.get("J_kept")
    if J_kept is None:
        J_kept = np.ma.masked_all(J.shape, dtype=float)

    fig, axs = plt.subplots(1, 4, figsize=(28, 7))

    # (0) Initial log1p image with fitted center.
    im0 = axs[0].imshow(J, cmap="viridis", origin="lower")
    _overlay_center_markers(
        axs[0],
        center_init_yx=result.center_init_yx,
        center_refined_yx=result.center_yx,
    )
    axs[0].set_title("log1p(image) + center (yellow=init, red=refined)")
    axs[0].set_xlabel("Pixel X")
    axs[0].set_ylabel("Pixel Y")
    fig.colorbar(im0, ax=axs[0], fraction=0.046, pad=0.04)

    # (1) Intensity bins, colored by bin id (discrete colormap).
    bin_vis = np.ma.masked_where(bin_map < 0, bin_map.astype(float))
    n_bins = int(np.asarray(dbg["edges"]).size) - 1
    cmap_bins = ListedColormap(plt.get_cmap("nipy_spectral")(np.linspace(0, 1, n_bins)))
    bin_bounds = np.arange(-0.5, n_bins + 0.5, 1.0)
    bin_norm = BoundaryNorm(bin_bounds, n_bins)
    im1 = axs[1].imshow(bin_vis, cmap=cmap_bins, norm=bin_norm, origin="lower")
    axs[1].set_title(
        f"intensity bins (n_bins={n_bins}, floor-cut dim pixels)"
    )
    axs[1].set_xlabel("Pixel X")
    axs[1].set_ylabel("Pixel Y")
    tick_step = max(1, n_bins // 10)
    bin_ticks = np.arange(0, n_bins, tick_step)
    fig.colorbar(
        im1,
        ax=axs[1],
        fraction=0.046,
        pad=0.04,
        boundaries=bin_bounds,
        ticks=bin_ticks,
    )

    # (2) DBSCAN clusters overlay.
    axs[2].imshow(J, cmap="viridis", origin="lower")
    if points_xy.size > 0 and labels.size > 0:
        xs = points_xy[:, 0]
        ys = points_xy[:, 1]
        uniq = np.unique(labels)
        cmap = plt.get_cmap("tab20")
        cluster_ids = sorted(int(u) for u in uniq if int(u) != -1)
        id_to_color = {lab: cmap(i % 20) for i, lab in enumerate(cluster_ids)}
        for lab in uniq.tolist():
            lab_int = int(lab)
            mask = labels == lab
            if lab_int == -1:
                axs[2].scatter(xs[mask], ys[mask], s=2, c="lightgrey", alpha=0.5, linewidths=0)
            else:
                axs[2].scatter(xs[mask], ys[mask], s=2, c=[id_to_color[lab_int]], alpha=0.85, linewidths=0)
    axs[2].set_title(
        f"DBSCAN clusters (bins_used={result.n_bins_used}, clusters={result.n_clusters}, "
        f"pts={result.n_clustered_points}, noise={result.n_noise_points})"
    )
    axs[2].set_xlabel("Pixel X")
    axs[2].set_ylabel("Pixel Y")

    # (3) Kept clusters only; rejected pixels masked.
    im3 = axs[3].imshow(J_kept, cmap="viridis", origin="lower")
    axs[3].set_title(f"kept clusters (n={result.n_kept_clusters})")
    axs[3].set_xlabel("Pixel X")
    axs[3].set_ylabel("Pixel Y")
    fig.colorbar(im3, ax=axs[3], fraction=0.046, pad=0.04)

    cy, cx = result.center_yx
    fig.suptitle(
        f"histogram+DBSCAN center | ok={result.ok} reason={result.reason} "
        f"center_yx=({cy:.2f},{cx:.2f}) kept={result.n_kept_clusters}",
        fontsize=12,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(description="Histogram + per-bin DBSCAN center preprocessing (v0).")
    parser.add_argument("--limit", type=int, default=0, help="Process at most N images (0=all).")
    parser.add_argument("--n-bins", type=int, default=20, help="Number of uniform log1p-intensity bins.")
    parser.add_argument("--dbscan-eps", type=float, default=5.0, help="DBSCAN eps in pixels.")
    parser.add_argument("--dbscan-min-samples", type=int, default=10, help="DBSCAN min_samples.")
    parser.add_argument(
        "--max-bin-points",
        type=int,
        default=250_000,
        help="Skip bins with more points than this (background guard).",
    )
    parser.add_argument("--circle-r2-min", type=float, default=0.5, help="Minimum circle R² to keep a cluster.")
    parser.add_argument(
        "--global-refine-bounds-half-width-px",
        type=float,
        default=50.0,
        help="L-BFGS-B search half-width around the median center (px).",
    )
    parser.add_argument(
        "--intensity-floor-frac",
        type=float,
        default=0.25,
        help="Cut pixels with log1p(I) < frac * Q_q(log1p(I)) (default: 0.25).",
    )
    parser.add_argument(
        "--intensity-floor-quantile",
        type=float,
        default=0.995,
        help="Quantile q used in the intensity floor (default: 0.995).",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data/calib_benchmark",
        help="Directory with input .tif files (relative to workspace root or absolute).",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default="debug_center_hist_dbscan_v0",
        help="Output directory for PNGs (under workspace debug/).",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.is_absolute():
        data_dir = WORKSPACE_ROOT / data_dir
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
        result, dbg = estimate_center_hist_dbscan_v0(
            img,
            n_bins=int(args.n_bins),
            dbscan_eps=float(args.dbscan_eps),
            dbscan_min_samples=int(args.dbscan_min_samples),
            max_bin_points=int(args.max_bin_points),
            circle_r2_min=float(args.circle_r2_min),
            global_refine_bounds_half_width_px=float(args.global_refine_bounds_half_width_px),
            intensity_floor_frac=float(args.intensity_floor_frac),
            intensity_floor_quantile=float(args.intensity_floor_quantile),
        )
        out_path = out_dir / f"{tif_path.stem}_hist_dbscan.png"
        _save_debug_plot(img, result, dbg, out_path=out_path)
        cy, cx = result.center_yx
        print(
            f"{tif_path.name}: ok={result.ok} center_yx=({cy:.2f},{cx:.2f}) "
            f"kept={result.n_kept_clusters} bins_used={result.n_bins_used} "
            f"clusters={result.n_clusters} clustered_pts={result.n_clustered_points} "
            f"noise={result.n_noise_points} used={dbg.get('n_used')}/{result.n_finite} "
            f"floor={dbg.get('intensity_floor_thresh')} "
            f"skipped_sparse={dbg.get('bins_skipped_sparse')} "
            f"skipped_dense={dbg.get('bins_skipped_dense')} -> {out_path}",
            flush=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
