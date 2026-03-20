# Skills paradigm: entry points for skills.
# Wrappers and common functionality live in autosaxs/skill_wrap.py.
# See repos/docs/skills_paradigm.md.

from __future__ import annotations

import os
import sys
import time
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd
import yaml

from .event_bus import EventBus, EventType
from .viewer import PLTViewer
from .processor import (
    IntegratorExtended,
    autocalib,
    integrate_2d_to_1d,
    subtract_buffer,
)
from .guinier import run_guinier_analysis, find_guinier_region
from .utils import (
    ATSAS_BIN_PREFIX,
    load_config,
    read_saxs,
    read_data,
    read_from_tiff,
    write_data,
    write_saxs,
    write_saxs_atsas_format,
    read_bodies_cif,
    compute_dammif_descriptors,
    calc_chi2,
    load_saxs_1d_any,
    ensure_q_nm,
)

# Re-export wrappers and cache helpers for callers (e.g. tests) that import from skill
from .skill_wrap import (
    CACHE_FILENAME,
    apply_batch,
    check_output_integrity,
    compute_input_hash,
    read_cache,
    run_with_cache,
    write_cache,
    _strip_sub_int_prefix,
)


# ---------------------------------------------------------------------------
# Skill: calibrate
# Public entry point (CLI-compatible): calibrate(...)
# Internal implementation: _calibrate_paths(...)
# ---------------------------------------------------------------------------


def calibrate(
    calib_image: str,
    config_path: str,
    output_dir: str = ".",
    *,
    mask: Optional[str] = None,
    use_cache: bool = True,
) -> Dict[str, str]:
    """
    Calibrate detector geometry from a calibration image. Public entry point.

    Positional args mirror CLI: calib_image, config_path.
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stderr))
    input_paths: Dict[str, Union[str, List[str]]] = {"calib_image": calib_image, "config": config_path}
    if mask is not None:
        input_paths["mask"] = mask
    return _calibrate_paths(input_paths=input_paths, output_dir=output_dir, event_bus=bus, use_cache=use_cache)


@apply_batch(stem_from_keys="calib_image")
@run_with_cache(
    path_keys_for_hash=["calib_image", "config", "mask"],
    kwargs_for_hash=None,
    include_config_in_hash=False,
)
def _calibrate_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = True,
    sample_index: int = 0,
) -> Dict[str, str]:
    """
    Calibrate detector geometry from a calibration image (center refinement, ring search,
    geometry refinement). Produces an integrator and refined parameters for 2D→1D integration.

    Inputs: input_paths['calib_image'], input_paths.get('config') (path to config file),
    optional input_paths.get('mask'). config can also be passed in-memory. Notable kwargs: none.

    Outputs: integrator_dir, refined_path, calibration_plot_path, calibration_mask_path.
    """
    calib_image = input_paths.get("calib_image")
    if isinstance(calib_image, list):
        calib_image = calib_image[0] if calib_image else None
    if not calib_image or not os.path.isfile(calib_image):
        raise FileNotFoundError("calibrate requires input_paths['calib_image']")
    cfg = config
    if cfg is None and input_paths.get("config"):
        cfg_path = input_paths["config"] if isinstance(input_paths["config"], str) else input_paths["config"][0]
        cfg = load_config(cfg_path)
    if not cfg:
        raise ValueError("calibrate requires config (in-memory or path in input_paths['config'])")
    mask_path = input_paths.get("mask")
    if isinstance(mask_path, list):
        mask_path = mask_path[0] if mask_path else None
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "Calibration: ring search…"})
    result = autocalib(calib_image, cfg, mask_path=mask_path)
    os.makedirs(output_dir, exist_ok=True)
    integrator_dir = os.path.join(output_dir, "integrator")
    result["integrator"].to_disk(integrator_dir)
    refined_path = os.path.join(output_dir, "refined.yml")
    with open(refined_path, "w") as f:
        yaml.dump(result["refined"], f, default_flow_style=False)
    calibration_plot_path = os.path.join(output_dir, "calibration.png")
    calibration_mask_path = os.path.join(output_dir, "calibration_mask.png")
    PLTViewer.view_calibration(
        img_data=result["calib_data"],
        tiff_path=calib_image,
        center_y_px=result["center_y_px"],
        center_x_px=result["center_x_px"],
        clusters=result["clusters"],
        rings=result["rings"],
        curve_calibrated=result["curve_calibrated"],
        theoretical_peaks=result["theoretical_peaks"],
        show_duration=None,
        plotFilePath=calibration_plot_path,
    )
    PLTViewer.view_mask(
        result["calib_data"],
        result["integrator"].mask,
        tiff_path=calib_image,
        show_duration=None,
        plotFilePath=calibration_mask_path,
    )
    return {
        "integrator_dir": integrator_dir,
        "refined_path": refined_path,
        "calibration_plot_path": calibration_plot_path,
        "calibration_mask_path": calibration_mask_path,
    }


# ---------------------------------------------------------------------------
# Skill: integrate
# Public entry point (CLI-compatible): integrate(...)
# Internal implementation: _integrate_paths(...)
# ---------------------------------------------------------------------------


def integrate(
    images: List[str],
    integrator_dir: str,
    output_dir: str = ".",
    *,
    npt: int = 1000,
    use_cache: bool = True,
) -> Dict[str, Union[str, List[str]]]:
    """
    Integrate 2D SAXS images to 1D curves. Public entry point.

    Positional args mirror CLI: images (one or more), integrator_dir.
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stderr))
    return _integrate_paths(
        input_paths={"images": images, "integrator_dir": integrator_dir},
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        npt=npt,
    )


