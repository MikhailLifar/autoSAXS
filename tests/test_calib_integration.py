"""
Validation test for calibration + integration: run pipeline on validation data,
compare integrated 1D curves to reference .chi files, plot and compute metric.

Prerequisites:
  - Run scripts/setup_validation_data.py once to create validation/ and copy/rename data.
  - validation/ must contain raw/*_calib.tif, raw/*_buffer.tif, raw/*_sample.tif,
    reference/*.chi, and config.conf. Place a mask file (e.g. mask*.msk) in validation/
    to use it for calibration (default mask_choice='f').

Metric: int_{q0}^{qmax} 2 * |I1(q) - I2(q)| / (|I1(q)|*|I2(q)| + eps)
"""
import os
import sys
import glob
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Add repos to path when running as script
_REPOS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPOS not in sys.path:
    sys.path.insert(0, _REPOS)

from autosaxs import api
from autosaxs.utils import read_saxs, read_chi, integration_comparison_metric

WORKSPACE_ROOT = os.path.abspath(os.path.join(_REPOS, ".."))
VALIDATION_DIR = os.path.join(WORKSPACE_ROOT, "validation")
AVERAGED_DIR = os.path.join(VALIDATION_DIR, "averaged")
REFERENCE_DIR = os.path.join(VALIDATION_DIR, "reference")
OUTPUT_DIR = os.path.join(VALIDATION_DIR, "validation_plots")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def _int_dat_to_ref_basename(dat_basename: str) -> str:
    """int_0002_ihs27_95.9_sample.dat -> 0002_ihs27_95.9 for reference 0002_ihs27_95.9.chi."""
    name = dat_basename
    if name.startswith("int_"):
        name = name[4:]
    for suffix in ("_sample", "_buffer"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def run_calibration_integration(mask_choice="f"):
    """Run pipeline with only calibration and integration steps.
    mask_choice: 'f' = from file (use validation/mask*), 'c' = combine with automask, 'a' = automask only.
    """
    api.fast_first_processing(
        VALIDATION_DIR,
        steps=["calibration", "integration"],
        mask_choice=mask_choice,
    )


def compare_and_plot():
    """
    For each averaged/int_*.dat find the reference .chi, compute metric, plot comparison,
    save figure with metric in title and as prefix in filename.
    """
    int_pattern = os.path.join(AVERAGED_DIR, "int_*.dat")
    int_files = sorted(glob.glob(int_pattern))
    if not int_files:
        raise FileNotFoundError(
            f"No integrated files found: {int_pattern}. Run calibration+integration first."
        )

    results = []
    for int_path in int_files:
        base = os.path.splitext(os.path.basename(int_path))[0]
        ref_base = _int_dat_to_ref_basename(base)
        ref_path = os.path.join(REFERENCE_DIR, ref_base + ".chi")
        if not os.path.isfile(ref_path):
            continue

        q_pipe, I_pipe, _, _ = read_saxs(int_path)
        q_ref, I_ref = read_chi(ref_path)

        metric = integration_comparison_metric(q_pipe, I_pipe, q_ref, I_ref)
        results.append((base, ref_base, metric, q_pipe, I_pipe, q_ref, I_ref))

        # Plot
        fig, ax = plt.subplots()
        ax.plot(q_ref, I_ref, label="reference (.chi)", alpha=0.8)
        ax.plot(q_pipe, I_pipe, label="pipeline (int)", alpha=0.8)
        ax.set_xlabel("q")
        ax.set_ylabel("I")
        ax.set_title(f"metric = {metric:.6f}\n{base}")
        ax.legend()
        ax.set_yscale("log")
        fig.tight_layout()
        safe_metric_str = f"{metric:.4f}".replace(".", "_")
        out_name = f"{safe_metric_str}_{base}.png"
        out_path = os.path.join(OUTPUT_DIR, out_name)
        fig.savefig(out_path, dpi=150)
        plt.close(fig)

    return results


def test_calib_integration_validation():
    """Pytest entry: run pipeline (calib+integration) and compare to reference .chi."""
    if not os.path.isdir(VALIDATION_DIR):
        raise FileNotFoundError(
            f"Validation directory not found: {VALIDATION_DIR}. "
            "Run: python repos/scripts/setup_validation_data.py"
        )
    run_calibration_integration()
    results = compare_and_plot()
    assert len(results) > 0, "No pipeline outputs could be matched to reference .chi files"
    # Optional: assert metric below a threshold, e.g. assert all(r[2] < 1.0 for r in results)


if __name__ == "__main__":
    if not os.path.isdir(VALIDATION_DIR):
        print("Running setup_validation_data.py first...")
        sys.path.insert(0, _REPOS)
        from scripts.setup_validation_data import main as setup_main
        setup_main()
    run_calibration_integration()
    results = compare_and_plot()
    print(f"Compared {len(results)} curves. Plots saved under {OUTPUT_DIR}")
    for base, ref_base, metric, *_ in results:
        print(f"  {base} vs {ref_base}.chi  metric = {metric:.6f}")
