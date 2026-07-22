from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from autosaxs.core.event_bus import EventBus, EventType
from autosaxs.core.guinier import parse_guinier_results_txt
from autosaxs.core.utils import ensure_q_nm, load_saxs_1d_any, write_saxs_atsas_format
from autosaxs.core.viewer import PLTViewer

from ..common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    coerce_dat_path_expression,
    expand_files_from_unwrapped,
)
from ..skill_wrap import _strip_sub_int_prefix, apply_batch, run_with_cache
from .guinier import run_fixed_interval_guinier, run_guinier_analysis, guinier_point_range_1based

__all__ = [
    "fit_guinier",
    "parse_guinier_results_txt",
]


def fit_guinier(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    first: Optional[int] = None,
    last: Optional[int] = None,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    SAXS / small-angle x-ray scattering: fit the Guinier region on a 1D profile (adaptive Rg, I(0), Rg span). Writes:

    - a text results file (chosen Guinier parameters and method comparison)
    - an ATSAS-format `.dat` file for downstream tools
    - a Guinier plot (ln I vs q²) with error bars and the chosen fit line

    ### Arguments

    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where analysis outputs are written.
    - `first` (int | None, default `None`): 1-based start point for a fixed-interval Guinier fit (requires `last`).
    - `last` (int | None, default `None`): 1-based end point (inclusive) for a fixed-interval Guinier fit (requires `first`).
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    ### Returns

    `dict[str, str]` with:

    - `results_path`: Path to the results text file.
    - `atsas_dat_path`: Path to the ATSAS-format `.dat` file.
    - `guinier_plot_path`: Path to the Guinier fit PNG.

    ### Python usage

    ```python
    from autosaxs.skill import fit_guinier

    out = fit_guinier(
        profile="subtracted/sub_sample_01.dat",
        output_dir="guinier",
        use_cache=False,
    )

    print(out["results_path"])
    ```

    ### CLI usage

    ```bash
    autosaxs fit-guinier subtracted/sub_sample_01.dat --output-dir guinier
    ```
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    profile = coerce_dat_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("fit_guinier input files must have .dat extension")
    input_batch = [{"profile": p} for p in expanded_profiles]
    return _fit_guinier_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        first=first,
        last=last,
    )