@apply_batch(stem_from_keys="images", single_output_dir=True)
@run_with_cache(
    path_keys_for_hash=["images", "integrator_dir"],
    kwargs_for_hash_keys=["npt"],
    include_config_in_hash=False,
)
def _integrate_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = True,
    sample_index: int = 0,
    npt: int = 1000,
) -> Dict[str, Union[str, List[str]]]:
    """
    Integrate 2D SAXS images to 1D curves (q, I, σ) using a calibrated integrator.

    Inputs: input_paths['images'] (list of 2D image paths), input_paths['integrator_dir'].
    Notable kwargs: npt (number of points in q).

    Outputs: integrated_1d (list of paths to 1D curves).
    """
    images = input_paths.get("images")
    if isinstance(images, str):
        images = [images]
    if not images:
        raise ValueError("integrate requires input_paths['images']")
    integrator_dir = input_paths.get("integrator_dir")
    if isinstance(integrator_dir, list):
        integrator_dir = integrator_dir[0] if integrator_dir else None
    if not integrator_dir or not os.path.isdir(integrator_dir):
        raise FileNotFoundError("integrate requires input_paths['integrator_dir']")
    integrator = IntegratorExtended.from_disk(integrator_dir)
    os.makedirs(output_dir, exist_ok=True)
    integrated = []
    for idx, im_path in enumerate(images):
        if event_bus:
            event_bus.publish(EventType.MESSAGE, {"text": f"Integration {idx + 1}/{len(images)}…"})
        data = read_from_tiff(im_path)
        base = os.path.splitext(os.path.basename(im_path))[0]
        dest = os.path.join(output_dir, f"int_{base}.dat")
        q, I, sigma = integrate_2d_to_1d(integrator, data, npt=npt, destpath=dest)
        integrated.append(dest)
    return {"integrated_1d": integrated}


# ---------------------------------------------------------------------------
# Skill: subtract
# Public entry point (CLI-compatible): subtract(...)
# Internal implementation: _subtract_paths(...)
# ---------------------------------------------------------------------------


def subtract(
    sample_1d: str,
    buffer_1d: str,
    output_dir: str = ".",
    *,
    method: str = "match_tail",
    q_min: Optional[float] = None,
    q_max: Optional[float] = None,
    use_cache: bool = True,
) -> Dict[str, str]:
    """
    Subtract buffer from sample 1D profile. Public entry point.

    Positional args mirror CLI: sample_1d, buffer_1d.
    """
    match_tail_ops: Optional[Dict] = None
    if q_min is not None or q_max is not None:
        if q_min is None:
            raise ValueError("subtract: q_min must be set when q_max is set")
        match_tail_ops = {"q_range_abs": (q_min, q_max)}
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stderr))
    return _subtract_paths(
        input_paths={"sample_1d": sample_1d, "buffer_1d": buffer_1d},
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        method=method,
        match_tail_ops=match_tail_ops,
    )


