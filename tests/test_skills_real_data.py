"""
Validation test for calibration + integration + subtraction: run pipeline on validation data,
compare integrated 1D curves to reference .chi, subtracted curves to reference sub_*.dat; plot and compute metric.

Prerequisites:
  - Run scripts/setup_validation_data.py once to create validation/ and copy/rename data.
  - validation/ must contain raw/*_calib.tif, raw/*_buffer.tif, raw/*_sample.tif,
    reference/*.chi, reference_subtracted/sub_*.dat, and config.conf. Place a mask file (e.g. mask*.msk) in validation/.

Metric: int_{q0}^{qmax} 2 * |I1(q) - I2(q)| / (|I1(q)|*|I2(q)| + eps)
"""
import os
import sys
import glob
import re
import csv
import shutil
from typing import Any, cast
import pytest
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Add repos to path when running as script
_REPOS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPOS not in sys.path:
    sys.path.insert(0, _REPOS)

from autosaxs import api
from autosaxs.utils import read_saxs, read_chi, read_reference_sub_dat, integration_comparison_metric

WORKSPACE_ROOT = os.path.abspath(os.path.join(_REPOS, ".."))
VALIDATION_DIR = os.path.join(WORKSPACE_ROOT, "validation")
AVERAGED_DIR = os.path.join(VALIDATION_DIR, "averaged")
SUBTRACTED_DIR = os.path.join(VALIDATION_DIR, "subtracted")
REFERENCE_DIR = os.path.join(VALIDATION_DIR, "reference")
REFERENCE_SUBTRACTED_DIR = os.path.join(VALIDATION_DIR, "reference_subtracted")
OUTPUT_DIR = os.path.join(VALIDATION_DIR, "validation_plots")
OUTPUT_DIR_INTEGRATED = os.path.join(OUTPUT_DIR, "integrated")
OUTPUT_DIR_INTEGRATED_LOG = os.path.join(OUTPUT_DIR_INTEGRATED, "log")
OUTPUT_DIR_INTEGRATED_LINEAR = os.path.join(OUTPUT_DIR_INTEGRATED, "linear")
OUTPUT_DIR_SUBTRACTED = os.path.join(OUTPUT_DIR, "subtracted")

METRICS_INTEGRATED_CSV = os.path.join(VALIDATION_DIR, "metrics_integrated.csv")
METRICS_SUBTRACTED_CSV = os.path.join(VALIDATION_DIR, "metrics_subtracted.csv")
SUCCESS_TXT = os.path.join(VALIDATION_DIR, "success.txt")
SIGNIFICANT_INCREASE_REL = 0.01  # >1%

SUB_DAT_PATTERN = re.compile(r"^sub_\d+\.dat$")

_VALIDATION_MISSING_MSG = (
    f"Validation directory not found: {VALIDATION_DIR}. "
    "Run: python repos/scripts/setup_validation_data.py"
)


@pytest.fixture(scope="module", autouse=True)
def _require_validation_dir_fixture():
    if not os.path.isdir(VALIDATION_DIR):
        raise FileNotFoundError(_VALIDATION_MISSING_MSG)

def _reset_validation_plots_dir():
    """Remove validation_plots/ before regenerating plots."""
    if os.path.isdir(OUTPUT_DIR):
        shutil.rmtree(OUTPUT_DIR)
    os.makedirs(OUTPUT_DIR_INTEGRATED_LOG, exist_ok=True)
    os.makedirs(OUTPUT_DIR_INTEGRATED_LINEAR, exist_ok=True)
    os.makedirs(OUTPUT_DIR_SUBTRACTED, exist_ok=True)


def _read_metrics_csv(path: str):
    """Read metrics CSV into dict keyed by (reference, generated)."""
    if not os.path.isfile(path):
        return {}
    out = {}
    with open(path, "r", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            ref = (row.get("reference") or "").strip()
            gen = (row.get("generated") or "").strip()
            metric_s = (row.get("metric") or "").strip()
            if not ref or not gen or not metric_s:
                continue
            try:
                metric = float(metric_s)
            except ValueError:
                continue
            out[(ref, gen)] = metric
    return out


def _write_metrics_csv(path: str, rows):
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["reference", "generated", "metric"])
        w.writeheader()
        for row in rows:
            w.writerow(row)