@apply_batch(stem_from_keys="profile", per_sample_subdir="never")
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash=None,
    kwargs_for_hash_keys=["first", "last"],
    include_config_in_hash=False,
)
def _fit_guinier_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
    first: Optional[int] = None,
    last: Optional[int] = None,
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
    profile = input_paths.get("profile")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("fit_guinier requires input_paths['profile']")
    os.makedirs(output_dir, exist_ok=True)
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(profile))[0])
    results_path = os.path.join(output_dir, f"{base}_results.txt")
    atsas_dat_path = os.path.join(output_dir, f"{base}_atsas.dat")
    guinier_plot_path = os.path.join(output_dir, f"{base}_guinier_fit.png")

    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "Guinier fit…"})

    q_arr, I_arr, sigma_arr = load_saxs_1d_any(profile)
    q_arr, I_arr, sigma_arr = ensure_q_nm(q_arr, I_arr, sigma_arr)
    write_saxs_atsas_format(atsas_dat_path, q_arr, I_arr, sigma_arr)

    user_first = first
    user_last = last
    if (user_first is None) != (user_last is None):
        raise ValueError("fit_guinier: provide both first and last for fixed-interval mode, or omit both for adaptive.")
    if user_first is not None and user_last is not None:
        guinier_results = run_fixed_interval_guinier(
            q_arr,
            I_arr,
            sigma_arr,
            first_point_1based=int(user_first),
            last_point_1based=int(user_last),
        )
    else:
        guinier_results = run_guinier_analysis(q_arr, I_arr, sigma_arr, atsas_dat_path=atsas_dat_path)

    guinier_region = None
    rg_source = None
    if guinier_results.get("chosen") is not None:
        ch_int = guinier_results.get("chosen_interval")
        chosen_result = guinier_results.get(guinier_results["chosen"]) or {}
        val_r2 = guinier_results.get("chosen_validation_r2")
        guinier_region = {
            "rg": guinier_results.get("chosen_Rg"),
            "rg_min": guinier_results.get("rg_min"),
            "rg_max": guinier_results.get("rg_max"),
            "i0": guinier_results.get("chosen_I0"),
            "q_min": ch_int[0] if ch_int else None,
            "q_max": ch_int[1] if ch_int else None,
            "n_points": guinier_results.get("chosen_n_points"),
            "fit_quality": guinier_results.get("chosen_quality"),
            "interval_r2": chosen_result.get("interval_r2"),
            "validation_r2": val_r2,
            "r_squared": guinier_results.get("chosen_quality"),
            "selection_mode": guinier_results.get("selection_mode"),
            "quality_class": guinier_results.get("quality_class"),
            "classification": guinier_results.get("classification"),
            "n_candidates": chosen_result.get("n_candidates"),
            "sigma_rg": chosen_result.get("sigma_rg"),
            "sigma_i0": chosen_result.get("sigma_i0"),
            "i_start": chosen_result.get("i_start"),
            "first_point_1based": chosen_result.get("first_point_1based"),
            "last_point_1based": chosen_result.get("last_point_1based"),
        }
        if user_first is not None and user_last is not None:
            guinier_region["first_point_1based"] = int(user_first)
            guinier_region["last_point_1based"] = int(user_last)
            guinier_region["i_start"] = int(user_first) - 1
        else:
            fp, lp = guinier_point_range_1based(guinier_region)
            if fp is not None:
                guinier_region["first_point_1based"] = fp
            if lp is not None:
                guinier_region["last_point_1based"] = lp
            if guinier_region.get("i_start") is None and guinier_region.get("first_point_1based") is not None:
                guinier_region["i_start"] = int(guinier_region["first_point_1based"]) - 1
        rg_source = guinier_results["chosen"]

        rg_plot = guinier_region.get("rg")
        i0_plot = guinier_region.get("i0")
        if rg_plot is not None and i0_plot is not None:
            PLTViewer.view_guinier_fit(
                q_arr,
                I_arr,
                rg_nm=float(rg_plot),
                i0=float(i0_plot),
                sigma=sigma_arr,
                q_min=guinier_region.get("q_min"),
                q_max=guinier_region.get("q_max"),
                title=f"Guinier fit: {base}",
                plotFilePath=guinier_plot_path,
            )

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
            rmin, rmax = guinier_region.get("rg_min"), guinier_region.get("rg_max")
            if rmin is not None and rmax is not None:
                f.write(f"  Rg span (all tried intervals) = [{rmin:.4g}, {rmax:.4g}] nm\n")
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
            fp = guinier_region.get("first_point_1based")
            lp = guinier_region.get("last_point_1based")
            if fp is not None:
                f.write(f"  first point (1-based) = {int(fp)}\n")
            if lp is not None:
                f.write(f"  last point (1-based) = {int(lp)}\n")
            i_start = guinier_region.get("i_start")
            if i_start is not None:
                f.write(f"  i_start (0-based) = {int(i_start)}\n")
            n_cand = guinier_region.get("n_candidates")
            if n_cand is not None:
                f.write(f"  n candidates = {int(n_cand)}\n")
            if guinier_region.get("fit_quality") is not None:
                f.write(f"  fit quality (selection metric) = {guinier_region['fit_quality']:.4f}\n")
            ir2 = guinier_region.get("interval_r2")
            if ir2 is not None:
                f.write(f"  interval R^2 = {ir2:.4f}\n")
            val_r2 = guinier_results.get("chosen_validation_r2")
            if val_r2 is not None:
                f.write(f"  validation R^2 (on [q_max/2, q_max]) = {val_r2:.4f}\n")
            qcls = guinier_region.get("quality_class")
            if qcls is not None:
                f.write(f"  quality class = {qcls}\n")
            sm = guinier_region.get("selection_mode")
            if sm is not None:
                f.write(f"  selection mode = {sm}\n")
            cl = guinier_region.get("classification")
            if cl is not None:
                f.write(f"  classification ([0, q_max/2]) = {cl}\n")
            f.write(f"  Guinier plot = {guinier_plot_path}\n")
        else:
            f.write("  No valid Guinier result chosen.\n")
        f.write("\nAll Guinier methods (Rg, n_points, fit_quality, guinier_interval, validation_r2):\n")
        if guinier_results.get("chosen") == "interval":
            r = guinier_results.get("interval")
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
                    f"  interval: Rg={rg_s} nm, n_points={np_s}, fit_quality={qq_s}, interval={int_s}, validation_r2={val_s} [CHOSEN]\n"
                )
        else:
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
                    extra = ""
                    if method == "adaptive":
                        rmin, rmax = r.get("rg_min"), r.get("rg_max")
                        qc = r.get("quality_class")
                        if rmin is not None and rmax is not None:
                            extra += f", rg_span=[{rmin:.4g},{rmax:.4g}]"
                        if qc is not None:
                            extra += f", quality_class={qc}"
                    f.write(
                        f"  {method}: Rg={rg_s} nm, n_points={np_s}, fit_quality={qq_s}, interval={int_s}, validation_r2={val_s}{extra}{mark}\n"
                    )
                else:
                    f.write(f"  {method}: (no result)\n")

    from autosaxs.core.report_fragments import write_skill_report_fragments

    rg_nm = None
    if isinstance(guinier_region, dict) and guinier_region.get("rg") is not None:
        try:
            rg_nm = float(guinier_region["rg"])
        except (TypeError, ValueError):
            rg_nm = None
    rg_txt = f"{rg_nm:.4f} nm" if rg_nm is not None else "N/A"
    md_lines = [
        "### Guinier fit\n",
        f"Rg ≈ **{rg_txt}**.\n",
    ]
    if os.path.isfile(guinier_plot_path):
        md_lines.append(f"![Guinier fit]({os.path.basename(guinier_plot_path)})\n")
    summary_refs = [
        {"role": "guinier_results", "path": os.path.basename(results_path), "format": "text"},
    ]
    if os.path.isfile(guinier_plot_path):
        summary_refs.append(
            {"role": "guinier_plot", "path": os.path.basename(guinier_plot_path), "format": "png"}
        )
    if rg_nm is not None:
        summary_refs.append(
            {
                "role": "rg_nm",
                "path": os.path.basename(results_path),
                "format": "text",
                "display_name": "Rg (nm)",
            }
        )
    write_skill_report_fragments(
        output_dir,
        base,
        "fit_guinier",
        "".join(md_lines),
        summary_references=summary_refs,
    )
    return {
        "results_path": results_path,
        "atsas_dat_path": atsas_dat_path,
        "guinier_plot_path": guinier_plot_path,
    }