@apply_batch(stem_from_keys="sample_1d", single_output_dir=True)
@run_with_cache(
    path_keys_for_hash=["sample_1d", "buffer_1d"],
    kwargs_for_hash_keys=["method", "match_tail_ops"],
    include_config_in_hash=False,
)
def _subtract_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = True,
    sample_index: int = 0,
    method: str = "match_tail",
    match_tail_ops: Optional[Dict] = None,
) -> Dict[str, str]:
    """
    Subtract buffer from sample 1D profile (e.g. match-tail scaling). Writes subtracted curve.

    Inputs: input_paths['sample_1d'], input_paths['buffer_1d'] (paired or by convention).
    Notable kwargs: method, match_tail_ops.

    Outputs: subtracted_1d, diff_plot_path, sub_plot_path.
    """
    sample_1d = input_paths.get("sample_1d")
    buffer_1d = input_paths.get("buffer_1d")
    if isinstance(sample_1d, list):
        sample_1d = sample_1d[0] if sample_1d else None
    if isinstance(buffer_1d, list):
        buffer_1d = buffer_1d[0] if buffer_1d else None
    if not sample_1d or not os.path.isfile(sample_1d):
        raise FileNotFoundError("subtract requires input_paths['sample_1d']")
    if not buffer_1d or not os.path.isfile(buffer_1d):
        raise FileNotFoundError("subtract requires input_paths['buffer_1d']")
    os.makedirs(output_dir, exist_ok=True)
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(sample_1d))[0])
    dest = os.path.join(output_dir, f"sub_{base}.dat")
    q, I_sub, I_buff_scaled, sigma_sub, sigma_buff_scaled = subtract_buffer(
        buffer_1d, sample_1d, dest,
        method=method, match_tail_ops=match_tail_ops,
    )
    q_sample, I_sample, sigma_sample, _ = read_saxs(sample_1d)
    diff_plot_path = os.path.join(output_dir, f"diff_{base}.png")
    sub_plot_path = os.path.join(output_dir, f"sub_{base}.png")
    PLTViewer.view_curves(
        q_sample, I_sample, "sample",
        q, I_buff_scaled, "buffer scaled",
        sigmas=(sigma_sample, sigma_buff_scaled),
        legend=True, plotFilePath=diff_plot_path, save=False,
    )
    PLTViewer.view_curves(
        q, I_sub, "sample",
        sigmas=(sigma_sub,), legend=True,
        plotFilePath=sub_plot_path, save=False,
    )
    return {
        "subtracted_1d": dest,
        "diff_plot_path": diff_plot_path,
        "sub_plot_path": sub_plot_path,
    }


# ---------------------------------------------------------------------------
# Skill: plot
# Public entry point (CLI-compatible): plot(...)
# Internal implementation: _plot_paths(...)
# ---------------------------------------------------------------------------


def plot(
    profile: str,
    output_dir: str = ".",
    *,
    guinier_q_min: Optional[float] = None,
    guinier_q_max: Optional[float] = None,
    use_cache: bool = True,
) -> Dict[str, str]:
    """
    Generate standard plots for a 1D profile. Public entry point.

    Positional args mirror CLI: profile.
    """
    guinier_region: Optional[tuple] = None
    if guinier_q_min is not None or guinier_q_max is not None:
        if guinier_q_min is None:
            raise ValueError("plot: guinier_q_min must be set when guinier_q_max is set")
        guinier_region = (guinier_q_min, guinier_q_max)
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stderr))
    return _plot_paths(
        input_paths={"profile": profile},
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        guinier_region=guinier_region,
    )


