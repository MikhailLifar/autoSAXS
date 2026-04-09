from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from .deps import (
    EventBus,
    EventType,
    _strip_sub_int_prefix,
    apply_batch,
    ensure_q_nm,
    load_saxs_1d_any,
    run_guinier_analysis,
    run_with_cache,
    write_saxs_atsas_format,
)
from .common import PathExpressionArg, coerce_path_expression, expand_files_from_unwrapped


def guinier_analysis(
    profile: PathExpressionArg,
    output_dir: str = ".",
    *,
    use_cache: bool = True,
) -> Dict[str, Union[str, List[str]]]:
    """
    Run Guinier analysis on a 1D profile and write results + ATSAS `.dat` for downstream tools.
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    profile = coerce_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("guinier_analysis input files must have .dat extension")
    input_batch = [{"profile": p} for p in expanded_profiles]
    return _guinier_analysis_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
    )


@apply_batch(stem_from_keys="profile", per_sample_subdir="never")
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
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
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

    guinier_results = run_guinier_analysis(q_arr, I_arr, sigma_arr, atsas_dat_path=atsas_dat_path)

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
                int_s = (
                    f"[{interval[0]:.5g}, {interval[1]:.5g}]"
                    if interval and interval[0] is not None and interval[1] is not None
                    else "N/A"
                )
                val_s = f"{val_r2:.4f}" if val_r2 is not None else "N/A"
                f.write(
                    f"  {method}: Rg={rg_s} nm, n_points={np_s}, fit_quality={qq_s}, interval={int_s}, validation_r2={val_s}{mark}\n"
                )
            else:
                f.write(f"  {method}: (no result)\n")

    with open(guinier_region_path, "w") as f:
        yaml.dump(guinier_region or {}, f, default_flow_style=False)

    return {"results_path": results_path, "atsas_dat_path": atsas_dat_path, "guinier_region_path": guinier_region_path}

