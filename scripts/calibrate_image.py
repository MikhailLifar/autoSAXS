#!/usr/bin/env python3
"""
Apply the autosaxs 'calibrate' skill to a single calibration TIFF image.

Example:
  /home/mikl/.conda/envs/LLMAssistant/bin/python repos/scripts/calibrate_image.py
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    # This file lives in <root>/repos/scripts/
    return Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run autosaxs calibrate skill on one TIFF image.")
    parser.add_argument(
        "--image",
        default=str(_repo_root() / "data/260317_calib/img_10(2).tif"),
        help="Path to calibration TIFF image.",
    )
    parser.add_argument(
        "--config",
        default=str(_repo_root() / "debug/data/config_base.conf"),
        help="Path to calibration config (YAML).",
    )
    parser.add_argument(
        "--mask",
        default="",
        help="Optional path to mask file.",
    )
    parser.add_argument(
        "--out",
        default=str(_repo_root() / "data/260317_calib/out_calibrate_img_10_2"),
        help="Output directory for calibration results.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable skill caching.",
    )
    args = parser.parse_args()

    # Headless-safe plotting
    import matplotlib

    matplotlib.use("Agg")

    repo_root = _repo_root()
    repos_dir = repo_root / "repos"
    if str(repos_dir) not in sys.path:
        sys.path.insert(0, str(repos_dir))

    image_path = os.path.abspath(args.image)
    config_path = os.path.abspath(args.config)
    mask_path = os.path.abspath(args.mask) if args.mask else ""
    out_dir = os.path.abspath(args.out)

    if not os.path.isfile(image_path):
        raise FileNotFoundError(f"Calibration image not found: {image_path}")
    if not os.path.isfile(config_path):
        raise FileNotFoundError(f"Config file not found: {config_path}")
    if mask_path and not os.path.isfile(mask_path):
        raise FileNotFoundError(f"Mask file not found: {mask_path}")

    from autosaxs.skill import calibrate

    result = calibrate(
        image_path,
        config_path,
        out_dir,
        mask=mask_path or None,
        use_cache=not args.no_cache,
    )

    print("Calibration finished. Outputs:")
    for k, v in result.items():
        print(f"- {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