@apply_batch(stem_from_keys="profile", single_output_dir=True)
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash_keys=["guinier_region"],
    include_config_in_hash=False,
)
def _plot_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = True,
    sample_index: int = 0,
    guinier_region: Optional[tuple] = None,
) -> Dict[str, str]:
    """
    Generate standard plots for a 1D profile: Guinier, Kratky, log–log; optionally write Guinier-range .dat.

    Inputs: input_paths['profile'] (1D path). Notable kwargs: guinier_region (q_min, q_max) for optional guinier_dat.

    Outputs: guinier_plot_path, kratky_plot_path, loglog_plot_path, optional guinier_dat_path.
    """
    profile = input_paths.get("profile")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("plot requires input_paths['profile']")
    os.makedirs(output_dir, exist_ok=True)
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(profile))[0])
    q, I, _, _ = read_saxs(profile)
    guinier_plot_path = os.path.join(output_dir, f"guinier_{base}.png")
    kratky_plot_path = os.path.join(output_dir, f"kratky_{base}.png")
    loglog_plot_path = os.path.join(output_dir, f"loglog_{base}.png")
    guinier_dat_path = os.path.join(output_dir, f"guinier_{base}.dat")

    write_data(
        guinier_dat_path,
        pd.DataFrame(np.stack([q * q, np.log(I)], axis=-1), columns=["q^2", "log(I)"]),
        metadata={"type": "guinier", "parent": profile},
    )
    PLTViewer.view_curves(
        q * q, np.log(I), "log(I) vs q^2",
        xlabel="q^2 (nm-2)", ylabel="log(I) (a.u.)",
        legend=True, plotFilePath=guinier_plot_path,
    )
    write_data(
        os.path.join(output_dir, f"kratky_{base}.dat"),
        pd.DataFrame(np.stack([q, q * q * I], axis=-1), columns=["q", "I * q^2"]),
        metadata={"type": "kratky", "parent": profile},
    )
    PLTViewer.view_curves(
        q, q * q * I, "I * q^2 vs q",
        xlabel="q (nm-1)", ylabel="I * q^2 (a.u.)",
        legend=True, plotFilePath=kratky_plot_path,
    )
    write_data(
        os.path.join(output_dir, f"loglog_{base}.dat"),
        pd.DataFrame(np.stack([np.log(q), np.log(I)], axis=-1), columns=["log(q)", "log(I)"]),
        metadata={"type": "loglog", "parent": profile},
    )
    PLTViewer.view_curves(
        np.log(q), np.log(I), "log(I) vs log(q)",
        xlabel="log(q)", ylabel="log(I)",
        legend=True, plotFilePath=loglog_plot_path,
    )
    return {
        "guinier_plot_path": guinier_plot_path,
        "kratky_plot_path": kratky_plot_path,
        "loglog_plot_path": loglog_plot_path,
        "guinier_dat_path": guinier_dat_path,
    }


# ---------------------------------------------------------------------------
# Skill: guinier_analysis
# Public entry point (CLI-compatible): guinier_analysis(...)
# Internal implementation: _guinier_analysis_paths(...)
# ---------------------------------------------------------------------------


