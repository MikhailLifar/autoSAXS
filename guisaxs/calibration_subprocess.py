"""
Calibration subprocess runner.

This module is executed in a separate process to avoid GUI deadlocks caused by
NumPy/pyFAI/BLAS threading interactions. It writes a status JSON file (optional)
and a result JSON file that the GUI reads after completion.

Usage:
    python -m guisaxs.calibration_subprocess <config_json> <output_dir> [--status-file <path>]

Where config_json contains:
    - calibrant_path: Path to calibrant image
    - mask_path: Optional path to mask file
    - config_path: Path to YAML calibration config (autosaxs format)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from typing import Any, Dict, Optional

import yaml

# Set threading environment variables BEFORE importing NumPy/SciPy/pyFAI
_THREADING_ENV_VARS = [
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
    "TBB_NUM_THREADS",
    "NUMBA_NUM_THREADS",
]

try:
    import multiprocessing

    _NUM_CORES = multiprocessing.cpu_count()
    _OPTIMAL_THREADS = str(_NUM_CORES)
except (ImportError, AttributeError):
    _OPTIMAL_THREADS = "4"

for _var in _THREADING_ENV_VARS:
    os.environ[_var] = _OPTIMAL_THREADS

print(f"Calibration subprocess: Using {_OPTIMAL_THREADS} threads for computation", file=sys.stderr)

from autosaxs.skill.calibrate import calibrate  # noqa: E402
from autosaxs.utils import load_config  # noqa: E402


STATUS_FILE: Optional[str] = None
OUTPUT_DIR: Optional[str] = None


def _write_status(message: str, status_type: str = "info") -> None:
    """Write status message to file for GUI to read."""
    if not STATUS_FILE:
        return
    try:
        with open(STATUS_FILE, "w") as f:
            json.dump(
                {
                    "message": message,
                    "type": status_type,
                    "timestamp": os.path.getmtime(__file__) if os.path.exists(__file__) else 0,
                },
                f,
            )
    except Exception as e:
        print(f"Error writing status: {e}", file=sys.stderr)


def main(argv: Optional[list[str]] = None) -> int:
    global STATUS_FILE, OUTPUT_DIR

    parser = argparse.ArgumentParser(description="Calibration subprocess runner (guisaxs)")
    parser.add_argument("config_file", help="Path to JSON config file")
    parser.add_argument("output_dir", help="Directory to save results")
    parser.add_argument("--status-file", help="Path to status file for progress updates")

    args = parser.parse_args(argv)

    STATUS_FILE = args.status_file
    OUTPUT_DIR = args.output_dir

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Load configuration
    try:
        with open(args.config_file, "r") as f:
            config_data: Dict[str, Any] = json.load(f)
    except Exception as e:
        _write_status(f"Error loading config: {str(e)}", "error")
        print(f"Error loading config: {e}", file=sys.stderr)
        return 1

    calibrant_path = config_data.get("calibrant_path")
    mask_path = config_data.get("mask_path")
    config_path = config_data.get("config_path")

    if not calibrant_path or not config_path:
        _write_status("Missing required parameters: calibrant_path or config_path", "error")
        print("Error: Missing required parameters", file=sys.stderr)
        return 1

    # Run calibration
    try:
        _write_status("Calibration: starting", "progress")

        # Load config to pick calibrant default if not explicitly present.
        cfg = load_config(str(config_path))
        calibrant_name = cfg.get("calibrant_name", "AgBh")

        _write_status("Calibration: ring analysis and geometry refinement (this may take a while)", "progress")

        out = calibrate(
            calib_image=str(calibrant_path),
            config_path=str(config_path),
            output_dir=str(OUTPUT_DIR),
            mask=str(mask_path) if mask_path else None,
            mask_mode="f" if mask_path else "a",
            calibrant=str(calibrant_name),
            use_cache=True,
        )

        refined_path = out.get("refined_path")
        integrator_dir = out.get("integrator_dir")

        calibrated_params: Dict[str, Any] = {}
        if refined_path and os.path.exists(str(refined_path)):
            with open(str(refined_path), "r") as f:
                calibrated_params = yaml.safe_load(f) or {}

        # Save calibrated parameters + key output paths for GUI
        result_file = os.path.join(OUTPUT_DIR, "calibration_result.json")
        result_data = {
            "calibrated_params": calibrated_params,
            "integrator_dir": integrator_dir,
            "refined_path": refined_path,
            "status": "success",
        }
        with open(result_file, "w") as f:
            json.dump(result_data, f, indent=2)

        _write_status("Calibration complete", "success")
        return 0

    except Exception as e:
        error_msg = f"Calibration failed: {str(e)}"
        _write_status(error_msg, "error")
        print(error_msg, file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

        # Save error to result file
        result_file = os.path.join(OUTPUT_DIR, "calibration_result.json")
        result_data = {"status": "error", "error": str(e)}
        try:
            with open(result_file, "w") as f:
                json.dump(result_data, f, indent=2)
        except Exception:
            pass

        return 1


if __name__ == "__main__":
    raise SystemExit(main())

