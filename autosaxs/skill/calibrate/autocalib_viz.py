from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Circle  # noqa: E402


def _ensure_parent_dir(out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)


def save_refined_curve_with_theoretical_peaks(
    curve_calibrated: Sequence[float],
    theoretical_peaks: Sequence[float],
    out_path: Path,
    *,
    dpi: int = 120,
    figsize: tuple[float, float] = (10, 6),
) -> None:
    """Save 1D refined curve plot with theoretical peak lines overlaid."""
    from autosaxs.core.viewer import PLTViewer

    fig, ax = plt.subplots(figsize=figsize)
    PLTViewer.view_refined_curve(
        curve_calibrated,
        theoretical_peaks,
        fig_axs=(fig, np.array([ax])),
        show_duration=None,
    )
    _ensure_parent_dir(out_path)
    fig.savefig(out_path, dpi=dpi)
    plt.close(fig)


def save_smoothed_image_plot(J_smooth: np.ndarray, out_path: Path) -> None:
    """Save the NaN-safe Gaussian-smoothed log1p image."""
    fig, ax = plt.subplots(1, 1, figsize=(14, 10))
    im = ax.imshow(J_smooth, cmap="viridis", origin="lower")
    ax.set_title("Smoothed log1p(I) (NaN-safe Gaussian)")
    ax.set_xlabel("Pixel X")
    ax.set_ylabel("Pixel Y")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    _ensure_parent_dir(out_path)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def save_laplacian_plot(div: np.ndarray, out_path: Path) -> None:
    """Save Laplacian/divergence map with symmetric color scaling."""
    finite = np.isfinite(div)
    if np.any(finite):
        clim = float(np.quantile(np.abs(div[finite]), 0.995))
        clim = max(clim, 1e-15)
    else:
        clim = 1.0

    fig, ax = plt.subplots(1, 1, figsize=(14, 10))
    im = ax.imshow(div, cmap="RdBu_r", origin="lower", vmin=-clim, vmax=clim)
    ax.set_title("Laplacian/div (NaN-safe central 2nd differences)")
    ax.set_xlabel("Pixel X")
    ax.set_ylabel("Pixel Y")
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    _ensure_parent_dir(out_path)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def save_selected_sources_plot(
    J_bg: np.ndarray,
    sources_mask: np.ndarray,
    out_path: Path,
) -> None:
    """Save overlay of selected source pixels on the log image."""
    fig, ax = plt.subplots(1, 1, figsize=(14, 10))
    ax.imshow(J_bg, cmap="viridis", origin="lower")
    ys, xs = np.nonzero(sources_mask)
    if xs.size > 0:
        ax.scatter(xs, ys, s=3, c="r", alpha=0.7, linewidths=0)
    ax.set_title(f"Selected sources (count={int(xs.size)})")
    ax.set_xlabel("Pixel X")
    ax.set_ylabel("Pixel Y")
    fig.tight_layout()
    _ensure_parent_dir(out_path)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def save_dbscan_clusters_plot(
    J_bg: np.ndarray,
    points_xy: np.ndarray,
    labels: np.ndarray,
    out_path: Path,
) -> None:
    """Save DBSCAN clusters overlay (noise in lightgrey)."""
    fig, ax = plt.subplots(1, 1, figsize=(16, 12))
    ax.imshow(J_bg, cmap="viridis", origin="lower")

    if points_xy.size == 0 or labels.size == 0:
        ax.text(0.5, 0.5, "No DBSCAN points", ha="center", va="center")
        ax.set_axis_off()
        fig.tight_layout()
        _ensure_parent_dir(out_path)
        fig.savefig(out_path, dpi=120)
        plt.close(fig)
        return

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
            color = "lightgrey"
            ax.scatter(xs[mask], ys[mask], s=2, c=color, alpha=0.8, linewidths=0)
        else:
            color = id_to_color[lab_int]
            ax.scatter(xs[mask], ys[mask], s=2, c=[color], alpha=0.85, linewidths=0)

    ax.set_title(
        f"DBSCAN clusters (n_points={int(points_xy.shape[0])}, eps-based, noise=-1)"
    )
    ax.set_xlabel("Pixel X")
    ax.set_ylabel("Pixel Y")
    fig.tight_layout()
    _ensure_parent_dir(out_path)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def save_fitted_circles_plot(
    J_bg: np.ndarray,
    circles: List[Dict[str, float]],
    *,
    center_init_yx: Tuple[float, float],
    center_refined_yx: Tuple[float, float],
    out_path: Path,
) -> None:
    """Save fitted circles + initial and refined center markers."""
    center_init_y, center_init_x = center_init_yx
    center_ref_y, center_ref_x = center_refined_yx

    fig, ax = plt.subplots(1, 1, figsize=(16, 12))
    ax.imshow(J_bg, cmap="viridis", origin="lower")

    for c in circles:
        cx = float(c["center_x"])
        cy = float(c["center_y"])
        r = float(c["r_px"])
        if np.isfinite(r):
            ax.add_patch(
                Circle(
                    (cx, cy),
                    r,
                    fill=False,
                    edgecolor="magenta",
                    linewidth=1.2,
                    alpha=0.75,
                )
            )

    if np.isfinite(center_init_x) and np.isfinite(center_init_y):
        ax.scatter([center_init_x], [center_init_y], marker="x", s=140, c="yellow", linewidths=3)

    if np.isfinite(center_ref_x) and np.isfinite(center_ref_y):
        ax.scatter([center_ref_x], [center_ref_y], marker="+", s=240, c="cyan", linewidths=4)

    ax.set_title("Fitted circles and center refinement")
    ax.set_xlabel("Pixel X")
    ax.set_ylabel("Pixel Y")
    fig.tight_layout()
    _ensure_parent_dir(out_path)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