def guinier_analysis(
    profile: str,
    output_dir: str = ".",
    *,
    use_cache: bool = True,
) -> Dict[str, str]:
    """
    Run Guinier analysis on a 1D profile. Public entry point.

    Positional args mirror CLI: profile.
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stderr))
    return _guinier_analysis_paths(
        input_paths={"profile": profile},
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
    )


@apply_batch(stem_from_keys="profile")
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash=None,
    include_config_in_hash=False,
)
def _guinier_analysis_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = True,
    sample_index: int = 0,
) -> Dict[str, str]:
    """
    Run Guinier analysis on a 1D profile (first5, first10, autorg, adaptive).
    Chosen result is always adaptive when available. Writes results file and ATSAS-format .dat for downstream.

    Inputs: input_paths['profile'] (1D path).

    Outputs: results_path, atsas_dat_path, guinier_region_path (yml with chosen Rg, I0, interval).
    """
    profile = input_paths.get("profile")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("guinier_analysis requires input_paths['profile']")
    os.makedirs(output_dir, exist_ok=True)
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(profile))[0])
    results_path = os.path.join(output_dir, f"{base}_results.txt")
    atsas_dat_path = os.path.join(output_dir, f"{base}_atsas.dat")
    guinier_region_path = os.path.join(output_dir, f"{base}_guinier_region.yml")

    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "Guinier analysis…"})

    q_arr, I_arr, sigma_arr = load_saxs_1d_any(profile)
    q_arr, I_arr, sigma_arr = ensure_q_nm(q_arr, I_arr, sigma_arr)
    write_saxs_atsas_format(atsas_dat_path, q_arr, I_arr, sigma_arr)

    guinier_results = run_guinier_analysis(
        q_arr, I_arr, sigma_arr, atsas_dat_path=atsas_dat_path
    )

    guinier_region = None
    rg_source = None
    if guinier_results.get("chosen") is not None:
        ch_int = guinier_results.get("chosen_interval")
        chosen_result = guinier_results.get(guinier_results["chosen"]) or {}
        guinier_region = {
            "rg": guinier_results.get("chosen_Rg"),
            "i0": guinier_results.get("chosen_I0"),
            "q_min": ch_int[0] if ch_int else None,
            "q_max": ch_int[1] if ch_int else None,
            "r_squared": guinier_results.get("chosen_quality"),
            "n_points": guinier_results.get("chosen_n_points"),
            "sigma_rg": chosen_result.get("sigma_rg"),
            "sigma_i0": chosen_result.get("sigma_i0"),
        }
        rg_source = guinier_results["chosen"]

    # Write results file (Guinier section)
    with open(results_path, "w") as f:
        f.write("SAXS Guinier Analysis Results\n")
        f.write("============================\n")
        f.write(f"Input file: {profile}\n")
        f.write(f"Analysis date: {time.ctime()}\n\n")
        f.write("Chosen Guinier result (used downstream):\n")
        if guinier_region is not None:
            sr = guinier_region.get("sigma_rg")
            si = guinier_region.get("sigma_i0")
            f.write(f"  Source = {rg_source}\n")
            f.write(f"  Rg = {guinier_region['rg']:.4f} nm\n")
            if sr is not None:
                f.write(f"  Rg StDev = {sr:.4g} nm\n")
            if guinier_region.get("i0") is not None:
                f.write(f"  I(0) = {guinier_region['i0']:.4g}\n")
            if si is not None:
                f.write(f"  I(0) StDev = {si:.4g}\n")
            qmn, qmx = guinier_region.get("q_min"), guinier_region.get("q_max")
            if qmn is not None and qmx is not None:
                f.write(f"  q range = [{qmn:.5g}, {qmx:.5g}] nm^-1\n")
            if guinier_region.get("n_points") is not None:
                f.write(f"  n points = {guinier_region['n_points']}\n")
            if guinier_region.get("r_squared") is not None:
                f.write(f"  R^2 = {guinier_region['r_squared']:.4f}\n")
            val_r2 = guinier_results.get("chosen_validation_r2")
            if val_r2 is not None:
                f.write(f"  validation R^2 (on [q_max/2, q_max]) = {val_r2:.4f}\n")
            cl = guinier_results.get("classification")
            if cl is not None:
                f.write(f"  classification ([0, q_max/2]) = {cl}\n")
        else:
            f.write("  No valid Guinier result chosen.\n")
        f.write("\nAll Guinier methods (Rg, n_points, fit_quality, guinier_interval, validation_r2):\n")
        for method in ("first5", "first10", "autorg", "adaptive"):
            r = guinier_results.get(method)
            mark = " [CHOSEN]" if guinier_results.get("chosen") == method else ""
            if r is not None:
                rg = r.get("Rg")
                np_ = r.get("n_points")
                qq = r.get("fit_quality")
                interval = r.get("guinier_interval")
                val_r2 = r.get("validation_r2")
                rg_s = f"{rg:.4f}" if rg is not None else "N/A"
                np_s = str(np_) if np_ is not None else "N/A"
                qq_s = f"{qq:.4f}" if qq is not None else "N/A"
                int_s = f"[{interval[0]:.5g}, {interval[1]:.5g}]" if interval and interval[0] is not None and interval[1] is not None else "N/A"
                val_s = f"{val_r2:.4f}" if val_r2 is not None else "N/A"
                f.write(f"  {method}: Rg={rg_s} nm, n_points={np_s}, fit_quality={qq_s}, interval={int_s}, validation_r2={val_s}{mark}\n")
            else:
                f.write(f"  {method}: (no result)\n")

    if guinier_region is not None:
        with open(guinier_region_path, "w") as f:
            yaml.dump(guinier_region, f, default_flow_style=False)
    else:
        with open(guinier_region_path, "w") as f:
            yaml.dump({}, f, default_flow_style=False)

    return {
        "results_path": results_path,
        "atsas_dat_path": atsas_dat_path,
        "guinier_region_path": guinier_region_path,
    }


# ---------------------------------------------------------------------------
# Skill: fit_mixture
# Public entry point (CLI-compatible): fit_mixture(...)
# Internal implementation: _fit_mixture_paths(...)
# ---------------------------------------------------------------------------


def fit_mixture(
    profile: str,
    output_dir: str = ".",
    *,
    q_min_nm: Optional[float] = None,
    q_max_nm: Optional[float] = None,
    use_cache: bool = True,
) -> Dict[str, str]:
    """
    Run MIXTURE fits and select best by BIC. Public entry point.

    Positional args mirror CLI: profile.
    """
    q_range_nm: Optional[tuple] = None
    if q_min_nm is not None or q_max_nm is not None:
        if q_min_nm is None:
            raise ValueError("fit_mixture: q_min_nm must be set when q_max_nm is set")
        q_range_nm = (q_min_nm, q_max_nm)
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stderr))
    return _fit_mixture_paths(
        input_paths={"profile": profile},
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        q_range_nm=q_range_nm,
    )


@apply_batch(stem_from_keys="profile")
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash_keys=["q_range_nm"],
    include_config_in_hash=False,
)
def _fit_mixture_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = True,
    sample_index: int = 0,
    q_range_nm: Optional[tuple] = None,
) -> Dict[str, str]:
    """
    Run MIXTURE fits (1-/2-/3-phase × Gaussian/Schultz–Zimm, sphere-only), select best by BIC,
    write comparison plot, distribution plot, results CSV.

    Inputs: input_paths['profile'] (one 1D subtracted curve).
    Notable kwargs: q_range_nm (q_min, q_max) in nm⁻¹.

    Outputs: output_subdir, comparison_path, distributions_path, results_csv_path.
    """
    from .mixture import fit_mixtures as _fit_mixtures

    profile = input_paths.get("profile")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("fit_mixture requires input_paths['profile']")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "MIXTURE fit…"})
    result = _fit_mixtures(
        profile, output_dir=output_dir, fast_forward=False, q_range_nm=q_range_nm
    )
    if result is None:
        raise RuntimeError("fit_mixture failed")
    return {
        "output_subdir": result["output_subdir"],
        "comparison_path": result["comparison_path"],
        "distributions_path": result["distributions_path"],
        "results_csv_path": result["results_csv_path"],
    }


# ---------------------------------------------------------------------------
# Skill: fit_bodies
# Public entry point (CLI-compatible): fit_bodies(...)
# Internal implementation: _fit_bodies_paths(...)
# ---------------------------------------------------------------------------


def fit_bodies(
    profile: str,
    output_dir: str = ".",
    *,
    use_cache: bool = True,
) -> Dict[str, str]:
    """
    Run ATSAS bodies and export fits. Public entry point.

    Positional args mirror CLI: profile.
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stderr))
    return _fit_bodies_paths(
        input_paths={"profile": profile},
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
    )

