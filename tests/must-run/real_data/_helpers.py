"""
Shared helpers for commit-gate real-data skill pipelines under ``validation/``.

Pytest entry points live under ``light/`` and ``heavy/``. This module owns runners,
comparators, and path constants only.

Prerequisites: run ``scripts/setup_validation_data.py`` (optional ``PROTOCOL_MONO_2D``,
``PROTOCOL_POLY``) so ``validation/`` has IHS raw/reference trees and, when available,
``reference_mono/`` / ``reference_poly/`` plus ``poly/`` inputs.
"""
from __future__ import annotations

import os
import sys
import glob
import re
import csv
import shutil
from typing import Any, Dict, List, Optional
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import yaml

# tests/must-run/real_data -> repo root is three levels up
_REPOS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_SRC = os.path.join(_REPOS, "src")
for _p in (_SRC, _REPOS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from autosaxs.core.gnom import parse_gnom_out
from autosaxs.core.guinier import parse_guinier_results_txt
from autosaxs.core.utils import (
    chi2_average_sigma,
    integration_comparison_metric,
    map_sample_files_to_buffer_files,
    read_chi,
    read_reference_sub_dat,
    read_saxs,
    subtraction_comparison_metric,
)
from autosaxs.skill.analyze_kratky import analyze_kratky
from autosaxs.skill.calibrate import calibrate
from autosaxs.skill.config import merge_skill_params
from autosaxs.skill.fit_distances import fit_distances
from autosaxs.skill.fit_guinier import fit_guinier
from autosaxs.skill.fit_sizes import fit_sizes
from autosaxs.skill.integrate import integrate
from autosaxs.skill.model_dam import model_dam
from autosaxs.skill.subtract import subtract

WORKSPACE_ROOT = os.path.abspath(os.path.join(_REPOS, ".."))
VALIDATION_DIR = os.path.join(WORKSPACE_ROOT, "validation")
RAW_DIR = os.path.join(VALIDATION_DIR, "raw")
CONFIG_PATH = os.path.join(VALIDATION_DIR, "config.conf")
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
METRICS_INTEGRATED_CHI2_CSV = os.path.join(VALIDATION_DIR, "metrics_integrated_chi2.csv")
METRICS_SUBTRACTED_CHI2_CSV = os.path.join(VALIDATION_DIR, "metrics_subtracted_chi2.csv")
SUCCESS_TXT = os.path.join(VALIDATION_DIR, "success.txt")
SIGNIFICANT_INCREASE_REL = 0.01  # >1%
REFERENCE_DEFAULT_REL_SIGMA = 0.03

REFERENCE_MONO_DIR = os.path.join(VALIDATION_DIR, "reference_mono")
REFERENCE_MONO_MANIFEST = os.path.join(REFERENCE_MONO_DIR, "manifest.yml")
MONO_GUINIER_DIR = os.path.join(VALIDATION_DIR, "guinier_mono")
MONO_KRATKY_DIR = os.path.join(VALIDATION_DIR, "analyze_kratky")
MONO_FD_SMOKE_DIR = os.path.join(VALIDATION_DIR, "fit_distances_datgnom")
MONO_FD_REFINE_DIR = os.path.join(VALIDATION_DIR, "fit_distances_refine")
MONO_DAM_DIR = os.path.join(VALIDATION_DIR, "dammif")
METRICS_MONO_GUINIER_CSV = os.path.join(VALIDATION_DIR, "metrics_mono_guinier.csv")
METRICS_MONO_KRATKY_CSV = os.path.join(VALIDATION_DIR, "metrics_mono_kratky.csv")
METRICS_MONO_FD_REFINE_CSV = os.path.join(VALIDATION_DIR, "metrics_mono_fit_distances_refine.csv")
SUCCESS_MONO_TXT = os.path.join(VALIDATION_DIR, "success_mono.txt")

# Polydisperse (Pt_NPs) — separate from IHS raw/
POLY_DIR = os.path.join(VALIDATION_DIR, "poly")
POLY_RAW_DIR = os.path.join(POLY_DIR, "raw")
POLY_CONFIG_PATH = os.path.join(POLY_DIR, "config.conf")
POLY_AVERAGED_DIR = os.path.join(POLY_DIR, "averaged")
POLY_SUBTRACTED_DIR = os.path.join(POLY_DIR, "subtracted")
POLY_GUINIER_DIR = os.path.join(POLY_DIR, "guinier_poly")
POLY_FIT_SIZES_DIR = os.path.join(POLY_DIR, "fit_sizes")
REFERENCE_POLY_DIR = os.path.join(VALIDATION_DIR, "reference_poly")
REFERENCE_POLY_MANIFEST = os.path.join(REFERENCE_POLY_DIR, "manifest.yml")
METRICS_POLY_GUINIER_CSV = os.path.join(VALIDATION_DIR, "metrics_poly_guinier.csv")
METRICS_POLY_FIT_SIZES_CSV = os.path.join(VALIDATION_DIR, "metrics_poly_fit_sizes.csv")
SUCCESS_POLY_TXT = os.path.join(VALIDATION_DIR, "success_poly.txt")

# Relative tolerances vs protocol goldens (E2E regenerated curves may differ slightly).
MONO_RG_REL_TOL = 0.10
MONO_KRATKY_PEAK_REL_TOL = 0.15
MONO_PR_RG_REL_TOL = 0.10
MONO_PR_DIST_METRIC_MAX = 0.25
MONO_DAM_N_RUNS = 3
POLY_RG_REL_TOL = 0.15
POLY_RMAX_REL_TOL = 0.15
POLY_DR_DIST_METRIC_MAX = 0.30

SUB_DAT_PATTERN = re.compile(r"^sub_\d+\.dat$")
PROTOCOL_KEY_FROM_STEM = re.compile(r"^(ihs\d+)")

_VALIDATION_MISSING_MSG = (
    f"Validation directory not found: {VALIDATION_DIR}. "
    "Run: python scripts/setup_validation_data.py"
)

def _reset_validation_plots_dir():
    """
    Reset validation_plots/ before regenerating plots.

    IMPORTANT: Do not wipe unrelated plot types. The integrated and subtracted validation
    tests run independently; deleting the whole OUTPUT_DIR in each test would erase plots
    produced by the other test earlier in the same pytest run.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)


def _reset_validation_plots_subdir(which: str) -> None:
    """
    Remove only a specific plots subdir under validation_plots/.

    which: "integrated" or "subtracted"
    """
    if which not in ("integrated", "subtracted"):
        raise ValueError("which must be 'integrated' or 'subtracted'")
    root = OUTPUT_DIR_INTEGRATED if which == "integrated" else OUTPUT_DIR_SUBTRACTED
    if os.path.isdir(root):
        shutil.rmtree(root)
    if which == "integrated":
        os.makedirs(OUTPUT_DIR_INTEGRATED_LOG, exist_ok=True)
        os.makedirs(OUTPUT_DIR_INTEGRATED_LINEAR, exist_ok=True)
    else:
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


def _compare_metrics(old: dict, new_rows, label: str, rel_tol: float = SIGNIFICANT_INCREASE_REL):
    """
    Compare new metrics to old.
    If any metric increases by >rel_tol for an existing (reference, generated) pair -> warn and fail.
    Missing old rows are ignored and do not fail validation.
    """
    increased = []
    for row in new_rows:
        key = (row["reference"], row["generated"])
        new_m = row["metric"]
        old_m = old.get(key)
        if old_m is None:
            continue
        if new_m > old_m * (1.0 + rel_tol):
            increased.append((key[0], key[1], old_m, new_m))

    if increased:
        print(
            f"WARNING: {label} validation metric increased by > {rel_tol*100:.0f}% "
            f"for {len(increased)} case(s). Please check correctness."
        )
        for ref, gen, old_m, new_m in increased:
            rel = (new_m / old_m - 1.0) if old_m != 0 else float("inf")
            print(
                f"  reference={ref} generated={gen} old={old_m:.6f} new={new_m:.6f} rel_increase={rel:.3%}"
            )

    return len(increased) == 0


def _chi2_vs_reference(
    q_ref: np.ndarray,
    I_ref: np.ndarray,
    q_pipe: np.ndarray,
    I_pipe: np.ndarray,
    sigma_pipe: Optional[np.ndarray] = None,
    q_min: Optional[float] = None,
    q_max: float = 6.0,
) -> float:
    """Reduced chi2 via :func:`chi2_average_sigma` on a common q grid."""
    if q_min is None:
        q_min = max(np.min(q_ref), np.min(q_pipe))
    q_max_val = float(q_max)
    q_common = np.sort(np.unique(np.concatenate([q_ref, q_pipe])))
    q_common = q_common[(q_common >= q_min) & (q_common <= q_max_val)]
    if len(q_common) < 2:
        return np.nan
    I_ref_interp = np.interp(q_common, q_ref, I_ref)
    I_pipe_interp = np.interp(q_common, q_pipe, I_pipe)
    sigma_ref = REFERENCE_DEFAULT_REL_SIGMA * np.abs(I_ref_interp)
    if sigma_pipe is not None:
        sigma_pipe_interp = np.interp(q_common, q_pipe, sigma_pipe)
    else:
        sigma_pipe_interp = REFERENCE_DEFAULT_REL_SIGMA * np.abs(I_pipe_interp)
    return chi2_average_sigma(I_ref_interp, I_pipe_interp, sigma_ref, sigma_pipe_interp)


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


def _validation_calib_tif() -> str:
    paths = sorted(glob.glob(os.path.join(RAW_DIR, "*_calib.tif")))
    if not paths:
        raise FileNotFoundError(f"No calibration TIFF in {RAW_DIR}")
    return paths[0]


def _validation_mask_path() -> str:
    paths = sorted(
        p
        for p in glob.glob(os.path.join(VALIDATION_DIR, "mask*"))
        if os.path.isfile(p)
    )
    if not paths:
        raise FileNotFoundError(f"No mask file matching mask* in {VALIDATION_DIR}")
    return paths[0]


_MASK_MODE_BY_CHOICE = {"f": "from_file", "c": "combined", "a": "auto"}


def run_calibration_integration_subtraction(mask_choice="f", *, run_subtraction: bool = True):
    """Run calibrate → integrate → subtract via skills (no interactive pipeline).

    mask_choice: 'f' = from file (validation/mask*), 'c' = combine with automask, 'a' = automask only.
    """
    if mask_choice not in _MASK_MODE_BY_CHOICE:
        raise ValueError(f"mask_choice must be one of {sorted(_MASK_MODE_BY_CHOICE)}; got {mask_choice!r}")

    calib_image = _validation_calib_tif()
    mask_mode = _MASK_MODE_BY_CHOICE[mask_choice]
    mask_path = _validation_mask_path()

    out_cal = calibrate(
        calib_image,
        VALIDATION_DIR,
        config_path=CONFIG_PATH,
        mask=mask_path,
        mask_mode=mask_mode,
        use_cache=False,
    )
    integrator_dir = out_cal["integrator_dir"]

    buffer_paths = sorted(glob.glob(os.path.join(RAW_DIR, "*_buffer.tif")))
    sample_paths = sorted(glob.glob(os.path.join(RAW_DIR, "*_sample.tif")))
    os.makedirs(AVERAGED_DIR, exist_ok=True)

    if buffer_paths:
        integrate(
            buffer_paths,
            integrator_dir,
            AVERAGED_DIR,
            config_path=CONFIG_PATH,
            use_cache=False,
        )
    if sample_paths:
        integrate(
            sample_paths,
            integrator_dir,
            AVERAGED_DIR,
            config_path=CONFIG_PATH,
            use_cache=False,
        )

    if not run_subtraction:
        return

    buffer_1d = sorted(glob.glob(os.path.join(AVERAGED_DIR, "int_*_buffer.dat")))
    sample_1d = sorted(glob.glob(os.path.join(AVERAGED_DIR, "int_*_sample.dat")))
    if not sample_1d:
        raise FileNotFoundError(f"No integrated sample curves in {AVERAGED_DIR}")

    alignment = map_sample_files_to_buffer_files(sample_1d, buffer_1d)
    if alignment["overlapped"] or alignment["not_paired"]:
        overlap_str = "\n".join([", ".join(p) for p in alignment["overlapped"]])
        not_paired_str = "\n".join(alignment["not_paired"])
        raise RuntimeError(
            "Buffer-sample alignment failed for validation 1D curves.\n"
            f"Overlapped: {overlap_str}\nNot paired: {not_paired_str}"
        )

    os.makedirs(SUBTRACTED_DIR, exist_ok=True)
    sub_merged = merge_skill_params("subtract", config_path=CONFIG_PATH)
    q_sub_min = sub_merged.get("q_min")
    q_sub_max = sub_merged.get("q_max")
    if q_sub_min is None or q_sub_max is None:
        raise RuntimeError("validation config.conf subtract section must define q_min and q_max")
    for sample_path, buffer_path in alignment["aligned_pairs"]:
        subtract(
            sample_path,
            buffer_path,
            SUBTRACTED_DIR,
            q_min=float(q_sub_min),
            q_max=float(q_sub_max),
            config_path=CONFIG_PATH,
            use_cache=False,
        )


def _plot_comparison(
    q_ref, I_ref, q_pipe, I_pipe, metric, chi2, base, ref_label, pipe_label, out_dir, log_scale=True
):
    """Draw comparison plot; metric and chi2 appear in title and filename."""
    os.makedirs(out_dir, exist_ok=True)
    fig, ax = plt.subplots()
    ax.plot(q_ref, I_ref, label=ref_label, alpha=0.8)
    ax.plot(q_pipe, I_pipe, label=pipe_label, alpha=0.8)
    ax.set_xlabel("q")
    ax.set_ylabel("I")
    ax.set_title(f"metric = {metric:.6f}, chi2 = {chi2:.6f}\n{base}")
    ax.legend()
    if log_scale:
        ax.set_yscale("log")
    fig.tight_layout()
    safe_metric_str = f"{metric:.4f}".replace(".", "_")
    safe_chi2_str = f"{chi2:.4f}".replace(".", "_")
    out_name = f"{safe_metric_str}_chi2_{safe_chi2_str}_{base}.png"
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
    chi2_rows = []
    for int_path in int_files:
        base = os.path.splitext(os.path.basename(int_path))[0]
        ref_base = _int_dat_to_ref_basename(base)
        if not ref_base:
            continue
        ref_path = os.path.join(REFERENCE_DIR, ref_base + ".chi")
        if not os.path.isfile(ref_path):
            continue

        q_pipe, I_pipe, sigma_pipe, _ = read_saxs(int_path)
        q_ref, I_ref = read_chi(ref_path)

        metric = integration_comparison_metric(q_pipe, I_pipe, q_ref, I_ref)
        chi2 = _chi2_vs_reference(q_ref, I_ref, q_pipe, I_pipe, sigma_pipe=sigma_pipe)
        results.append((base, ref_base, metric, q_pipe, I_pipe, q_ref, I_ref))
        metrics_rows.append(
            {"reference": ref_base + ".chi", "generated": base + ".dat", "metric": float(metric)}
        )
        chi2_rows.append(
            {"reference": ref_base + ".chi", "generated": base + ".dat", "metric": float(chi2)}
        )

        _plot_comparison(
            q_ref, I_ref, q_pipe, I_pipe, metric, chi2, base,
            ref_label="reference (.chi)", pipe_label="pipeline (int)",
            out_dir=OUTPUT_DIR_INTEGRATED_LOG,
            log_scale=True,
        )
        _plot_comparison(
            q_ref, I_ref, q_pipe, I_pipe, metric, chi2, base,
            ref_label="reference (.chi)", pipe_label="pipeline (int)",
            out_dir=OUTPUT_DIR_INTEGRATED_LINEAR,
            log_scale=False,
        )

    return results, metrics_rows, chi2_rows


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
    chi2_rows = []
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

        q_pipe, I_pipe, sigma_pipe, _ = read_saxs(pipe_path)
        metric = subtraction_comparison_metric(q_ref, I_ref, q_pipe, I_pipe)
        chi2 = _chi2_vs_reference(q_ref, I_ref, q_pipe, I_pipe, sigma_pipe=sigma_pipe)
        base = os.path.splitext(os.path.basename(pipe_path))[0]
        results.append((base, ref_name, metric, q_pipe, I_pipe, q_ref, I_ref))
        metrics_rows.append(
            {"reference": ref_name, "generated": base + ".dat", "metric": float(metric)}
        )
        chi2_rows.append(
            {"reference": ref_name, "generated": base + ".dat", "metric": float(chi2)}
        )

        _plot_comparison(
            q_ref, I_ref, q_pipe, I_pipe, metric, chi2, base,
            ref_label="reference (sub_*.dat)", pipe_label="pipeline (sub)",
            out_dir=OUTPUT_DIR_SUBTRACTED,
            log_scale=False,
        )

    return results, metrics_rows, chi2_rows


def _load_mono_manifest() -> Dict[str, Any]:
    if not os.path.isfile(REFERENCE_MONO_MANIFEST):
        raise FileNotFoundError(
            f"Missing {REFERENCE_MONO_MANIFEST}. "
            "Sync with scripts/setup_validation_data.py (PROTOCOL_MONO_2D) or copy reference_mono/."
        )
    with open(REFERENCE_MONO_MANIFEST, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _as_scalar(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _protocol_key_from_sub_path(sub_path: str) -> Optional[str]:
    base = os.path.splitext(os.path.basename(sub_path))[0]
    if base.startswith("sub_"):
        base = base[4:]
    if base.endswith("_sample"):
        base = base[: -len("_sample")]
    m = PROTOCOL_KEY_FROM_STEM.match(base)
    return m.group(1) if m else None


def _sample_stem_from_sub_path(sub_path: str) -> str:
    base = os.path.splitext(os.path.basename(sub_path))[0]
    if base.startswith("sub_"):
        base = base[4:]
    return base


def _validation_sub_dat_paths() -> List[str]:
    paths = sorted(glob.glob(os.path.join(SUBTRACTED_DIR, "sub_*_sample.dat")))
    if not paths:
        raise FileNotFoundError(f"No sub_*_sample.dat under {SUBTRACTED_DIR}")
    return paths


def _rel_err(a: float, b: float) -> float:
    denom = max(abs(b), 1e-12)
    return abs(float(a) - float(b)) / denom


def _pr_distribution_metric(parsed_ref: Dict[str, Any], parsed_pipe: Dict[str, Any]) -> float:
    """Mean relative |p_ref - p_pipe| / (|p_ref| + |p_pipe| + eps) on a common r grid."""
    dist_ref = parsed_ref.get("distribution")
    dist_pipe = parsed_pipe.get("distribution")
    if not dist_ref or not dist_pipe:
        return float("nan")
    r_ref, p_ref, _ = dist_ref
    r_pipe, p_pipe, _ = dist_pipe
    r_ref = np.asarray(r_ref, dtype=float)
    p_ref = np.asarray(p_ref, dtype=float)
    r_pipe = np.asarray(r_pipe, dtype=float)
    p_pipe = np.asarray(p_pipe, dtype=float)
    r0 = max(float(np.min(r_ref)), float(np.min(r_pipe)))
    r1 = min(float(np.max(r_ref)), float(np.max(r_pipe)))
    if not np.isfinite(r0) or not np.isfinite(r1) or r1 <= r0:
        return float("nan")
    r_common = np.linspace(r0, r1, num=200)
    p1 = np.interp(r_common, r_ref, p_ref)
    p2 = np.interp(r_common, r_pipe, p_pipe)
    eps = 1e-12
    return float(np.mean(np.abs(p1 - p2) / (np.abs(p1) + np.abs(p2) + eps)))


def _guinier_results_path_for_stem(stem: str) -> str:
    return os.path.join(MONO_GUINIER_DIR, f"{stem}_results.txt")


def _reset_mono_output_dirs() -> None:
    for d in (MONO_GUINIER_DIR, MONO_KRATKY_DIR, MONO_FD_SMOKE_DIR, MONO_FD_REFINE_DIR):
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d, exist_ok=True)


def run_monodisperse_skills() -> Dict[str, Any]:
    """
    Light E2E monodisperse stage on regenerated subtracted curves.

    Order: Guinier → Kratky → DATGNOM smoke (all samples) → pinned GNOM refine
    (protocol refine keys). Heavy ``model_dam`` is a separate stage.
    """
    manifest = _load_mono_manifest()
    samples_meta: Dict[str, Any] = manifest.get("samples") or {}

    run_calibration_integration_subtraction()
    _reset_mono_output_dirs()
    sub_paths = _validation_sub_dat_paths()

    # --- fit-guinier (all) ---
    fit_guinier(
        os.path.join(SUBTRACTED_DIR, "sub_*_sample.dat"),
        output_dir=MONO_GUINIER_DIR,
        use_cache=False,
    )

    guinier_by_stem: Dict[str, Dict[str, Any]] = {}
    for sub in sub_paths:
        stem = _sample_stem_from_sub_path(sub)
        parsed = parse_guinier_results_txt(_guinier_results_path_for_stem(stem))
        if not parsed.get("rg"):
            raise RuntimeError(f"Guinier failed for {stem}: missing Rg in results")
        guinier_by_stem[stem] = parsed

    # --- analyze-kratky (all; Rg/I0 from just-produced Guinier) ---
    kratky_by_stem: Dict[str, Dict[str, Any]] = {}
    for sub in sub_paths:
        stem = _sample_stem_from_sub_path(sub)
        g = guinier_by_stem[stem]
        out_k = analyze_kratky(
            sub,
            output_dir=MONO_KRATKY_DIR,
            rg_nm=float(g["rg"]),
            i0=float(g["i0"]) if g.get("i0") is not None else None,
            use_cache=False,
        )
        kratky_by_stem[stem] = {
            "classification": _as_scalar(out_k.get("classification")),
            "x_max": _as_scalar(out_k.get("x_max")),
            "y_max": _as_scalar(out_k.get("y_max")),
            "results_path": _as_scalar(out_k.get("results_path")),
        }

    # --- DATGNOM smoke (all) ---
    smoke_out_by_stem: Dict[str, Dict[str, Any]] = {}
    for sub in sub_paths:
        stem = _sample_stem_from_sub_path(sub)
        g = guinier_by_stem[stem]
        out_fd = fit_distances(
            sub,
            output_dir=MONO_FD_SMOKE_DIR,
            rg_nm=float(g["rg"]),
            use_cache=False,
        )
        best = _as_scalar(out_fd.get("best_gnom_out_path"))
        if not best or not os.path.isfile(str(best)):
            raise RuntimeError(f"DATGNOM smoke produced no .out for {stem}")
        smoke_out_by_stem[stem] = out_fd

    # --- pinned GNOM refine (protocol refine keys only) ---
    refine_out_by_key: Dict[str, Dict[str, Any]] = {}
    sub_by_key: Dict[str, str] = {}
    for sub in sub_paths:
        key = _protocol_key_from_sub_path(sub)
        if key:
            sub_by_key[key] = sub

    for key, meta in samples_meta.items():
        if str(meta.get("mode") or "") != "refine":
            continue
        sub = sub_by_key.get(key)
        if not sub:
            raise RuntimeError(f"No validation sub_*.dat mapped to protocol key {key}")
        stem = _sample_stem_from_sub_path(sub)
        g = guinier_by_stem[stem]
        out_ref = fit_distances(
            sub,
            output_dir=MONO_FD_REFINE_DIR,
            rg_nm=float(g["rg"]),
            q_min=float(meta["q_min"]),
            q_max=float(meta["q_max"]),
            dmax_nm=float(meta["dmax_nm"]),
            alpha=float(meta["alpha"]),
            force_zero_rmin=str(meta.get("force_zero_rmin") or "N"),
            force_zero_rmax=str(meta.get("force_zero_rmax") or "N"),
            minimal=True,
            use_cache=False,
        )
        best = _as_scalar(out_ref.get("best_gnom_out_path"))
        if not best or not os.path.isfile(str(best)):
            raise RuntimeError(f"Pinned refine produced no .out for {key}")
        refine_out_by_key[key] = out_ref

    return {
        "manifest": manifest,
        "sub_paths": sub_paths,
        "guinier_by_stem": guinier_by_stem,
        "kratky_by_stem": kratky_by_stem,
        "smoke_out_by_stem": smoke_out_by_stem,
        "refine_out_by_key": refine_out_by_key,
        "sub_by_key": sub_by_key,
    }


def run_model_dam_heavy(*, model_dam_key: Optional[str] = None) -> Dict[str, Any]:
    """
    Heavy stage: ``model_dam`` smoke for one protocol key (default ihs27).

    Requires light mono refine outputs under ``fit_distances_refine/`` (or re-runs
    light monodisperse if missing).
    """
    manifest = _load_mono_manifest()
    key = str(model_dam_key or manifest.get("model_dam_key") or "ihs27")
    samples_meta: Dict[str, Any] = manifest.get("samples") or {}
    meta = samples_meta.get(key)
    if not meta or str(meta.get("mode") or "") != "refine":
        raise RuntimeError(f"model_dam key {key} missing refine metadata in mono manifest")

    # Prefer existing light refine .out; otherwise run light chain once.
    refine_glob = glob.glob(os.path.join(MONO_FD_REFINE_DIR, "**", "gnom_best.out"), recursive=True)
    sub_paths = []
    if os.path.isdir(SUBTRACTED_DIR):
        sub_paths = sorted(glob.glob(os.path.join(SUBTRACTED_DIR, "sub_*_sample.dat")))
    need_light = not refine_glob or not sub_paths
    if need_light:
        light = run_monodisperse_skills()
        refine_out = light["refine_out_by_key"].get(key) or {}
        sub_dam = light["sub_by_key"].get(key)
        gnom_dam = _as_scalar(refine_out.get("best_gnom_out_path"))
    else:
        sub_by_key: Dict[str, str] = {}
        for sub in sub_paths:
            pk = _protocol_key_from_sub_path(sub)
            if pk:
                sub_by_key[pk] = sub
        sub_dam = sub_by_key.get(key)
        # Match refine out for this key: stem folder under refine dir
        gnom_dam = None
        if sub_dam:
            stem = _sample_stem_from_sub_path(sub_dam)
            cand = os.path.join(MONO_FD_REFINE_DIR, stem, "gnom_best.out")
            if os.path.isfile(cand):
                gnom_dam = cand
            else:
                # fit_distances may write flat or nested; search
                for p in refine_glob:
                    if key in p or stem in p:
                        gnom_dam = p
                        break
        if not gnom_dam and refine_glob:
            gnom_dam = refine_glob[0]

    if not sub_dam or not gnom_dam or not os.path.isfile(str(gnom_dam)):
        raise RuntimeError(f"Cannot locate sub curve / refine .out for model_dam key {key}")

    if os.path.isdir(MONO_DAM_DIR):
        shutil.rmtree(MONO_DAM_DIR)
    os.makedirs(MONO_DAM_DIR, exist_ok=True)

    dam_out = model_dam(
        sub_dam,
        output_dir=MONO_DAM_DIR,
        gnom_path=str(gnom_dam),
        n_runs=MONO_DAM_N_RUNS,
        dammif_mode="fast",
        use_cache=False,
    )
    best_cif = _as_scalar(dam_out.get("best_cif_path"))
    if not best_cif or not os.path.lexists(str(best_cif)):
        raise RuntimeError(f"model_dam did not produce best.cif for {key}")
    return {"model_dam_key": key, "dam_out": dam_out, "sub_path": sub_dam, "gnom_path": gnom_dam}


def compare_monodisperse_to_reference(run_state: Dict[str, Any]):
    """
    Compare protocol-keyed mono outputs to reference_mono goldens.

    Returns (ok, failures, metric_row_groups).
    """
    manifest = run_state["manifest"]
    samples_meta: Dict[str, Any] = manifest.get("samples") or {}
    guinier_by_stem = run_state["guinier_by_stem"]
    kratky_by_stem = run_state["kratky_by_stem"]
    refine_out_by_key = run_state["refine_out_by_key"]
    sub_by_key = run_state["sub_by_key"]

    guinier_rows = []
    kratky_rows = []
    refine_rows = []
    failures: List[str] = []

    for key, meta in samples_meta.items():
        sub = sub_by_key.get(key)
        if not sub:
            failures.append(f"{key}: missing validation sub curve")
            continue
        stem = _sample_stem_from_sub_path(sub)

        # Guinier
        ref_g_path = os.path.join(REFERENCE_MONO_DIR, meta["guinier_results"])
        ref_g = parse_guinier_results_txt(ref_g_path)
        pipe_g = guinier_by_stem[stem]
        rg_err = _rel_err(pipe_g["rg"], ref_g["rg"])
        guinier_rows.append(
            {
                "reference": f"{key}:{os.path.basename(ref_g_path)}",
                "generated": f"{stem}_results.txt",
                "metric": float(rg_err),
            }
        )
        if rg_err > MONO_RG_REL_TOL:
            failures.append(f"{key} Guinier Rg rel_err={rg_err:.3f} > {MONO_RG_REL_TOL}")

        # Kratky
        ref_k_path = os.path.join(REFERENCE_MONO_DIR, meta["kratky_params"])
        with open(ref_k_path, "r", encoding="utf-8") as f:
            ref_k = yaml.safe_load(f) or {}
        pipe_k = kratky_by_stem[stem]
        ref_cls = str(ref_k.get("classification") or "").strip().lower()
        pipe_cls = str(pipe_k.get("classification") or "").strip().lower()
        if ref_cls and pipe_cls != ref_cls:
            failures.append(f"{key} Kratky classification {pipe_cls!r} != {ref_cls!r}")
        x_err = _rel_err(float(pipe_k["x_max"]), float(ref_k["x_max"]))
        y_err = _rel_err(float(pipe_k["y_max"]), float(ref_k["y_max"]))
        kratky_metric = max(x_err, y_err)
        kratky_rows.append(
            {
                "reference": f"{key}:{os.path.basename(ref_k_path)}",
                "generated": stem,
                "metric": float(kratky_metric),
            }
        )
        if x_err > MONO_KRATKY_PEAK_REL_TOL or y_err > MONO_KRATKY_PEAK_REL_TOL:
            failures.append(
                f"{key} Kratky peak rel_err x={x_err:.3f} y={y_err:.3f} "
                f"> {MONO_KRATKY_PEAK_REL_TOL}"
            )

        # Pinned refine vs golden .out (refine keys only)
        if str(meta.get("mode") or "") != "refine":
            continue
        out_ref = refine_out_by_key.get(key) or {}
        best = _as_scalar(out_ref.get("best_gnom_out_path"))
        ref_out_path = os.path.join(REFERENCE_MONO_DIR, meta["best_out_path"])
        parsed_ref = parse_gnom_out(ref_out_path)
        parsed_pipe = parse_gnom_out(str(best))
        rg_ref = parsed_ref.get("real_space_rg")
        rg_pipe = parsed_pipe.get("real_space_rg")
        if rg_ref is None or rg_pipe is None:
            failures.append(f"{key} refine: missing real-space Rg in .out")
            continue
        rg_pr_err = _rel_err(rg_pipe, rg_ref)
        dist_metric = _pr_distribution_metric(parsed_ref, parsed_pipe)
        refine_rows.append(
            {
                "reference": f"{key}:{os.path.basename(ref_out_path)}",
                "generated": os.path.basename(str(best)),
                "metric": float(dist_metric) if np.isfinite(dist_metric) else float(rg_pr_err),
            }
        )
        if rg_pr_err > MONO_PR_RG_REL_TOL:
            failures.append(f"{key} refine Rg_pr rel_err={rg_pr_err:.3f} > {MONO_PR_RG_REL_TOL}")
        if np.isfinite(dist_metric) and dist_metric > MONO_PR_DIST_METRIC_MAX:
            failures.append(
                f"{key} refine p(r) metric={dist_metric:.3f} > {MONO_PR_DIST_METRIC_MAX}"
            )

    n_smoke = len(run_state["smoke_out_by_stem"])
    if n_smoke < 1:
        failures.append("DATGNOM smoke: no samples")

    ok = len(failures) == 0
    return ok, failures, {
        "guinier": guinier_rows,
        "kratky": kratky_rows,
        "refine": refine_rows,
    }


def require_validation_dir() -> None:
    if not os.path.isdir(VALIDATION_DIR):
        raise FileNotFoundError(_VALIDATION_MISSING_MSG)


def _load_poly_manifest() -> Dict[str, Any]:
    if not os.path.isfile(REFERENCE_POLY_MANIFEST):
        raise FileNotFoundError(
            f"Missing {REFERENCE_POLY_MANIFEST}. "
            "Sync with scripts/setup_validation_data.py (PROTOCOL_POLY)."
        )
    with open(REFERENCE_POLY_MANIFEST, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _poly_mask_path() -> str:
    for name in ("mask-Pt-NPs.txt", "mask.txt"):
        p = os.path.join(POLY_DIR, name)
        if os.path.isfile(p):
            return p
    paths = sorted(
        p for p in glob.glob(os.path.join(POLY_DIR, "mask*")) if os.path.isfile(p)
    )
    if not paths:
        raise FileNotFoundError(f"No mask under {POLY_DIR}")
    return paths[0]


def _poly_calib_tif() -> str:
    paths = sorted(glob.glob(os.path.join(POLY_RAW_DIR, "*calib*.tif")))
    if not paths:
        paths = sorted(glob.glob(os.path.join(POLY_RAW_DIR, "AgBh*.tif")))
    if not paths:
        raise FileNotFoundError(f"No calibration TIFF in {POLY_RAW_DIR}")
    return paths[0]


def run_polydisperse_pipeline() -> Dict[str, Any]:
    """
    Light polydisperse: calib → integrate → subtract → Guinier → fit_sizes (no MIXTURE).

    Uses ``validation/poly/`` inputs and compares later via ``compare_polydisperse_to_reference``.
    """
    manifest = _load_poly_manifest()
    samples_meta: Dict[str, Any] = manifest.get("samples") or {}
    if not os.path.isdir(POLY_RAW_DIR):
        raise FileNotFoundError(
            f"Missing {POLY_RAW_DIR}. Run setup_validation_data.py with PROTOCOL_POLY."
        )
    config_path = POLY_CONFIG_PATH if os.path.isfile(POLY_CONFIG_PATH) else CONFIG_PATH

    calib_image = _poly_calib_tif()
    mask_path = _poly_mask_path()
    out_cal = calibrate(
        calib_image,
        POLY_DIR,
        config_path=config_path,
        mask=mask_path,
        mask_mode="from_file",
        use_cache=False,
    )
    integrator_dir = out_cal["integrator_dir"]

    buffer_paths = sorted(glob.glob(os.path.join(POLY_RAW_DIR, "*buffer*.tif")))
    sample_paths = sorted(
        p
        for p in glob.glob(os.path.join(POLY_RAW_DIR, "Pt_NPs_*.tif"))
        if "buffer" not in os.path.basename(p).lower()
        and "calib" not in os.path.basename(p).lower()
        and "AgBh" not in os.path.basename(p)
    )
    os.makedirs(POLY_AVERAGED_DIR, exist_ok=True)
    if buffer_paths:
        integrate(buffer_paths, integrator_dir, POLY_AVERAGED_DIR, config_path=config_path, use_cache=False)
    if sample_paths:
        integrate(sample_paths, integrator_dir, POLY_AVERAGED_DIR, config_path=config_path, use_cache=False)

    buffer_1d = sorted(glob.glob(os.path.join(POLY_AVERAGED_DIR, "int_*buffer*.dat")))
    sample_1d = sorted(
        p
        for p in glob.glob(os.path.join(POLY_AVERAGED_DIR, "int_Pt_NPs_*.dat"))
        if "buffer" not in os.path.basename(p).lower()
    )
    if not sample_1d or not buffer_1d:
        raise RuntimeError(f"Poly integrate produced no curves under {POLY_AVERAGED_DIR}")

    # Single shared buffer for all Pt_NPs samples
    buffer_path = buffer_1d[0]
    os.makedirs(POLY_SUBTRACTED_DIR, exist_ok=True)
    sub_merged = merge_skill_params("subtract", config_path=config_path)
    q_sub_min = sub_merged.get("q_min")
    q_sub_max = sub_merged.get("q_max")
    # Prefer protocol subtract window from manifest when present
    man_sub = manifest.get("subtract") or {}
    if man_sub.get("q_min") is not None:
        q_sub_min = man_sub["q_min"]
    if man_sub.get("q_max") is not None:
        q_sub_max = man_sub["q_max"]
    if q_sub_min is None or q_sub_max is None:
        raise RuntimeError("poly subtract q_min/q_max missing from config/manifest")

    sub_by_key: Dict[str, str] = {}
    for sample_path in sample_1d:
        out_sub = subtract(
            sample_path,
            buffer_path,
            POLY_SUBTRACTED_DIR,
            q_min=float(q_sub_min),
            q_max=float(q_sub_max),
            config_path=config_path,
            use_cache=False,
        )
        sub_path = _as_scalar(out_sub.get("subtracted_1d"))
        if not sub_path or not os.path.isfile(str(sub_path)):
            raise RuntimeError(f"subtract failed for {sample_path}")
        # Map to protocol key Pt_NPs_30 etc.
        base = os.path.splitext(os.path.basename(str(sub_path)))[0]
        for key in samples_meta:
            if key in base:
                sub_by_key[key] = str(sub_path)
                break

    for d in (POLY_GUINIER_DIR, POLY_FIT_SIZES_DIR):
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d, exist_ok=True)

    guinier_by_key: Dict[str, Dict[str, Any]] = {}
    sizes_out_by_key: Dict[str, Dict[str, Any]] = {}
    for key, meta in samples_meta.items():
        sub = sub_by_key.get(key)
        if not sub:
            raise RuntimeError(f"No subtracted curve for poly key {key}")
        out_g = fit_guinier(sub, output_dir=POLY_GUINIER_DIR, use_cache=False)
        g_path = _as_scalar(out_g.get("results_path"))
        if not g_path or not os.path.isfile(str(g_path)):
            raise RuntimeError(f"Poly Guinier failed for {key}: missing results")
        parsed_g = parse_guinier_results_txt(str(g_path))
        if not parsed_g.get("rg"):
            raise RuntimeError(f"Poly Guinier failed for {key}: {g_path}")
        guinier_by_key[key] = parsed_g

        kwargs: Dict[str, Any] = {
            "output_dir": POLY_FIT_SIZES_DIR,
            "shape": str(meta.get("shape") or "spheres"),
            "use_cache": False,
            "minimal": True,
        }
        if meta.get("q_min") is not None:
            kwargs["q_min"] = float(meta["q_min"])
        if meta.get("q_max") is not None:
            kwargs["q_max"] = float(meta["q_max"])
        if str(meta.get("mode") or "") == "refine":
            kwargs["rmax_nm"] = float(meta["rmax_nm"])
            if meta.get("alpha") is not None:
                kwargs["alpha"] = float(meta["alpha"])
            kwargs["force_zero_rmin"] = str(meta.get("force_zero_rmin") or "N")
            kwargs["force_zero_rmax"] = str(meta.get("force_zero_rmax") or "N")
        out_sz = fit_sizes(sub, **kwargs)
        best = _as_scalar(out_sz.get("best_gnom_out_path"))
        if not best or not os.path.isfile(str(best)):
            raise RuntimeError(f"fit_sizes produced no .out for {key}")
        sizes_out_by_key[key] = out_sz

    return {
        "manifest": manifest,
        "sub_by_key": sub_by_key,
        "guinier_by_key": guinier_by_key,
        "sizes_out_by_key": sizes_out_by_key,
    }


def compare_polydisperse_to_reference(run_state: Dict[str, Any]):
    """Compare poly Guinier Rg and fit_sizes D(R)/rmax to reference_poly goldens."""
    samples_meta: Dict[str, Any] = (run_state["manifest"].get("samples") or {})
    guinier_by_key = run_state["guinier_by_key"]
    sizes_out_by_key = run_state["sizes_out_by_key"]
    failures: List[str] = []
    guinier_rows = []
    sizes_rows = []

    for key, meta in samples_meta.items():
        ref_g_path = os.path.join(REFERENCE_POLY_DIR, meta["guinier_results"])
        ref_g = parse_guinier_results_txt(ref_g_path)
        pipe_g = guinier_by_key[key]
        rg_err = _rel_err(pipe_g["rg"], ref_g["rg"])
        guinier_rows.append(
            {
                "reference": f"{key}:{os.path.basename(ref_g_path)}",
                "generated": key,
                "metric": float(rg_err),
            }
        )
        if rg_err > POLY_RG_REL_TOL:
            failures.append(f"{key} Guinier Rg rel_err={rg_err:.3f} > {POLY_RG_REL_TOL}")

        out_sz = sizes_out_by_key[key]
        best = _as_scalar(out_sz.get("best_gnom_out_path"))
        ref_out_path = os.path.join(REFERENCE_POLY_DIR, meta["best_out_path"])
        parsed_ref = parse_gnom_out(ref_out_path)
        parsed_pipe = parse_gnom_out(str(best))
        # Prefer rmax from passport / distribution extent
        rmax_ref = parsed_ref.get("real_space_rmax")
        rmax_pipe = parsed_pipe.get("real_space_rmax")
        # Fall back to manifest pinned rmax when refining
        if rmax_ref is None and meta.get("rmax_nm") is not None:
            rmax_ref = float(meta["rmax_nm"])
        if rmax_pipe is None and meta.get("rmax_nm") is not None and str(meta.get("mode")) == "refine":
            rmax_pipe = float(meta["rmax_nm"])
        dist_metric = _pr_distribution_metric(parsed_ref, parsed_pipe)
        metric = float(dist_metric) if np.isfinite(dist_metric) else float("nan")
        if rmax_ref is not None and rmax_pipe is not None:
            rmax_err = _rel_err(float(rmax_pipe), float(rmax_ref))
            if not np.isfinite(metric):
                metric = float(rmax_err)
            if rmax_err > POLY_RMAX_REL_TOL:
                failures.append(f"{key} fit_sizes rmax rel_err={rmax_err:.3f} > {POLY_RMAX_REL_TOL}")
        sizes_rows.append(
            {
                "reference": f"{key}:{os.path.basename(ref_out_path)}",
                "generated": os.path.basename(str(best)),
                "metric": metric if np.isfinite(metric) else 0.0,
            }
        )
        if np.isfinite(dist_metric) and dist_metric > POLY_DR_DIST_METRIC_MAX:
            failures.append(
                f"{key} fit_sizes D(R) metric={dist_metric:.3f} > {POLY_DR_DIST_METRIC_MAX}"
            )

    ok = len(failures) == 0
    return ok, failures, {"guinier": guinier_rows, "fit_sizes": sizes_rows}

