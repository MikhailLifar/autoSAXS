"""McSAS3 form-free D(R) skill (``model_dr_mc`` / ``autosaxs model-dr-mc``)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from autosaxs.core.event_bus import EventBus, EventType

from ..common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    coerce_dat_path_expression,
    expand_files_from_unwrapped,
)
from ..config import merge_skill_params, resolve_optional_config_path
from ..skill_wrap import apply_batch, run_with_cache

__all__ = ["model_dr_mc", "_model_dr_mc_paths"]


def _resolve_config_path(config_path: Optional[ConfigPathExpressionArg]) -> Optional[str]:
    return resolve_optional_config_path(config_path)


def _q_range_from_merged(
    merged: Dict,
    q_min_nm: Optional[float],
    q_max_nm: Optional[float],
) -> tuple[Optional[float], Optional[float]]:
    if q_min_nm is not None or q_max_nm is not None:
        return (
            None if q_min_nm is None else float(q_min_nm),
            None if q_max_nm is None else float(q_max_nm),
        )
    q_min_cfg = merged.get("q_min_nm")
    q_max_cfg = merged.get("q_max_nm")
    return (
        None if q_min_cfg is None else float(q_min_cfg),
        None if q_max_cfg is None else float(q_max_cfg),
    )


def model_dr_mc(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    q_min_nm: Optional[float] = None,
    q_max_nm: Optional[float] = None,
    n_rep: Optional[int] = None,
    n_contrib: Optional[int] = None,
    conv_crit: Optional[float] = None,
    n_cores: Optional[int] = None,
    nbins: Optional[int] = None,
    n_bin: Optional[int] = None,
    max_iter: Optional[int] = None,
    sld: Optional[float] = None,
    sld_solvent: Optional[float] = None,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str], float, int]]:
    """
    SAXS / small-angle x-ray scattering: recover a form-free volume-weighted size distribution
    \(D(R)\) with per-bin uncertainties using McSAS3 Monte Carlo fitting (polydisperse spheres).

    Fits an ensemble of independent sphere-contribution models to a subtracted 1D curve, then
    histograms the recovered radii. Bin heights are volume-weighted; error bars are the sample
    standard deviation across independent repetitions. For publication-quality uncertainty on
    \(D(R)\), raise ``n_rep`` to 50–100 (default 5 is for interactive / pipeline use).

    Prerequisites:

    - Python package ``mcsas3`` (installed with autosaxs).
    - Sphere form factor only in this skill (McSAS3 internal ``mcsas_sphere``).

    ### Arguments

    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where McSAS outputs are written (one subdirectory per profile).
    - `config_path` (str | None, default `None`): Optional YAML/config with a `model_dr_mc` section. When omitted, bundled defaults apply.
    - `q_min_nm` / `q_max_nm` (float | None): Optional q bounds (nm^-1) for the fit window.
    - `n_rep` (int, default `5`): Independent MC repetitions. Mean \(D(R)\) and per-bin \(\sigma\) come from this ensemble; use 50–100 for publication.
    - `n_contrib` (int, default `300`): Number of sphere contributions in each MC model.
    - `conv_crit` (float, default `1`): Reduced-\(\chi^2\) convergence target. Raise if experimental \(\sigma_I\) are too optimistic and runs never finish.
    - `n_cores` (int, default `0`): Parallel workers for repetitions (`0` = autodetect).
    - `nbins` (int, default `100`): Rebin count for input \(I(q)\) before fitting.
    - `n_bin` (int, default `50`): Number of bins in the post-fit log-\(R\) volume-weighted histogram.
    - `max_iter` (int, default `20000`): Max MC iterations per repetition.
    - `sld` / `sld_solvent` (float): Scattering-length densities for absolute scaling (`1e-6 Å^-2`). Relative \(I(q)\) still yields a useful relative \(D(R)\).
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    Important constraint:

    - If you set `q_max_nm`, you must also set `q_min_nm` (otherwise the skill raises `ValueError`).

    ### Returns

    `dict` with:

    - `output_subdir`: Per-sample output directory.
    - `state_path`: McSAS3 HDF5/NeXus state (`.nxs`).
    - `dr_csv_path`: CSV of \(R\), \(dR\), \(D\), \(D_\mathrm{std}\).
    - `stats_path`: YAML with gof, modes, peaks, resolved limits.
    - `handoff_path`: Compact YAML hints for `model_mixture`.
    - `fit_png_path` / `dr_png_path` / `result_card_png_path`: Plot paths (result card may be empty on McPlot failure).
    - `n_rep`, `r_min_nm`, `r_max_nm`, `q_min_nm`, `q_max_nm`, `n_components_suggested`.

    ### Python usage

    ```python
    from autosaxs.skill import model_dr_mc

    out = model_dr_mc(
        profile="subtracted/sub_sample_01.dat",
        output_dir="mcsas",
        n_rep=5,
        use_cache=False,
    )
    print(out["dr_png_path"])
    ```

    ### CLI usage

    ```bash
    autosaxs model-dr-mc subtracted/sub_sample_01.dat --output-dir mcsas --n-rep 10
    ```
    """
    if q_max_nm is not None and q_min_nm is None:
        raise ValueError("model_dr_mc: q_min_nm must be set when q_max_nm is set")
    cfg_path = _resolve_config_path(config_path)
    merged = merge_skill_params(
        "model_dr_mc",
        config_path=cfg_path,
        q_min_nm=q_min_nm,
        q_max_nm=q_max_nm,
        n_rep=n_rep,
        n_contrib=n_contrib,
        conv_crit=conv_crit,
        n_cores=n_cores,
        nbins=nbins,
        n_bin=n_bin,
        max_iter=max_iter,
        sld=sld,
        sld_solvent=sld_solvent,
    )
    required = (
        "n_rep",
        "n_contrib",
        "conv_crit",
        "n_cores",
        "nbins",
        "n_bin",
        "max_iter",
        "sld",
        "sld_solvent",
    )
    missing = [k for k in required if k not in merged or merged[k] is None]
    if missing:
        raise ValueError(f"model_dr_mc requires parameters (config or CLI): {missing}")

    q_min_v, q_max_v = _q_range_from_merged(merged, q_min_nm, q_max_nm)
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))

    profile = coerce_dat_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("model_dr_mc input files must have .dat extension")
    input_batch = [{"profile": p} for p in expanded_profiles]

    overrides: Dict[str, Any] = {
        "n_rep": int(merged["n_rep"]),
        "n_contrib": int(merged["n_contrib"]),
        "conv_crit": float(merged["conv_crit"]),
        "n_cores": int(merged["n_cores"]),
        "nbins": int(merged["nbins"]),
        "n_bin": int(merged["n_bin"]),
        "max_iter": int(merged["max_iter"]),
        "sld": float(merged["sld"]),
        "sld_solvent": float(merged["sld_solvent"]),
        "q_min_nm": q_min_v,
        "q_max_nm": q_max_v,
    }
    return _model_dr_mc_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        mcsas_param_overrides=overrides,
    )


@apply_batch(stem_from_keys="profile", per_sample_subdir="always")
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash_keys=["mcsas_param_overrides"],
    include_config_in_hash=False,
    warn_if_no_cache=True,
)
def _model_dr_mc_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
    mcsas_param_overrides: Optional[Dict] = None,
) -> Dict[str, Union[str, float, int]]:
    """Run McSAS3 and write D(R) artifacts / plots for one profile."""
    from .artifacts import write_report_and_artifacts
    from .runner import run_mcsas3_dr
    from .vis import write_all_plots

    _ = config, use_cache, sample_index
    profile = input_paths.get("profile")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("model_dr_mc requires input_paths['profile']")
    if not mcsas_param_overrides:
        raise ValueError("model_dr_mc requires mcsas_param_overrides")

    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"McSAS3 D(R): n_rep={mcsas_param_overrides['n_rep']}, "
                    f"n_contrib={mcsas_param_overrides['n_contrib']}…"
                )
            },
        )

    result = run_mcsas3_dr(
        profile,
        output_dir,
        q_min_nm=mcsas_param_overrides.get("q_min_nm"),
        q_max_nm=mcsas_param_overrides.get("q_max_nm"),
        n_rep=int(mcsas_param_overrides["n_rep"]),
        n_contrib=int(mcsas_param_overrides["n_contrib"]),
        conv_crit=float(mcsas_param_overrides["conv_crit"]),
        n_cores=int(mcsas_param_overrides["n_cores"]),
        nbins=int(mcsas_param_overrides["nbins"]),
        n_bin=int(mcsas_param_overrides["n_bin"]),
        max_iter=int(mcsas_param_overrides["max_iter"]),
        sld=float(mcsas_param_overrides["sld"]),
        sld_solvent=float(mcsas_param_overrides["sld_solvent"]),
    )

    from autosaxs.core.utils import _strip_sub_int_prefix as _strip_base

    base = _strip_base(os.path.splitext(os.path.basename(str(profile)))[0])
    plot_paths = write_all_plots(result, output_dir, base)
    if plot_paths.get("result_card_error") and event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {"text": f"McSAS3 result card skipped ({plot_paths['result_card_error']})."},
        )

    art = write_report_and_artifacts(
        result=result,
        output_dir=output_dir,
        profile_path=str(profile),
        plot_paths=plot_paths,
    )

    # Drop heavy objects before returning / caching.
    result.pop("mcres", None)
    result.pop("meas", None)

    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"McSAS3 D(R) done: peaks={result.get('peaks_nm')}, "
                    f"gof={result.get('gof_mean')}"
                )
            },
        )

    return {
        "output_subdir": str(output_dir),
        "state_path": str(result["state_path"]),
        "dr_csv_path": art["dr_csv_path"],
        "stats_path": art["stats_path"],
        "handoff_path": art["handoff_path"],
        "fit_png_path": plot_paths.get("fit_png_path", ""),
        "dr_png_path": plot_paths.get("dr_png_path", ""),
        "result_card_png_path": plot_paths.get("result_card_png_path", ""),
        "n_rep": int(result["n_rep"]),
        "r_min_nm": float(result["r_min_nm"]),
        "r_max_nm": float(result["r_max_nm"]),
        "q_min_nm": float(result["q_min_nm"]),
        "q_max_nm": float(result["q_max_nm"]),
        "n_components_suggested": int(result["n_components_suggested"]),
    }