# BODIES shape names (must match ATSAS bodies; order from controller)
BODIES_SHAPES_LIST = [
    "cylinder", "dumbbell", "ellipsoid", "elliptic-cylinder", "hollow-cylinder",
    "hollow-sphere", "parallelepiped", "rotation-ellipsoid",
]


@apply_batch(stem_from_keys="profile")
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash=None,
    include_config_in_hash=False,
)
def _fit_bodies_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = True,
    sample_index: int = 0,
) -> Dict[str, str]:
    """
    Run ATSAS bodies on a 1D profile for multiple shapes; export fits (fir, PNG, yml, csv).

    Inputs: input_paths['profile'] (1D path).
    Outputs: output_subdir, and bodies fit files (fir, png, yml, csv) under output_subdir.
    """
    import re

    profile = input_paths.get("profile")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("fit_bodies requires input_paths['profile']")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "BODIES fit…"})
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(profile))[0])
    os.makedirs(output_dir, exist_ok=True)
    bodies_call = os.path.join(ATSAS_BIN_PREFIX, "bodies")
    bodies_prefix = os.path.join(output_dir, "bodies_fit")
    os.system(f"{bodies_call} --prefix={bodies_prefix} {profile}")

    from .viewer import PLTViewer

    q, I, sigma, _ = read_saxs(profile)
    fits_data = []
    to_plot = []
    for shape in BODIES_SHAPES_LIST:
        fir_path = os.path.join(output_dir, f"bodies_fit-{shape}.fir")
        if not os.path.isfile(fir_path):
            continue
        with open(fir_path, "r") as f:
            first_line = f.readline().strip()
        params_dict = {}
        match = re.match(r"^(?P<shape>[\w\-]+):\s*(?P<params>.+)$", first_line)
        if match:
            for param_assignment in match.group("params").split(","):
                param_assignment = param_assignment.strip()
                kv = re.match(r"^(\w+)\s*=\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)$", param_assignment)
                if kv:
                    params_dict[kv.group(1)] = float(kv.group(2))
        data = np.loadtxt(fir_path, skiprows=1, dtype=np.float64)
        q_fit, I_fit, sigma_bodies = data[:, 0], data[:, 3], data[:, 2]
        idx = q <= q_fit[-1]
        q_int, I_int = q[idx], I[idx]
        sigma_interp = np.interp(q_int, q_fit, sigma_bodies)
        I_fit_interp = np.interp(q_int, q_fit, I_fit)
        chi2 = calc_chi2(I_int, I_fit_interp, sigma_interp)
        fits_data.append((shape, params_dict, chi2, q_int, I_fit_interp))
        to_plot.extend([q_int, I_fit_interp, f"{shape}; $\\chi^2$: {chi2:.2f}"])
        PLTViewer.plot_3d_views_and_scattering(
            (shape, params_dict), q_int, I_int, sigma_interp, I_fit_interp,
            plotFilePath=os.path.join(output_dir, f"{shape}_view.png"),
        )
    bodies_fits_yml = os.path.join(output_dir, "bodies_fits.yml")
    bodies_fits_csv = os.path.join(output_dir, "bodies_fits.csv")
    bodies_fits_png = os.path.join(output_dir, f"{base}_fits.png")
    if fits_data:
        fits_yml = {s: {**p, "chi2": float(c)} for s, p, c, _q, _i in fits_data}
        with open(bodies_fits_yml, "w") as f:
            yaml.dump(fits_yml, f, default_flow_style=False)
        q_max = max(to_plot[i][-1] for i in range(0, len(to_plot), 3))
        idx = q <= q_max
        q_csv, I_exp_csv = q[idx], I[idx]
        csv_cols = ["q", "exp"] + [s for s, *_ in fits_data]
        csv_arrays = [q_csv, I_exp_csv] + [
            np.interp(q_csv, _q, _i) for _s, _p, _c, _q, _i in fits_data
        ]
        pd.DataFrame(dict(zip(csv_cols, csv_arrays))).to_csv(bodies_fits_csv, index=False)
        to_plot = [q[idx], I[idx], {"label": "exp", "lw": 4}] + to_plot
        PLTViewer.view_curves(
            *to_plot, sigmas=(sigma[idx],),
            title=f"Fits comparison for\n{base}", xlabel="q (nm-1)", ylabel="I", legend=True,
            plotFilePath=bodies_fits_png,
        )
    return {
        "output_subdir": output_dir,
    }