def save_rings_from_pixels_plot(
    J: np.ndarray,
    rings_pixels: np.ndarray,
    *,
    center_yx: Tuple[float, float],
    out_path: Path,
    plot_title: str = "Filtered rings + refined center",
) -> None:
    """Plot rings from a pixels array [y, x, ring_id]."""
    center_y, center_x = center_yx
    cx = float(center_x)
    cy = float(center_y)

    fig, ax = plt.subplots(1, 1, figsize=(16, 12))
    ax.imshow(J, cmap="viridis", origin="lower")

    rings_pixels = np.asarray(rings_pixels)
    if rings_pixels.size == 0 or rings_pixels.shape[0] == 0:
        ax.text(0.5, 0.5, "No rings after filtering", ha="center", va="center")
        ax.set_axis_off()
        _ensure_parent_dir(out_path)
        fig.savefig(out_path, dpi=120)
        plt.close(fig)
        return

    ring_ids = np.unique(rings_pixels[:, 2].astype(int))
    ring_ids_sorted = sorted(int(x) for x in ring_ids)
    cmap = plt.get_cmap("tab10")

    for i, ring_id in enumerate(ring_ids_sorted):
        mask = rings_pixels[:, 2].astype(int) == int(ring_id)
        pts_yx = rings_pixels[mask][:, :2].astype(np.float64, copy=False)
        ys = pts_yx[:, 0]
        xs = pts_yx[:, 1]
        r = np.hypot(xs - cx, ys - cy)
        r_in = float(np.min(r))
        r_out = float(np.max(r))
        color = cmap(ring_id % 10)

        if np.isfinite(r_out) and r_out > 0:
            ax.add_patch(
                Circle(
                    (cx, cy),
                    r_out,
                    fill=False,
                    edgecolor=color,
                    linewidth=2.0,
                    linestyle="-",
                    alpha=0.95,
                )
            )

        if np.isfinite(r_in) and r_in > 0 and r_in < r_out:
            ax.add_patch(
                Circle(
                    (cx, cy),
                    r_in,
                    fill=False,
                    edgecolor=color,
                    linewidth=1.6,
                    linestyle="--",
                    alpha=0.95,
                )
            )

        label_x = cx + r_out * 0.02
        label_y = cy + r_out * 0.02 + i * 8.0
        ax.text(
            label_x,
            label_y,
            f"ring {int(ring_id)} [{r_in:.1f}, {r_out:.1f}]",
            color=color,
            fontsize=9,
            bbox=dict(facecolor="black", alpha=0.25, edgecolor="none"),
        )

    ax.scatter([cx], [cy], marker="+", s=240, c="cyan", linewidths=4, alpha=0.95)
    ax.set_title(plot_title)
    ax.set_xlabel("Pixel X")
    ax.set_ylabel("Pixel Y")
    fig.tight_layout()
    _ensure_parent_dir(out_path)
    fig.savefig(out_path, dpi=120)
    plt.close(fig)


__all__ = [
    "save_refined_curve_with_theoretical_peaks",
    "save_smoothed_image_plot",
    "save_laplacian_plot",
    "save_selected_sources_plot",
    "save_dbscan_clusters_plot",
    "save_fitted_circles_plot",
    "save_rings_from_pixels_plot",
]