def _compare_metrics(old: dict, new_rows, label: str):
    """
    Compare new metrics to old.
    If any metric increases by >1% for an existing (reference, generated) pair -> warn and fail.
    Missing old rows are ignored and do not fail validation.
    """
    increased = []
    for row in new_rows:
        key = (row["reference"], row["generated"])
        new_m = row["metric"]
        old_m = old.get(key)
        if old_m is None:
            continue
        if new_m > old_m * (1.0 + SIGNIFICANT_INCREASE_REL):
            increased.append((key[0], key[1], old_m, new_m))

    if increased:
        print(
            f"WARNING: {label} validation metric increased by > {SIGNIFICANT_INCREASE_REL*100:.0f}% "
            f"for {len(increased)} case(s). Please check correctness."
        )
        for ref, gen, old_m, new_m in increased:
            rel = (new_m / old_m - 1.0) if old_m != 0 else float("inf")
            print(
                f"  reference={ref} generated={gen} old={old_m:.6f} new={new_m:.6f} rel_increase={rel:.3%}"
            )

    return len(increased) == 0


def _strip_leading_number_codes(name: str) -> str:
    """Remove leading number codes (digits + underscore). E.g. 0002_ihs27_95.9 -> ihs27_95.9."""
    while True:
        n = re.sub(r"^\d+_", "", name)
        if n == name:
            return name
        name = n