# ---------------------------------------------------------------------------
# Skill: fit_dammif
# Public entry point (CLI-compatible): fit_dammif(...)
# Internal implementation: _fit_dammif_paths(...)
# ---------------------------------------------------------------------------


def fit_dammif(
    profile: str,
    output_dir: str = ".",
    *,
    gnom_path: Optional[str] = None,
    use_cache: bool = True,
) -> Dict[str, str]:
    """
    Run ATSAS dammif and export results. Public entry point.

    Positional args mirror CLI: profile.
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stderr))
    input_paths: Dict[str, Union[str, List[str]]] = {"profile": profile}
    if gnom_path is not None:
        input_paths["gnom_path"] = gnom_path
    return _fit_dammif_paths(
        input_paths=input_paths,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
    )

DAMMIF_REPS_NUM = 2


@apply_batch(stem_from_keys="profile")
@run_with_cache(
    path_keys_for_hash=["profile", "gnom_path"],
    kwargs_for_hash=None,
    include_config_in_hash=False,
)
def _fit_dammif_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = True,
    sample_index: int = 0,
) -> Dict[str, str]:
    """
    Run ATSAS dammif (ab initio shape reconstruction) on a 1D profile; produce shape models and descriptors.

    Inputs: input_paths['profile'] (1D path). Optionally input_paths['gnom_path'] for GNOM .out (if not set, profile used).
    Outputs: output_subdir, dammif output files under output_subdir.
    """
    profile = input_paths.get("profile")
    gnom_path = input_paths.get("gnom_path") or profile
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if isinstance(gnom_path, list):
        gnom_path = gnom_path[0] if gnom_path else None
    if not gnom_path or not os.path.isfile(gnom_path):
        raise FileNotFoundError("fit_dammif requires input_paths['profile'] or input_paths['gnom_path']")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "DAMMIF fit…"})
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(gnom_path))[0])
    os.makedirs(output_dir, exist_ok=True)
    dammif_call = os.path.join(ATSAS_BIN_PREFIX, "dammif")
    dammif_prefix = os.path.join(output_dir, "dammif")
    os.system(f"for i in $(seq 1 {DAMMIF_REPS_NUM}); do {dammif_call} --prefix={dammif_prefix}-$i --mode=fast {gnom_path}; done")

    from .viewer import PLTViewer

    profile_1d = profile or gnom_path
    q, I, sigma, _ = read_saxs(profile_1d)
    to_plot = []
    fits_data = []
    for i in range(DAMMIF_REPS_NUM):
        fir_path = os.path.join(output_dir, f"dammif-{i+1}.fir")
        cif_path = os.path.join(output_dir, f"dammif-{i+1}-1.cif")
        if not os.path.isfile(fir_path):
            continue
        data = np.loadtxt(fir_path, skiprows=1, dtype=np.float64)
        q_fit, I_fit, sigma_d = data[:, 0], data[:, 3], data[:, 2]
        q_fit = q_fit * 10.0
        idx = q <= q_fit[-1]
        q_int, I_int = q[idx], I[idx]
        sigma_interp = np.interp(q_int, q_fit, sigma_d)
        I_fit_interp = np.interp(q_int, q_fit, I_fit)
        chi2 = calc_chi2(I_int, I_fit_interp, sigma_interp)
        atoms = read_bodies_cif(cif_path) if os.path.isfile(cif_path) else None
        descr = compute_dammif_descriptors(atoms) if atoms is not None else {}
        fits_data.append((f"dammif-{i}", {**descr, "chi2": float(chi2)}, q_int, I_fit_interp))
        to_plot.extend([q_int, I_fit_interp, f"dammif-{i}; $\\chi^2$: {chi2:.2f}"])
        if atoms is not None:
            PLTViewer.plot_3d_views_and_scattering(
                atoms, q_int, I_int, sigma_interp, I_fit_interp,
                plotFilePath=os.path.join(output_dir, f"dammif-{i}_view.png"),
            )
    dammif_fits_yml = os.path.join(output_dir, "dammif_fits.yml")
    dammif_fits_csv = os.path.join(output_dir, "dammif_fits.csv")
    dammif_fits_png = os.path.join(output_dir, f"{base}_fits.png")
    if fits_data:
        fits_yml = {k: {kk: float(vv) for kk, vv in d.items()} for k, d, _q, _i in fits_data}
        with open(dammif_fits_yml, "w") as f:
            yaml.dump(fits_yml, f, default_flow_style=False)
        q_max = max(to_plot[i][-1] for i in range(0, len(to_plot), 3))
        idx = q <= q_max
        csv_cols = ["q", "exp"] + [k for k, *_ in fits_data]
        csv_arrays = [q[idx], I[idx]] + [np.interp(q[idx], _q, _i) for _k, _d, _q, _i in fits_data]
        pd.DataFrame(dict(zip(csv_cols, csv_arrays))).to_csv(dammif_fits_csv, index=False)
        to_plot = [q[idx], I[idx], {"label": "exp", "lw": 4}] + to_plot
        PLTViewer.view_curves(
            *to_plot, sigmas=(sigma[idx],),
            title=f"Fits comparison for\n{base}", xlabel="q (nm-1)", ylabel="I", legend=True,
            plotFilePath=dammif_fits_png,
        )
    return {"output_subdir": output_dir}


# ---------------------------------------------------------------------------
# Report skills (entry points; main logic in report.py)
# ---------------------------------------------------------------------------


def report_individual(
    directory: str,
    basename: str,
    output_dir: str = ".",
    *,
    output_path: Optional[str] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Build individual PDF report for one sample from existing pipeline directory.
    Scans directory for paths matching basename, assembles report_data, writes PDF.
    Returns dict with 'report_pdf_path'.
    """
    from .report import write_individual_report_pdf
    _ = use_cache  # report generation does not use caching; kept for CLI parity
    if output_path is None:
        output_path = os.path.join(output_dir, f"{basename}_report.pdf")
    path = write_individual_report_pdf(directory, basename, output_path=output_path)
    return {"report_pdf_path": path}


def report_summary(
    directory: str,
    output_dir: str = ".",
    *,
    output_path: Optional[str] = None,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Build summary PDF report from existing pipeline directory.
    Discovers samples from subtracted/ and related dirs, writes summary PDF.
    Returns dict with 'report_pdf_path'.
    """
    from .report import write_summary_report_pdf
    _ = use_cache  # report generation does not use caching; kept for CLI parity
    if output_path is None:
        output_path = os.path.join(output_dir, "summary_report.pdf")
    path = write_summary_report_pdf(directory, output_path=output_path)
    return {"report_pdf_path": path}
