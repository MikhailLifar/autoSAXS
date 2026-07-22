#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import gc
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

WORKSPACE_ROOT = Path("/home/mikl/KurchatovCoop")
PKG_SRC = WORKSPACE_ROOT / "autosaxs" / "src"
sys.path.insert(0, str(PKG_SRC))

from autosaxs.core.utils import load_config  # noqa: E402
from autosaxs.skill.calibrate.autocalib import autocalib_ring_analysis  # noqa: E402


def _safe_float(x: Any) -> Optional[float]:
    try:
        v = float(x)
        if v != v:
            return None
        return v
    except Exception:
        return None


def _extract_refined_summary(res: Dict[str, Any]) -> Dict[str, Any]:
    refined = res.get("refined") or {}
    return {
        "dist_m": _safe_float(refined.get("dist")),
        "poni1_m": _safe_float(refined.get("poni1")),
        "poni2_m": _safe_float(refined.get("poni2")),
        "rot1_rad": _safe_float(refined.get("rot1")),
        "rot2_rad": _safe_float(refined.get("rot2")),
        "rot3_rad": _safe_float(refined.get("rot3")),
    }


def _iter_matching_tifs(data_dir: Path, regex: re.Pattern[str], limit: int) -> List[Path]:
    all_tifs = sorted(data_dir.glob("*.tif"))
    matched = [p for p in all_tifs if regex.match(p.name)]
    if limit and limit > 0:
        matched = matched[:limit]
    return matched


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark ring_analysis-driven autocalib.",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=str(WORKSPACE_ROOT / "data" / "AgBh"),
        help="Directory with TIFF calibrant images (default: data/AgBh).",
    )
    parser.add_argument(
        "--regex",
        type=str,
        default=r".*\.tif$",
        help=r"Regex to match TIFFs by filename (default: .*\\.tif$).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N images (0 = all).",
    )
    parser.add_argument(
        "--config-path",
        type=str,
        required=True,
        help="Path to autosaxs calibration YAML/CONF config.",
    )
    parser.add_argument(
        "--mask-path",
        type=str,
        default="",
        help="Optional mask path (required only for mask_config.mode in {from_file,combined}).",
    )
    parser.add_argument(
        "--out-csv",
        type=str,
        default="",
        help="Optional CSV output path (e.g. debug/bench_results.csv).",
    )
    parser.add_argument(
        "--out-dir-ring",
        type=str,
        default=str(WORKSPACE_ROOT / "debug" / "bench_autocalib_ring_analysis"),
        help="Directory where ring-analysis autocalib saves plots/refined.yml.",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        raise NotADirectoryError(str(data_dir))

    regex = re.compile(args.regex, re.IGNORECASE)
    tifs = _iter_matching_tifs(data_dir, regex, args.limit)
    if not tifs:
        raise FileNotFoundError(f"No matching .tif files found in: {data_dir}")

    cfg = load_config(args.config_path)
    mask_path = args.mask_path.strip() if args.mask_path else None
    out_dir_ring = Path(args.out_dir_ring)
    out_dir_ring.mkdir(parents=True, exist_ok=True)

    results: List[Dict[str, Any]] = []

    for tif_path in tifs:
        stem = tif_path.stem
        row: Dict[str, Any] = {"file": tif_path.name}

        t0 = time.perf_counter()
        try:
            res2 = autocalib_ring_analysis(
                str(tif_path),
                cfg,
                mask_path=mask_path,
                plots_out_dir=out_dir_ring,
                plot_stem=stem,
                calibration_curve_plot_path=(out_dir_ring / f"{stem}_ring_calibration_curve.png"),
            )
            t1 = time.perf_counter()
            row["ring_ok"] = True
            row["ring_time_s"] = float(t1 - t0)
            row["ring_center_y_px"] = _safe_float(res2.get("center_y_px"))
            row["ring_center_x_px"] = _safe_float(res2.get("center_x_px"))
            ring_refined = _extract_refined_summary(res2)
            for k, v in ring_refined.items():
                row[f"ring_{k}"] = v
            rings_arr = res2.get("rings")
            row["ring_rings_count"] = int(rings_arr.shape[0]) if rings_arr is not None else None
            row["ring_rings_nonzero"] = bool(rings_arr is not None and getattr(rings_arr, "size", 0) > 0)
            row["ring_initial_dist_guess_m"] = _safe_float(res2.get("initial_dist_guess_m"))
            row["ring_initial_k_m_per_px"] = _safe_float(res2.get("initial_dist_guess_k_m_per_px"))

            ring_refined_path = out_dir_ring / f"{stem}_ring_refined.yml"
            with ring_refined_path.open("w", encoding="utf-8") as f:
                yaml.safe_dump(res2.get("refined", {}), f)
        except Exception as e:
            t1 = time.perf_counter()
            row["ring_ok"] = False
            row["ring_time_s"] = float(t1 - t0)
            row["ring_error"] = str(e)
        finally:
            if "res2" in locals():
                del res2
            gc.collect()

        print(row, flush=True)
        results.append(row)

    if args.out_csv:
        out_path = Path(args.out_csv)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames: List[str] = []
        for r in results:
            for k in r.keys():
                if k not in fieldnames:
                    fieldnames.append(k)
        with out_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(results)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
