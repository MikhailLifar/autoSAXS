from __future__ import annotations

"""
Autocalibration helpers for the ring-based (center-agnostic) pipeline.

Public API:
  - `ring_analysis(img_raw, ...)` -> (center_y_px, center_x_px), rings_pixels [y,x,ring_id]
  - `autocalib_ring_analysis(calibration_image_path, config, ...)` -> refine calibration
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import scipy.ndimage as ndi

from autosaxs.autocalib_viz import (
    save_dbscan_clusters_plot,
    save_fitted_circles_plot,
    save_laplacian_plot,
    save_rings_from_pixels_plot,
    save_selected_sources_plot,
    save_smoothed_image_plot,
)
from autosaxs.utils import fit_circle_xy_r2, read_from_tiff

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]

_RING_ANALYSIS_CALL_ID = 0


def _log1p_image(img: np.ndarray) -> np.ndarray:
    """Log transform over finite values; keep NaNs as NaNs."""
    img = np.asarray(img, dtype=float)
    finite = np.isfinite(img)
    if not np.any(finite):
        return np.full_like(img, np.nan, dtype=float)
    out = np.full_like(img, np.nan, dtype=float)
    out[finite] = np.log1p(img[finite])
    return out


def _nan_gaussian_filter(J: np.ndarray, *, sigma: float) -> Tuple[np.ndarray, np.ndarray]:
    """
    NaN-safe Gaussian filter by normalizing with a filtered validity mask.

    Returns:
        (J_smooth, ok_mask) where ok_mask marks pixels supported by at least one
        finite input neighborhood.
    """
    J = np.asarray(J, dtype=float)
    finite = np.isfinite(J)
    if not np.any(finite):
        out = np.full_like(J, np.nan, dtype=float)
        return out, np.zeros_like(J, dtype=bool)

    J0 = np.where(finite, J, 0.0)
    num = ndi.gaussian_filter(J0, sigma=sigma, mode="nearest")
    den = ndi.gaussian_filter(finite.astype(float), sigma=sigma, mode="nearest")

    out = np.full_like(J, np.nan, dtype=float)
    ok = den > 0
    out[ok] = num[ok] / den[ok]
    # Preserve original NaN pixels as NaN after filtering.
    out[~finite] = np.nan
    ok = ok & finite
    return out, ok


def estimate_ring_sources_divergence(
    img_raw: np.ndarray,
    *,
    gauss_sigma: float = 25.0,
    div_gmm_components: int = 5,
    div_gmm_prob_main_lt: float = 0.01,
    div_gmm_max_samples: int = 100000,
    div_gmm_seed: int = 0,
) -> Tuple[np.ndarray, dict]:
    """
    Center-agnostic "ring source" detection via Laplacian/div(grad) of smoothed log.

    Negative Laplacian values are treated as ring pixels by hypothesis.
    """
    if div_gmm_components < 2:
        raise ValueError("div_gmm_components must be >= 2")
    if div_gmm_max_samples < 1000:
        raise ValueError("div_gmm_max_samples should be reasonably large")
    if div_gmm_prob_main_lt <= 0.0 or div_gmm_prob_main_lt >= 1.0:
        raise ValueError("div_gmm_prob_main_lt must be in (0, 1)")

    J = _log1p_image(img_raw)
    J_smooth, _ok = _nan_gaussian_filter(J, sigma=gauss_sigma)

    # NaN-safe Laplacian: compute second derivatives only where neighbors are finite.
    H, W = J_smooth.shape
    div = np.full((H, W), np.nan, dtype=float)
    finite_s = np.isfinite(J_smooth)

    # d2/dx2 central second difference.
    d2x = np.full_like(J_smooth, np.nan, dtype=float)
    ok_x = finite_s[:, :-2] & finite_s[:, 1:-1] & finite_s[:, 2:]
    if np.any(ok_x):
        arr_x = J_smooth[:, 2:] - 2.0 * J_smooth[:, 1:-1] + J_smooth[:, :-2]
        d2x[:, 1:-1][ok_x] = arr_x[ok_x]

    # d2/dy2.
    d2y = np.full_like(J_smooth, np.nan, dtype=float)
    ok_y = finite_s[:-2, :] & finite_s[1:-1, :] & finite_s[2:, :]
    if np.any(ok_y):
        arr_y = J_smooth[2:, :] - 2.0 * J_smooth[1:-1, :] + J_smooth[:-2, :]
        d2y[1:-1, :][ok_y] = arr_y[ok_y]

    finite_dx = np.isfinite(d2x)
    finite_dy = np.isfinite(d2y)
    ok_div = finite_dx | finite_dy
    div[ok_div] = np.where(finite_dx, d2x, 0.0)[ok_div] + np.where(finite_dy, d2y, 0.0)[ok_div]

    finite = np.isfinite(div)
    if not np.any(finite):
        sources = np.zeros_like(div, dtype=bool)
        dbg = {
            "J": J,
            "J_smooth": J_smooth,
            "div": div,
            "med": float("nan"),
            "sigma_hat": float("nan"),
        }
        return sources, dbg

    vals = div[finite].astype(np.float64, copy=False)
    med = float(np.nanmedian(vals))
    mad = float(np.nanmedian(np.abs(vals - med)))
    sigma_hat = 1.4826 * mad
    if not np.isfinite(sigma_hat) or sigma_hat <= 1e-15:
        sigma_hat = 1e-15

    # Negative Laplacian corresponds to ring pixels.
    sign_mask = finite & (div < 0.0)

    dbg_extra: dict = {}
    from sklearn.mixture import GaussianMixture  # type: ignore

    z_all = (div - med) / sigma_hat
    z_fit = z_all[finite].reshape(-1, 1).astype(np.float64, copy=False)

    n = z_fit.shape[0]
    if n > div_gmm_max_samples:
        rng = np.random.default_rng(div_gmm_seed)
        idx = rng.choice(n, size=div_gmm_max_samples, replace=False)
        z_fit_sub = z_fit[idx]
    else:
        z_fit_sub = z_fit

    gmm = GaussianMixture(
        n_components=int(div_gmm_components),
        covariance_type="full",
        reg_covar=1e-6,
        max_iter=300,
        random_state=int(div_gmm_seed),
        n_init=3,
    )
    gmm.fit(z_fit_sub)

    weights = np.asarray(gmm.weights_, dtype=np.float64)
    main_idx = int(np.argmax(weights))
    dbg_extra["gmm_main_idx"] = main_idx
    dbg_extra["gmm_weights"] = weights
    dbg_extra["gmm_means"] = np.asarray(gmm.means_, dtype=np.float64).reshape(-1)

    # Responsibilities on finite pixels only.
    probs = gmm.predict_proba(z_fit)  # (n_finite, n_components)
    p_main = probs[:, main_idx]

    sources = np.zeros_like(div, dtype=bool)
    sources[finite] = sign_mask[finite] & (p_main < float(div_gmm_prob_main_lt))

    dbg_extra["div_gmm_prob_main_lt"] = float(div_gmm_prob_main_lt)
    dbg_extra["div_gmm_components"] = int(div_gmm_components)
    dbg_extra["div_gmm_max_samples"] = int(div_gmm_max_samples)
    dbg_extra["div_gmm_seed"] = int(div_gmm_seed)

    dbg = {
        "J": J,
        "J_smooth": J_smooth,
        "div": div,
        "med": med,
        "sigma_hat": sigma_hat,
        "gauss_sigma": gauss_sigma,
        **dbg_extra,
    }
    return sources, dbg


def _dbscan_clusters_from_sources(
    sources_mask: np.ndarray,
    *,
    eps_px: float = 5.0,
    min_samples: int = 10,
) -> Tuple[np.ndarray, np.ndarray]:
    """Run DBSCAN on selected pixel coordinates."""
    ys, xs = np.nonzero(sources_mask)
    points_xy = np.column_stack([xs, ys]).astype(np.float64, copy=False)
    if points_xy.shape[0] == 0:
        return points_xy, np.full((0,), -1, dtype=int)

    from sklearn.cluster import DBSCAN  # type: ignore

    dbscan = DBSCAN(eps=float(eps_px), min_samples=int(min_samples))
    labels = dbscan.fit_predict(points_xy)
    return points_xy, labels


def _fit_circles_from_dbscan(
    points_xy: np.ndarray,
    labels: np.ndarray,
    *,
    r2_min: float = 0.5,
    image_shape: Optional[Tuple[int, int]] = None,
) -> Tuple[List[Dict[str, float]], np.ndarray, Dict[int, float]]:
    """Fit a circle to each DBSCAN cluster and keep only good fits."""
    circles: List[Dict[str, float]] = []
    kept_labels: List[int] = []
    circle_r2_by_label: Dict[int, float] = {}

    if points_xy.shape[0] == 0:
        return circles, np.asarray(kept_labels, dtype=int), circle_r2_by_label

    for lab in np.unique(labels):
        if lab == -1:
            continue
        mask = labels == lab
        pts = points_xy[mask]
        # Fast filtering to avoid expensive fits on tiny clusters.
        if pts.shape[0] < 1000:
            continue
        if pts.shape[0] < 3:
            continue
        fit = fit_circle_xy_r2(pts, image_shape=image_shape)
        circle_r2 = float(fit["circle_r2"])
        circle_r2_by_label[int(lab)] = circle_r2
        if not np.isfinite(circle_r2) or circle_r2 < float(r2_min):
            continue
        circles.append(
            {
                "label": float(lab),
                "center_x": float(fit["center_x"]),
                "center_y": float(fit["center_y"]),
                "r_px": float(fit["r_px"]),
                "circle_r2": float(fit["circle_r2"]),
                "n_points": float(pts.shape[0]),
            }
        )
        kept_labels.append(int(lab))

    return circles, np.asarray(kept_labels, dtype=int), circle_r2_by_label


def _estimate_center_from_circles(circles: List[Dict[str, float]]) -> Tuple[float, float]:
    """Median of circle centers (y and x) across clusters."""
    if not circles:
        return float("nan"), float("nan")
    cx = np.asarray([c["center_x"] for c in circles], dtype=float)
    cy = np.asarray([c["center_y"] for c in circles], dtype=float)
    return float(np.median(cy)), float(np.median(cx))


def _global_refine_center_from_clusters(
    points_xy: np.ndarray,
    labels: np.ndarray,
    kept_labels: np.ndarray,
    *,
    init_center_yx: Tuple[float, float],
    bounds_half_width_px: float = 50.0,
) -> Tuple[float, float]:
    """
    Global refinement of center.

    Objective:
        sum_{c in kept_clusters} (max_{p in c} dist(p, center) - min_{p in c} dist(p, center))
    """
    init_center_y, init_center_x = init_center_yx
    if not np.isfinite(init_center_y) or not np.isfinite(init_center_x):
        return init_center_y, init_center_x
    if kept_labels.size == 0:
        return init_center_y, init_center_x

    clusters = [int(l) for l in kept_labels.tolist()]
    # Pre-extract points per cluster label for speed.
    pts_by_label: Dict[int, np.ndarray] = {}
    for lab in clusters:
        m = labels == lab
        pts = points_xy[m]
        if pts.shape[0] >= 1:
            pts_by_label[lab] = pts
    if not pts_by_label:
        return init_center_y, init_center_x

    cx0 = float(init_center_x)
    cy0 = float(init_center_y)
    bounds = [
        (cx0 - float(bounds_half_width_px), cx0 + float(bounds_half_width_px)),
        (cy0 - float(bounds_half_width_px), cy0 + float(bounds_half_width_px)),
    ]
    x0 = np.asarray([cx0, cy0], dtype=np.float64)

    def objective(cxcy: np.ndarray) -> float:
        cx = float(cxcy[0])
        cy = float(cxcy[1])
        total = 0.0
        for pts in pts_by_label.values():
            r = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
            if r.size == 0:
                continue
            total += float(np.max(r) - np.min(r))
        return float(total)

    from scipy.optimize import minimize  # type: ignore

    res = minimize(
        objective,
        x0,
        method="L-BFGS-B",
        bounds=bounds,
        options={"maxiter": 300},
    )
    if res.success and np.all(np.isfinite(res.x)) and res.x.shape == (2,):
        cx_ref = float(res.x[0])
        cy_ref = float(res.x[1])
        return cy_ref, cx_ref

    return init_center_y, init_center_x


def _compute_cluster_radial_intervals(
    points_xy: np.ndarray,
    labels: np.ndarray,
    kept_labels: np.ndarray,
    *,
    refined_center_yx: Tuple[float, float],
) -> List[Dict[str, float]]:
    """For each kept DBSCAN cluster compute [r_in, r_out] relative to refined center."""
    refined_center_y, refined_center_x = refined_center_yx
    cx = float(refined_center_x)
    cy = float(refined_center_y)

    intervals: List[Dict[str, float]] = []
    for lab in kept_labels.tolist():
        lab_int = int(lab)
        if lab_int == -1:
            continue
        mask = labels == lab_int
        pts = points_xy[mask]
        if pts.shape[0] == 0:
            continue
        r = np.hypot(pts[:, 0] - cx, pts[:, 1] - cy)
        if r.size == 0:
            continue
        r_in = float(np.min(r))
        r_out = float(np.max(r))
        if r_out < r_in:
            r_in, r_out = r_out, r_in
        intervals.append(
            {
                "label": float(lab_int),
                "r_in": r_in,
                "r_out": r_out,
                "n_points": float(pts.shape[0]),
            }
        )
    return intervals


def ring_analysis(
    img_raw: np.ndarray,
    *,
    gauss_sigma: float = 25.0,
    div_gmm_components: int = 5,
    div_gmm_prob_main_lt: float = 0.01,
    div_gmm_max_samples: int = 100000,
    div_gmm_seed: int = 0,
    dbscan_eps: float = 5.0,
    dbscan_min_samples: int = 10,
    circle_r2_min: float = 0.5,
    global_refine_bounds_half_width_px: float = 50.0,
    final_max_radius_px: float = 500.0,
    final_skip_first_ring: bool = True,
    final_keep_first_ring_if_d2_lt_px: float = 40.0,
    final_keep_smallest_k: int = 3,
    final_interval_overlap_tol_px: float = 0.0,
    make_plots: bool = True,
    plots_out_dir: Optional[Path] = None,
    plot_stem: Optional[str] = None,
) -> Tuple[Tuple[float, float], np.ndarray]:
    """
    Center + ring pixel extraction (center-agnostic) followed by final ring filtering.

    Returns:
        ((center_y_px, center_x_px), rings)

    rings format:
      (N, 3) array with columns [y_px, x_px, ring_id]
    """
    global _RING_ANALYSIS_CALL_ID
    _RING_ANALYSIS_CALL_ID += 1
    run_id = int(_RING_ANALYSIS_CALL_ID)

    out_path_rings: Optional[Path] = None
    out_path_smoothed: Optional[Path] = None
    out_path_laplacian: Optional[Path] = None
    out_path_selected: Optional[Path] = None
    out_path_clusters: Optional[Path] = None
    out_path_circles: Optional[Path] = None

    if make_plots:
        out_dir_overlay = plots_out_dir or (WORKSPACE_ROOT / "debug" / "debug_ring_sources_divergence")
        prefix = f"{plot_stem}_" if plot_stem else ""
        out_path_rings = out_dir_overlay / f"{prefix}ring_analysis_{run_id:04d}_rings_filtered.png"
        out_path_smoothed = out_dir_overlay / f"{prefix}ring_analysis_{run_id:04d}_smoothed.png"
        out_path_laplacian = out_dir_overlay / f"{prefix}ring_analysis_{run_id:04d}_laplacian.png"
        out_path_selected = out_dir_overlay / f"{prefix}ring_analysis_{run_id:04d}_selected.png"
        out_path_clusters = out_dir_overlay / f"{prefix}ring_analysis_{run_id:04d}_dbscan_clusters.png"
        out_path_circles = out_dir_overlay / f"{prefix}ring_analysis_{run_id:04d}_fitted_circles.png"

    # Background for plots: log1p of intensity.
    J_bg = _log1p_image(img_raw)

    sources_mask, dbg_div = estimate_ring_sources_divergence(
        img_raw,
        gauss_sigma=gauss_sigma,
        div_gmm_components=div_gmm_components,
        div_gmm_prob_main_lt=div_gmm_prob_main_lt,
        div_gmm_max_samples=div_gmm_max_samples,
        div_gmm_seed=div_gmm_seed,
    )

    if make_plots:
        assert out_path_smoothed is not None
        assert out_path_laplacian is not None
        assert out_path_selected is not None
        save_smoothed_image_plot(dbg_div["J_smooth"], out_path_smoothed)
        save_laplacian_plot(dbg_div["div"], out_path_laplacian)
        save_selected_sources_plot(J_bg, sources_mask, out_path_selected)

    J_shape: Tuple[int, int] = (int(img_raw.shape[0]), int(img_raw.shape[1]))

    # DBSCAN segmentation of selected source pixels.
    points_xy, labels = _dbscan_clusters_from_sources(
        sources_mask,
        eps_px=dbscan_eps,
        min_samples=dbscan_min_samples,
    )

    if make_plots:
        assert out_path_clusters is not None
        save_dbscan_clusters_plot(J_bg, points_xy, labels, out_path_clusters)

    # Circle fitting + R2 filtering for clusters.
    circles, kept_labels, _circle_r2_by_label = _fit_circles_from_dbscan(
        points_xy,
        labels,
        r2_min=circle_r2_min,
        image_shape=J_shape,
    )

    center_y_median, center_x_median = _estimate_center_from_circles(circles)
    center_y_refined, center_x_refined = _global_refine_center_from_clusters(
        points_xy,
        labels,
        kept_labels,
        init_center_yx=(center_y_median, center_x_median),
        bounds_half_width_px=global_refine_bounds_half_width_px,
    )

    if make_plots:
        assert out_path_circles is not None
        save_fitted_circles_plot(
            J_bg,
            circles,
            center_init_yx=(center_y_median, center_x_median),
            center_refined_yx=(center_y_refined, center_x_refined),
            out_path=out_path_circles,
        )

    # If we don't have enough accepted clusters, return empty rings.
    if kept_labels.size == 0 or not (np.isfinite(center_y_refined) and np.isfinite(center_x_refined)):
        rings_out = np.zeros((0, 3), dtype=int)
        if make_plots:
            assert out_path_rings is not None
            save_rings_from_pixels_plot(
                J_bg,
                rings_out,
                center_yx=(center_y_refined, center_x_refined),
                out_path=out_path_rings,
            )
        return (center_y_refined, center_x_refined), rings_out

    # Compute per-cluster radial intervals [r_in, r_out] relative to refined center.
    cluster_intervals = _compute_cluster_radial_intervals(
        points_xy,
        labels,
        kept_labels,
        refined_center_yx=(center_y_refined, center_x_refined),
    )
    if not cluster_intervals:
        rings_out = np.zeros((0, 3), dtype=int)
        if make_plots:
            assert out_path_rings is not None
            save_rings_from_pixels_plot(
                J_bg,
                rings_out,
                center_yx=(center_y_refined, center_x_refined),
                out_path=out_path_rings,
            )
        return (center_y_refined, center_x_refined), rings_out

    # Merge intervals into ring groups using overlap: next.r_in <= cur.r_out (+ tolerance).
    cluster_intervals_sorted = sorted(cluster_intervals, key=lambda d: float(d["r_in"]))
    ring_groups: List[Dict[str, object]] = []

    cur_r_in = float(cluster_intervals_sorted[0]["r_in"])
    cur_r_out = float(cluster_intervals_sorted[0]["r_out"])
    cur_cluster_labels: List[int] = [int(cluster_intervals_sorted[0]["label"])]

    for it in cluster_intervals_sorted[1:]:
        r_in = float(it["r_in"])
        r_out = float(it["r_out"])
        lab = int(it["label"])
        if r_in <= cur_r_out + float(final_interval_overlap_tol_px):
            cur_r_in = float(min(cur_r_in, r_in))
            cur_r_out = float(max(cur_r_out, r_out))
            cur_cluster_labels.append(lab)
        else:
            ring_groups.append(
                {
                    "r_in": cur_r_in,
                    "r_out": cur_r_out,
                    "r_mean": 0.5 * (cur_r_in + cur_r_out),
                    "cluster_labels": cur_cluster_labels,
                }
            )
            cur_r_in = r_in
            cur_r_out = r_out
            cur_cluster_labels = [lab]

    ring_groups.append(
        {
            "r_in": cur_r_in,
            "r_out": cur_r_out,
            "r_mean": 0.5 * (cur_r_in + cur_r_out),
            "cluster_labels": cur_cluster_labels,
        }
    )

    # Final ring filtering rules:
    # 1) Usually drop the innermost ring (smallest r_mean), except when >=3 rings are detected
    #    and d2 = r_mean_2 - r_mean_1 < final_keep_first_ring_if_d2_lt_px (keep first ring).
    # 2) Filter out rings with r_mean >= final_max_radius_px
    # 3) Keep at most final_keep_smallest_k smallest rings after the above
    ring_groups_sorted = sorted(ring_groups, key=lambda g: float(g["r_mean"]))  # type: ignore[arg-type]
    skip_first = bool(final_skip_first_ring)
    if skip_first and len(ring_groups_sorted) >= 3:
        r_mean_1 = float(ring_groups_sorted[0]["r_mean"])  # smallest
        r_mean_2 = float(ring_groups_sorted[1]["r_mean"])  # second smallest
        d2 = r_mean_2 - r_mean_1
        if d2 < float(final_keep_first_ring_if_d2_lt_px):
            skip_first = False
    if skip_first and len(ring_groups_sorted) > 0:
        ring_groups_sorted = ring_groups_sorted[1:]

    ring_groups_sorted = [
        g for g in ring_groups_sorted if float(g["r_mean"]) < float(final_max_radius_px)  # type: ignore[arg-type]
    ]

    if final_keep_smallest_k is not None and final_keep_smallest_k > 0:
        ring_groups_sorted = ring_groups_sorted[: int(final_keep_smallest_k)]

    if not ring_groups_sorted:
        rings_out = np.zeros((0, 3), dtype=int)
        if make_plots:
            assert out_path_rings is not None
            save_rings_from_pixels_plot(
                J_bg,
                rings_out,
                center_yx=(center_y_refined, center_x_refined),
                out_path=out_path_rings,
            )
        return (center_y_refined, center_x_refined), rings_out

    # Build rings pixel array: [y_px, x_px, ring_id]
    rings_rows: List[np.ndarray] = []
    for ring_id, g in enumerate(ring_groups_sorted):
        cluster_labels = g["cluster_labels"]  # type: ignore[assignment]
        if not isinstance(cluster_labels, list) or len(cluster_labels) == 0:
            continue
        keep_mask = np.isin(labels, np.asarray(cluster_labels, dtype=int))
        ring_pts_xy = points_xy[keep_mask]
        if ring_pts_xy.shape[0] == 0:
            continue
        ring_yx = np.column_stack(
            [ring_pts_xy[:, 1].astype(int, copy=False), ring_pts_xy[:, 0].astype(int, copy=False)]
        )
        ring_id_col = np.full((ring_yx.shape[0], 1), int(ring_id), dtype=int)
        rings_rows.append(np.hstack([ring_yx, ring_id_col]))

    if not rings_rows:
        rings_out = np.zeros((0, 3), dtype=int)
    else:
        rings_out = np.vstack(rings_rows).astype(int, copy=False)

    if make_plots:
        assert out_path_rings is not None
        save_rings_from_pixels_plot(
            J_bg,
            rings_out,
            center_yx=(center_y_refined, center_x_refined),
            out_path=out_path_rings,
        )

    return (float(center_y_refined), float(center_x_refined)), rings_out


def autocalib_ring_analysis(
    calibration_image_path: str,
    config: dict,
    mask_path: Optional[str] = None,
    *,
    plots_out_dir: Optional[Path] = None,
    plot_stem: Optional[str] = None,
    calibration_curve_plot_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Autocalib variant which uses `ring_analysis()` for initial center + ring pixels,
    and then runs the original `autosaxs.processor.refine()` geometry refinement.
    """
    from autosaxs.autocalib_viz import save_refined_curve_with_theoretical_peaks
    from autosaxs.processor import get_r_beam_px, refine

    calib_data = read_from_tiff(calibration_image_path)

    cfg_ra_kwargs = config.get("ring_analysis", {})
    ra_kwargs: dict = dict(cfg_ra_kwargs) if isinstance(cfg_ra_kwargs, dict) else {}
    (center_y_px, center_x_px), rings_pixels = ring_analysis(
        calib_data,
        plots_out_dir=plots_out_dir,
        plot_stem=plot_stem,
        **ra_kwargs,
    )

    center_y_px = float(center_y_px)
    center_x_px = float(center_x_px)

    if rings_pixels.size == 0 or not (np.isfinite(center_y_px) and np.isfinite(center_x_px)):
        raise RuntimeError("ring_analysis produced empty rings or invalid center")

    r_beam_px = get_r_beam_px(calib_data, center_y_px, center_x_px)
    if r_beam_px is None:
        r_beam_px = float(config.get("r_beam_px", 35.0))

    d_geom = config["detector_geometry"]
    geometry_params = {k: d_geom[k] for k in ["dist", "wavelength", "pixel_size", "rot1", "rot2", "rot3"]}
    geometry_params.update(
        {
            "r_beam_px": float(r_beam_px),
            "center_y_px": center_y_px,
            "center_x_px": center_x_px,
            "calibrant_name": config["calibrant_name"],
            "mask_path": mask_path,
            "mask_config": config.get("mask_config", {"mode": "auto"}),
        }
    )

    refine_step_ret = refine(calib_data, rings_pixels, **geometry_params)
    refined = refine_step_ret["refined"].copy()
    refined["wavelength"] = float(d_geom["wavelength"])

    if calibration_curve_plot_path is not None:
        save_refined_curve_with_theoretical_peaks(
            refine_step_ret["curve_calibrated"],
            refine_step_ret["theoretical_peaks"],
            calibration_curve_plot_path,
        )

    return {
        "refined": refined,
        "integrator": refine_step_ret["integrator"],
        "calib_data": calib_data,
        "center_y_px": center_y_px,
        "center_x_px": center_x_px,
        "clusters": None,
        "rings": rings_pixels,
        "curve_calibrated": refine_step_ret["curve_calibrated"],
        "theoretical_peaks": refine_step_ret["theoretical_peaks"],
    }


__all__ = [
    "ring_analysis",
    "autocalib_ring_analysis",
    "estimate_ring_sources_divergence",
]