def _int_dat_to_pipeline_stem(dat_basename: str) -> str:
    """int_ihs27_95.6_95.9_sample.dat -> stem ihs27_95.6_95.9; then ref stem = first_last(ihs27_95.9)."""
    name = dat_basename
    if name.startswith("int_"):
        name = name[4:]
    for suffix in ("_sample", "_buffer"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def _pipeline_stem_to_ref_stem(pipeline_stem: str) -> str:
    """Pipeline sample stem is ihs27_95.9 (same as ref); only needed when stem had buffer in middle."""
    return pipeline_stem


def _ref_chi_base_for_pipeline_stem(pipeline_stem: str) -> str:
    """Find ref .chi basename (e.g. 0002_ihs27_95.9) whose stem after stripping leading digits equals pipeline_stem (e.g. ihs27_95.9)."""
    for f in os.listdir(REFERENCE_DIR):
        if f.endswith(".chi"):
            ref_base = f[: -4]
            if _strip_leading_number_codes(ref_base) == pipeline_stem:
                return ref_base
    return ""


def _int_dat_to_ref_basename(dat_basename: str) -> str:
    """Resolve pipeline int basename to reference .chi basename. Pipeline sample stem = ihs27_95.6_95.9 -> ref stem ihs27_95.9."""
    stem = _int_dat_to_pipeline_stem(dat_basename)
    ref_stem = _pipeline_stem_to_ref_stem(stem)
    return _ref_chi_base_for_pipeline_stem(ref_stem)


def _pipeline_sub_path_for_sample_basename(sample_basename: str) -> str:
    """Pipeline file is sub_ihs27_95.9_sample.dat; ref gives sample_basename 0002_ihs27_95.9 -> ref_stem ihs27_95.9."""
    ref_stem = _strip_leading_number_codes(sample_basename)
    path = os.path.join(SUBTRACTED_DIR, f"sub_{ref_stem}_sample.dat")
    return path if os.path.isfile(path) else ""


def run_calibration_integration_subtraction(mask_choice="f"):
    """Run pipeline with calibration, integration, and subtraction steps.
    mask_choice: 'f' = from file (use validation/mask*), 'c' = combine with automask, 'a' = automask only.
    """
    cast(Any, api).fast_first_processing(
        VALIDATION_DIR,
        steps=["calibration", "integration", "subtraction"],
        mask_choice=mask_choice,
        fast_forward=False,
    )


def _plot_comparison(q_ref, I_ref, q_pipe, I_pipe, metric, base, ref_label, pipe_label, out_dir, log_scale=True):
    """Draw comparison plot and save to out_dir with metric in title and filename. log_scale=False for subtracted (can be negative)."""
    fig, ax = plt.subplots()
    ax.plot(q_ref, I_ref, label=ref_label, alpha=0.8)
    ax.plot(q_pipe, I_pipe, label=pipe_label, alpha=0.8)
    ax.set_xlabel("q")
    ax.set_ylabel("I")
    ax.set_title(f"metric = {metric:.6f}\n{base}")
    ax.legend()
    if log_scale:
        ax.set_yscale("log")
    fig.tight_layout()
    safe_metric_str = f"{metric:.4f}".replace(".", "_")
    out_name = f"{safe_metric_str}_{base}.png"
    fig.savefig(os.path.join(out_dir, out_name), dpi=150)
    plt.close(fig)


def compare_and_plot_integrated():
    """
    For each averaged/int_*.dat find the reference .chi, compute metric, plot comparison,
    save to validation_plots/integrated/log/ (log y-scale) and validation_plots/integrated/linear/ (linear y-scale).
    """
    int_pattern = os.path.join(AVERAGED_DIR, "int_*.dat")
    int_files = sorted(glob.glob(int_pattern))
    if not int_files:
        raise FileNotFoundError(
            f"No integrated files found: {int_pattern}. Run calibration+integration first."
        )

    results = []
    metrics_rows = []
    for int_path in int_files:
        base = os.path.splitext(os.path.basename(int_path))[0]
        ref_base = _int_dat_to_ref_basename(base)
        if not ref_base:
            continue
        ref_path = os.path.join(REFERENCE_DIR, ref_base + ".chi")
        if not os.path.isfile(ref_path):
            continue

        q_pipe, I_pipe, _, _ = read_saxs(int_path)
        q_ref, I_ref = read_chi(ref_path)

        metric = integration_comparison_metric(q_pipe, I_pipe, q_ref, I_ref)
        results.append((base, ref_base, metric, q_pipe, I_pipe, q_ref, I_ref))
        metrics_rows.append(
            {"reference": ref_base + ".chi", "generated": base + ".dat", "metric": float(metric)}
        )

        _plot_comparison(
            q_ref, I_ref, q_pipe, I_pipe, metric, base,
            ref_label="reference (.chi)", pipe_label="pipeline (int)",
            out_dir=OUTPUT_DIR_INTEGRATED_LOG,
            log_scale=True,
        )
        _plot_comparison(
            q_ref, I_ref, q_pipe, I_pipe, metric, base,
            ref_label="reference (.chi)", pipe_label="pipeline (int)",
            out_dir=OUTPUT_DIR_INTEGRATED_LINEAR,
            log_scale=False,
        )

    return results, metrics_rows


def compare_and_plot_subtracted():
    """
    For each reference_subtracted/sub_*.dat parse metadata to get sample .chi basename;
    find pipeline subtracted/sub_<sample_base>_sample.dat, compare, plot to validation_plots/subtracted/.
    """
    ref_sub_files = sorted(
        f for f in os.listdir(REFERENCE_SUBTRACTED_DIR)
        if SUB_DAT_PATTERN.match(f)
    )
    if not ref_sub_files:
        raise FileNotFoundError(
            f"No reference subtracted files sub_*.dat in {REFERENCE_SUBTRACTED_DIR}."
        )

    results = []
    metrics_rows = []
    for ref_name in ref_sub_files:
        ref_path = os.path.join(REFERENCE_SUBTRACTED_DIR, ref_name)
        try:
            q_ref, I_ref, sample_basename = read_reference_sub_dat(ref_path)
        except ValueError:
            continue
        # Pipeline naming: subtracted/sub_<sample_base>_<buffer_base>_sample.dat (alignment requires buffer in sample name)
        pipe_path = _pipeline_sub_path_for_sample_basename(sample_basename)
        if not pipe_path or not os.path.isfile(pipe_path):
            continue

        q_pipe, I_pipe, _, _ = read_saxs(pipe_path)
        metric = integration_comparison_metric(q_pipe, I_pipe, q_ref, I_ref)
        base = os.path.splitext(os.path.basename(pipe_path))[0]
        results.append((base, ref_name, metric, q_pipe, I_pipe, q_ref, I_ref))
        metrics_rows.append(
            {"reference": ref_name, "generated": base + ".dat", "metric": float(metric)}
        )

        _plot_comparison(
            q_ref, I_ref, q_pipe, I_pipe, metric, base,
            ref_label="reference (sub_*.dat)", pipe_label="pipeline (sub)",
            out_dir=OUTPUT_DIR_SUBTRACTED,
            log_scale=False,
        )

    return results, metrics_rows


def test_calib_integration_validation():
    """Pytest entry: run pipeline (calib+integration) and compare to reference .chi."""
    _reset_validation_plots_dir()
    old_int = _read_metrics_csv(METRICS_INTEGRATED_CSV)
    run_calibration_integration_subtraction()
    results_int, metrics_rows = compare_and_plot_integrated()
    assert len(results_int) > 0, "No pipeline outputs could be matched to reference .chi files"
    ok = _compare_metrics(old_int, metrics_rows, label="Integrated")
    _write_metrics_csv(METRICS_INTEGRATED_CSV, metrics_rows)
    with open(SUCCESS_TXT, "w") as f:
        f.write("SUCCESS\n" if ok else "FAIL\n")
    print(f"VALIDATION: {'SUCCESS' if ok else 'FAIL'} (integrated)")
    assert ok, "Integrated metric regression detected (>1% increase)."


def test_calib_integration_subtraction_validation():
    """Pytest entry: run pipeline (calib+integration+subtraction) and compare to reference sub_*.dat."""
    _reset_validation_plots_dir()
    old_sub = _read_metrics_csv(METRICS_SUBTRACTED_CSV)
    run_calibration_integration_subtraction()
    results_sub, metrics_rows = compare_and_plot_subtracted()
    assert len(results_sub) > 0, "No pipeline subtracted outputs could be matched to reference sub_*.dat"
    ok = _compare_metrics(old_sub, metrics_rows, label="Subtracted")
    _write_metrics_csv(METRICS_SUBTRACTED_CSV, metrics_rows)
    with open(SUCCESS_TXT, "w") as f:
        f.write("SUCCESS\n" if ok else "FAIL\n")
    print(f"VALIDATION: {'SUCCESS' if ok else 'FAIL'} (subtracted)")
    assert ok, "Subtracted metric regression detected (>1% increase)."


if __name__ == "__main__":
    if not os.path.isdir(VALIDATION_DIR):
        raise FileNotFoundError(_VALIDATION_MISSING_MSG)
    _reset_validation_plots_dir()

    old_int = _read_metrics_csv(METRICS_INTEGRATED_CSV)
    old_sub = _read_metrics_csv(METRICS_SUBTRACTED_CSV)

    run_calibration_integration_subtraction()
    results_int, int_rows = compare_and_plot_integrated()
    results_sub, sub_rows = compare_and_plot_subtracted()

    ok_int = _compare_metrics(old_int, int_rows, label="Integrated")
    ok_sub = _compare_metrics(old_sub, sub_rows, label="Subtracted")
    ok_all = ok_int and ok_sub

    _write_metrics_csv(METRICS_INTEGRATED_CSV, int_rows)
    _write_metrics_csv(METRICS_SUBTRACTED_CSV, sub_rows)
    with open(SUCCESS_TXT, "w") as f:
        f.write("SUCCESS\n" if ok_all else "FAIL\n")
    print(f"VALIDATION: {'SUCCESS' if ok_all else 'FAIL'}")

    print(f"Integrated: compared {len(results_int)} curves. Plots in {OUTPUT_DIR_INTEGRATED_LOG} (log) and {OUTPUT_DIR_INTEGRATED_LINEAR} (linear)")
    for base, ref_base, metric, *_ in results_int:
        print(f"  {base} vs {ref_base}.chi  metric = {metric:.6f}")
    print(f"Subtracted: compared {len(results_sub)} curves. Plots in {OUTPUT_DIR_SUBTRACTED}")
    for base, ref_name, metric, *_ in results_sub:
        print(f"  {base} vs {ref_name}  metric = {metric:.6f}")

    if not ok_all:
        raise SystemExit(1)

