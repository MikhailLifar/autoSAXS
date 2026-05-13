#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np

WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
REPOS_DIR = WORKSPACE_ROOT / "repos"
if str(REPOS_DIR) not in sys.path:
    sys.path.insert(0, str(REPOS_DIR))

from autosaxs.skill.calibrate.autocalib import autocalib_ring_analysis, ring_analysis  # noqa: E402,F401
from autosaxs.core.utils import read_from_tiff  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect ring source pixels via Laplacian/GMM on divergence.")
    parser.add_argument("--limit", type=int, default=10, help="Process at most N images (0 = all).")
    parser.add_argument(
        "--data-dir",
        type=str,
        default=str(WORKSPACE_ROOT / "data" / "AgBh"),
        help="Directory with *_AgBh<digits>*.tif calibration images. Default: data/AgBh.",
    )
    parser.add_argument("--gauss-sigma", type=float, default=25.0, help="Gaussian sigma (px). Default: 25.")
    parser.add_argument("--gmm-components", type=int, default=5, help="Number of GMM components. Default: 5.")
    parser.add_argument(
        "--gmm-prob-main-lt",
        type=float,
        default=0.01,
        help="Select pixels with posterior prob of main component < this value. Default: 0.01.",
    )
    parser.add_argument(
        "--gmm-max-samples",
        type=int,
        default=100000,
        help="Max samples used to fit the 1D GMM (subsample if larger). Default: 100000.",
    )
    parser.add_argument("--gmm-seed", type=int, default=0, help="Random seed for GMM subsampling/init. Default: 0.")
    parser.add_argument("--dbscan-eps", type=float, default=5.0, help="DBSCAN eps (px). Default: 5.0.")
    parser.add_argument(
        "--dbscan-min-samples", type=int, default=10, help="DBSCAN min_samples. Default: 10."
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
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
        ra_res = ring_analysis(
            img,
            gauss_sigma=args.gauss_sigma,
            div_gmm_components=args.gmm_components,
            div_gmm_prob_main_lt=args.gmm_prob_main_lt,
            div_gmm_max_samples=args.gmm_max_samples,
            div_gmm_seed=args.gmm_seed,
            dbscan_eps=args.dbscan_eps,
            dbscan_min_samples=args.dbscan_min_samples,
            circle_r2_min=0.5,
            global_refine_bounds_half_width_px=50.0,
            final_max_radius_px=500.0,
            final_skip_first_ring=True,
            final_keep_smallest_k=3,
            final_interval_overlap_tol_px=0.0,
            plots_out_dir=WORKSPACE_ROOT / "debug" / "ring_sources_gmm_cli",
            plot_stem=tif_path.stem,
        )

        center_y = float(ra_res["center_y_px"])
        center_x = float(ra_res["center_x_px"])
        rings_pixels = ra_res["rings_original_pixels"]
        ring_radii = ra_res["ring_radii_original_px"]

        n_rings = 0 if rings_pixels.size == 0 else int(np.unique(rings_pixels[:, 2].astype(int)).size)
        print(
            f"{tif_path.name}: rings={n_rings} refined_center_yx=({center_y:.2f},{center_x:.2f}) "
            f"final_ring_radii[r_out,r_in]_px={ring_radii!r}",
            flush=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["ring_analysis", "autocalib_ring_analysis"]
