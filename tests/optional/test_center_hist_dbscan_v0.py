"""
Equal-mass histogram + per-bin DBSCAN beam-center preprocessing (v0).

Universal isotropic SAXS center search (no bin merging):
  1. log1p the image
  2. Drop dim edge pixels with log1p(I) < frac * Q_q(log1p(I)) (default frac=0.25, q=0.995)
  3. Assign each remaining pixel to one of `n_bins` equal-mass bins (same pixel count)
  4. Within each bin, run DBSCAN in (x, y) to separate spatial segments and drop noise
  5. Fit circles per cluster, filter by R² (same as `ring_analysis`)
  6. Initial center = median of fitted circle centers; global refine with the same objective

Debug plots (two PNGs, ≤6 panels each):
  overview — log1p+center, equal-mass bin map, DBSCAN overlay, kept (intensity),
             log1p histogram, pixels per bin
  metrics  — kept colored by circle_quality, kept colored by radial width (r_max−r_min),
             fitted circle centers (coordinates capped near the image)

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

from autosaxs.core.utils import circle_quality_vs_line, fit_circle_xy_r2, read_from_tiff
from autosaxs.skill.calibrate.autocalib import (
    _estimate_center_from_circles,
    _global_refine_center_from_clusters,
    _log1p_image,
)


WORKSPACE_ROOT = Path("/home/mikl/KurchatovCoop")
PKG_SRC = WORKSPACE_ROOT / "autosaxs" / "src"


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


def _equal_mass_bin_idx(vals: np.ndarray, *, n_bins: int) -> np.ndarray:
    """Assign equal-count bin ids in [0, n_bins) by intensity rank (0 = lowest I)."""
    vals = np.asarray(vals, dtype=np.float64)
    n = int(vals.size)
    n_bins = int(n_bins)
    if n == 0 or n_bins <= 0:
        return np.zeros((0,), dtype=np.int32)
    n_bins = min(n_bins, n)
    order = np.argsort(vals, kind="mergesort")
    ranks = np.empty(n, dtype=np.int64)
    ranks[order] = np.arange(n, dtype=np.int64)
    bin_idx = (ranks * n_bins) // n
    return np.clip(bin_idx, 0, n_bins - 1).astype(np.int32, copy=False)


def _assign_equal_mass_bin_map(
    J: np.ndarray,
    *,
    n_bins: int,
    use_mask: Optional[np.ndarray] = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
    """
    Assign equal-mass bin ids to selected finite pixels.

    Returns:
        bin_map, xs, ys, bin_idx, vals, n_bins_eff
    """
    J = np.asarray(J, dtype=float)
    if use_mask is None:
        sel = np.isfinite(J)
    else:
        sel = np.asarray(use_mask, dtype=bool) & np.isfinite(J)
    ys, xs = np.nonzero(sel)
    vals = J[ys, xs].astype(np.float64, copy=False)
    n_bins_eff = min(int(n_bins), int(vals.size)) if vals.size else 0
    if n_bins_eff <= 0:
        bin_map = np.full(J.shape, -1, dtype=np.int32)
        return (
            bin_map,
            np.zeros((0,), dtype=np.int32),
            np.zeros((0,), dtype=np.int32),
            np.zeros((0,), dtype=np.int32),
            vals,
            0,
        )
    bin_idx = _equal_mass_bin_idx(vals, n_bins=n_bins_eff)
    bin_map = np.full(J.shape, -1, dtype=np.int32)
    bin_map[ys, xs] = bin_idx
    return (
        bin_map,
        xs.astype(np.int32, copy=False),
        ys.astype(np.int32, copy=False),
        bin_idx,
        vals,
        n_bins_eff,
    )


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


def _fit_circles_quantile_quality(
    points_xy: np.ndarray,
    labels: np.ndarray,
    *,
    quality_min: float = 0.5,
    min_cluster_points: int = 1000,
    image_shape: Optional[Tuple[int, int]] = None,
) -> Tuple[List[Dict[str, float]], np.ndarray, Dict[int, float]]:
    """
    Fit circles per DBSCAN cluster; keep by `circle_quality_vs_line`.

    Does not use / replace `circle_r2` from ring_analysis.
    """
    circles: List[Dict[str, float]] = []
    kept_labels: List[int] = []
    quality_by_label: Dict[int, float] = {}

    if points_xy.shape[0] == 0:
        return circles, np.asarray(kept_labels, dtype=int), quality_by_label

    for lab in np.unique(labels):
        if int(lab) == -1:
            continue
        mask = labels == lab
        pts = points_xy[mask]
        if pts.shape[0] < int(min_cluster_points) or pts.shape[0] < 3:
            continue
        fit = fit_circle_xy_r2(pts, image_shape=image_shape)
        cx = float(fit["center_x"])
        cy = float(fit["center_y"])
        r_px = float(fit["r_px"])
        quality = circle_quality_vs_line(pts, center_x=cx, center_y=cy, r_px=r_px)
        quality_by_label[int(lab)] = float(quality)
        if not np.isfinite(quality) or quality < float(quality_min):
            continue
        dist = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
        circles.append(
            {
                "label": float(lab),
                "center_x": cx,
                "center_y": cy,
                "r_px": r_px,
                "circle_r2": float(fit["circle_r2"]),
                "circle_quality": float(quality),
                "radial_width_px": float(np.max(dist) - np.min(dist)),
                "n_points": float(pts.shape[0]),
            }
        )
        kept_labels.append(int(lab))

    return circles, np.asarray(kept_labels, dtype=int), quality_by_label


def estimate_center_hist_dbscan_v0(
    img_raw: np.ndarray,
    *,
    n_bins: int = 100,
    dbscan_eps: float = 5.0,
    dbscan_min_samples: int = 10,
    min_bin_points: Optional[int] = None,
    max_bin_points: int = 250_000,
    circle_quality_min: float = 0.5,
    global_refine_bounds_half_width_px: float = 50.0,
    intensity_floor_frac: float = 0.25,
    intensity_floor_quantile: float = 0.995,
) -> Tuple[HistDbscanCenterResult, Dict[str, object]]:
    """
    Equal-mass intensity binning + per-bin DBSCAN + quantile-span circle quality.
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

    bin_map, xs, ys, bin_idx, vals_used, n_bins_eff = _assign_equal_mass_bin_map(
        J, n_bins=int(n_bins), use_mask=use_mask
    )
    points_xy, labels, bins_used, cluster_dbg = _dbscan_clusters_per_bin(
        xs,
        ys,
        bin_idx,
        n_bins=int(n_bins_eff),
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
    circles, kept_labels, circle_quality_by_label = _fit_circles_quantile_quality(
        points_xy,
        labels,
        quality_min=float(circle_quality_min),
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
        "n_bins": n_bins_eff,
        "vals_used": vals_used,
        "bin_map": bin_map,
        "points_xy": points_xy,
        "labels": labels,
        "bins_used": bins_used,
        "circles": circles,
        "kept_labels": kept_labels,
        "circle_quality_by_label": circle_quality_by_label,
        "J_kept": _kept_cluster_intensity_image(J, points_xy, labels, kept_labels),
        **cluster_dbg,
    }
    return res, dbg


def _kept_scalar_image(
    shape: Tuple[int, int],
    points_xy: np.ndarray,
    labels: np.ndarray,
    value_by_label: Dict[int, float],
) -> np.ma.MaskedArray:
    """Paint kept-cluster pixels with a per-label scalar; mask everything else."""
    out = np.ma.masked_all(shape, dtype=float)
    if points_xy.size == 0 or labels.size == 0 or not value_by_label:
        return out
    for lab, val in value_by_label.items():
        if not np.isfinite(float(val)):
            continue
        mask = labels == int(lab)
        if not np.any(mask):
            continue
        xs = points_xy[mask, 0].astype(np.int32, copy=False)
        ys = points_xy[mask, 1].astype(np.int32, copy=False)
        out[ys, xs] = float(val)
    return out


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
    vals_used = np.asarray(dbg.get("vals_used", np.zeros((0,))), dtype=float)
    J_kept = dbg.get("J_kept")
    if J_kept is None:
        J_kept = np.ma.masked_all(J.shape, dtype=float)

    fig, axs = plt.subplots(2, 3, figsize=(22, 12))

    # (0,0) Initial log1p image with fitted center.
    im0 = axs[0, 0].imshow(J, cmap="viridis", origin="lower")
    _overlay_center_markers(
        axs[0, 0],
        center_init_yx=result.center_init_yx,
        center_refined_yx=result.center_yx,
    )
    axs[0, 0].set_title("log1p(image) + center (yellow=init, red=refined)")
    axs[0, 0].set_xlabel("Pixel X")
    axs[0, 0].set_ylabel("Pixel Y")
    fig.colorbar(im0, ax=axs[0, 0], fraction=0.046, pad=0.04)

    # (0,1) Intensity bins, colored by bin id (discrete colormap).
    bin_vis = np.ma.masked_where(bin_map < 0, bin_map.astype(float))
    n_bins = int(dbg.get("n_bins", max(1, int(bin_map.max()) + 1 if bin_map.size else 1)))
    n_bins = max(1, n_bins)
    cmap_bins = ListedColormap(plt.get_cmap("nipy_spectral")(np.linspace(0, 1, n_bins)))
    bin_bounds = np.arange(-0.5, n_bins + 0.5, 1.0)
    bin_norm = BoundaryNorm(bin_bounds, n_bins)
    im1 = axs[0, 1].imshow(bin_vis, cmap=cmap_bins, norm=bin_norm, origin="lower")
    axs[0, 1].set_title(f"equal-mass intensity bins (n_bins={n_bins}, floor-cut)")
    axs[0, 1].set_xlabel("Pixel X")
    axs[0, 1].set_ylabel("Pixel Y")
    tick_step = max(1, n_bins // 10)
    bin_ticks = np.arange(0, n_bins, tick_step)
    fig.colorbar(
        im1,
        ax=axs[0, 1],
        fraction=0.046,
        pad=0.04,
        boundaries=bin_bounds,
        ticks=bin_ticks,
    )

    # (0,2) DBSCAN clusters overlay.
    axs[0, 2].imshow(J, cmap="viridis", origin="lower")
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
                axs[0, 2].scatter(xs[mask], ys[mask], s=2, c="lightgrey", alpha=0.5, linewidths=0)
            else:
                axs[0, 2].scatter(
                    xs[mask], ys[mask], s=2, c=[id_to_color[lab_int]], alpha=0.85, linewidths=0
                )
    axs[0, 2].set_title(
        f"DBSCAN clusters (bins_used={result.n_bins_used}, clusters={result.n_clusters}, "
        f"pts={result.n_clustered_points}, noise={result.n_noise_points})"
    )
    axs[0, 2].set_xlabel("Pixel X")
    axs[0, 2].set_ylabel("Pixel Y")

    # (1,0) Kept clusters only; rejected pixels masked.
    im3 = axs[1, 0].imshow(J_kept, cmap="viridis", origin="lower")
    axs[1, 0].set_title(f"kept clusters (n={result.n_kept_clusters})")
    axs[1, 0].set_xlabel("Pixel X")
    axs[1, 0].set_ylabel("Pixel Y")
    fig.colorbar(im3, ax=axs[1, 0], fraction=0.046, pad=0.04)

    # (1,1) histogram of log1p(I)
    if vals_used.size > 0:
        axs[1, 1].hist(vals_used, bins=100, color="steelblue", edgecolor="none", alpha=0.9)
        floor_th = dbg.get("intensity_floor_thresh")
        if floor_th is not None and np.isfinite(float(floor_th)):
            axs[1, 1].axvline(float(floor_th), color="crimson", ls="--", lw=1.5, label="floor")
            axs[1, 1].legend(loc="upper right", fontsize=8)
    axs[1, 1].set_title("histogram of log1p(I) (used pixels)")
    axs[1, 1].set_xlabel("log1p(I)")
    axs[1, 1].set_ylabel("count")

    # (1,2) unused / spare panel: bin occupancy
    bin_counts = np.asarray(dbg.get("bin_counts", []), dtype=float)
    if bin_counts.size > 0:
        axs[1, 2].bar(np.arange(bin_counts.size), bin_counts, color="gray", width=1.0, align="center")
    axs[1, 2].set_title("pixels per equal-mass bin")
    axs[1, 2].set_xlabel("bin id")
    axs[1, 2].set_ylabel("count")

    cy, cx = result.center_yx
    fig.suptitle(
        f"equal-mass hist+DBSCAN | ok={result.ok} reason={result.reason} "
        f"center_yx=({cy:.2f},{cx:.2f}) kept={result.n_kept_clusters}",
        fontsize=12,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def _save_debug_plot_metrics(
    result: HistDbscanCenterResult,
    dbg: Dict[str, object],
    *,
    out_path: Path,
    center_cap_pad_frac: float = 1.0,
) -> None:
    """
    Second PNG: kept clusters by circle_quality, by radial width, and fitted centers.
    """
    J = np.asarray(dbg["J"], dtype=float)
    H, W = int(J.shape[0]), int(J.shape[1])
    points_xy = np.asarray(dbg.get("points_xy", np.zeros((0, 2))), dtype=float)
    labels = np.asarray(dbg.get("labels", np.zeros((0,), dtype=int)), dtype=int)
    circles = list(dbg.get("circles", []))

    quality_by_label = {int(c["label"]): float(c["circle_quality"]) for c in circles}
    width_by_label = {
        int(c["label"]): float(c.get("radial_width_px", float("nan"))) for c in circles
    }
    img_q = _kept_scalar_image((H, W), points_xy, labels, quality_by_label)
    img_width = _kept_scalar_image((H, W), points_xy, labels, width_by_label)

    fig, axs = plt.subplots(1, 3, figsize=(22, 7))

    im0 = axs[0].imshow(img_q, cmap="magma", origin="lower")
    axs[0].set_title(f"kept clusters by circle_quality (n={len(circles)})")
    axs[0].set_xlabel("Pixel X")
    axs[0].set_ylabel("Pixel Y")
    fig.colorbar(im0, ax=axs[0], fraction=0.046, pad=0.04)

    im1 = axs[1].imshow(img_width, cmap="viridis", origin="lower")
    axs[1].set_title("kept clusters by radial width (r_max−r_min)")
    axs[1].set_xlabel("Pixel X")
    axs[1].set_ylabel("Pixel Y")
    fig.colorbar(im1, ax=axs[1], fraction=0.046, pad=0.04)

    # Fitted circle centers; cap far outliers for display.
    pad = float(center_cap_pad_frac) * float(max(H, W))
    x_lo, x_hi = -pad, (W - 1) + pad
    y_lo, y_hi = -pad, (H - 1) + pad
    axs[2].imshow(J, cmap="gray", origin="lower", alpha=0.55)
    axs[2].set_xlim(x_lo, x_hi)
    axs[2].set_ylim(y_lo, y_hi)
    n_clipped = 0
    if circles:
        cxs = np.asarray([float(c["center_x"]) for c in circles], dtype=float)
        cys = np.asarray([float(c["center_y"]) for c in circles], dtype=float)
        finite = np.isfinite(cxs) & np.isfinite(cys)
        cxs = cxs[finite]
        cys = cys[finite]
        n_clipped = int(np.sum((cxs < x_lo) | (cxs > x_hi) | (cys < y_lo) | (cys > y_hi)))
        cxs_p = np.clip(cxs, x_lo, x_hi)
        cys_p = np.clip(cys, y_lo, y_hi)
        axs[2].scatter(
            cxs_p, cys_p, s=18, c="cyan", edgecolors="k", linewidths=0.3, zorder=5
        )
    _overlay_center_markers(
        axs[2],
        center_init_yx=result.center_init_yx,
        center_refined_yx=result.center_yx,
    )
    axs[2].add_patch(
        plt.Rectangle((0, 0), W, H, fill=False, edgecolor="yellow", lw=1.2, ls="--", zorder=4)
    )
    axs[2].set_title(
        f"fitted circle centers (capped±{center_cap_pad_frac:.0f}·maxdim; clipped={n_clipped})"
    )
    axs[2].set_xlabel("Pixel X")
    axs[2].set_ylabel("Pixel Y")
    axs[2].set_aspect("equal", adjustable="box")

    cy, cx = result.center_yx
    fig.suptitle(
        f"equal-mass metrics | ok={result.ok} center_yx=({cy:.2f},{cx:.2f}) kept={result.n_kept_clusters}",
        fontsize=12,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(out_path, dpi=130)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Equal-mass histogram + per-bin DBSCAN center preprocessing (v0)."
    )
    parser.add_argument("--limit", type=int, default=0, help="Process at most N images (0=all).")
    parser.add_argument("--n-bins", type=int, default=100, help="Number of equal-mass intensity bins.")
    parser.add_argument("--dbscan-eps", type=float, default=5.0, help="DBSCAN eps in pixels.")
    parser.add_argument("--dbscan-min-samples", type=int, default=10, help="DBSCAN min_samples.")
    parser.add_argument(
        "--max-bin-points",
        type=int,
        default=250_000,
        help="Skip bins with more points than this (background guard).",
    )
    parser.add_argument(
        "--circle-quality-min",
        type=float,
        default=0.5,
        help="Minimum circle_quality_vs_line to keep a cluster.",
    )
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
        default="data/center_benchmark",
        help="Directory with input .tif files (relative to workspace root or absolute).",
    )
    parser.add_argument(
        "--out-dir",
        type=str,
        default="debug_center_mass_dbscan_v0",
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

    print(f"DATA_DIR={data_dir} N={len(tif_paths)} OUT={out_dir}", flush=True)

    for tif_path in tif_paths:
        img = read_from_tiff(tif_path)
        result, dbg = estimate_center_hist_dbscan_v0(
            img,
            n_bins=int(args.n_bins),
            dbscan_eps=float(args.dbscan_eps),
            dbscan_min_samples=int(args.dbscan_min_samples),
            max_bin_points=int(args.max_bin_points),
            circle_quality_min=float(args.circle_quality_min),
            global_refine_bounds_half_width_px=float(args.global_refine_bounds_half_width_px),
            intensity_floor_frac=float(args.intensity_floor_frac),
            intensity_floor_quantile=float(args.intensity_floor_quantile),
        )
        out_path = out_dir / f"{tif_path.stem}_mass_hist_dbscan.png"
        out_metrics = out_dir / f"{tif_path.stem}_mass_hist_dbscan_metrics.png"
        _save_debug_plot(img, result, dbg, out_path=out_path)
        _save_debug_plot_metrics(result, dbg, out_path=out_metrics)
        cy, cx = result.center_yx
        print(
            f"{tif_path.name}: ok={result.ok} center_yx=({cy:.2f},{cx:.2f}) "
            f"kept={result.n_kept_clusters} bins_used={result.n_bins_used} "
            f"clusters={result.n_clusters} clustered_pts={result.n_clustered_points} "
            f"noise={result.n_noise_points} used={dbg.get('n_used')}/{result.n_finite} "
            f"floor={dbg.get('intensity_floor_thresh')} "
            f"skipped_sparse={dbg.get('bins_skipped_sparse')} "
            f"skipped_dense={dbg.get('bins_skipped_dense')} -> {out_path} ; {out_metrics}",
            flush=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
