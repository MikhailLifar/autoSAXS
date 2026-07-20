from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

from autosaxs.core.event_bus import EventBus, EventType

from ..config import merge_skill_params, resolve_optional_config_path
from ..skill_wrap import apply_batch, run_with_cache
from ..common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    coerce_dat_path_expression,
    coerce_config_path_expression,
    expand_files_from_unwrapped,
)

NM_TO_ANGSTROM = 10.0
ANGSTROM_TO_NM = 0.1


def _nm_to_angstrom(value_nm: float) -> float:
    return float(value_nm) * NM_TO_ANGSTROM


def _angstrom_to_nm(value_ang: float) -> float:
    return float(value_ang) * ANGSTROM_TO_NM


def _resolve_config_path(config_path: Optional[ConfigPathExpressionArg]) -> Optional[str]:
    return resolve_optional_config_path(config_path)


def _q_range_from_merged(merged: Dict, q_min_nm: Optional[float], q_max_nm: Optional[float]) -> Optional[tuple]:
    if q_min_nm is not None or q_max_nm is not None:
        if q_min_nm is None and q_max_nm is not None:
            return (None, float(q_max_nm))
        if q_min_nm is not None and q_max_nm is not None:
            return (float(q_min_nm), float(q_max_nm))
        if q_min_nm is not None:
            return (float(q_min_nm), None)
    q_min_cfg = merged.get("q_min_nm")
    q_max_cfg = merged.get("q_max_nm")
    if q_min_cfg is None and q_max_cfg is None:
        return None
    q_min_v = None if q_min_cfg is None else float(q_min_cfg)
    q_max_v = None if q_max_cfg is None else float(q_max_cfg)
    if q_min_v is None and q_max_v is None:
        return None
    return (q_min_v, q_max_v)


def _rmax_nm_from_fit_sizes(
    profile: str,
    output_dir: str,
    event_bus: Optional[EventBus],
) -> float:
    """Run fit_sizes in-process and return the selected GNOM ``--rmax`` (nm)."""
    from ..fit_sizes import _fit_sizes_paths

    sizes_dir = os.path.join(output_dir, "_fit_sizes_defaults")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "model_mixture: running fit_sizes (in-process)…"})
    result = _fit_sizes_paths(
        input_paths={"profile": profile},
        output_dir=sizes_dir,
        shape="spheres",
        event_bus=event_bus,
        use_cache=False,
    )
    fit_params_path = result.get("fit_params_path")
    if not fit_params_path or not os.path.isfile(str(fit_params_path)):
        raise RuntimeError("model_mixture: fit_sizes did not write fit_params_path; cannot derive r_max.")
    with open(str(fit_params_path), "r", encoding="utf-8") as fp:
        doc = yaml.safe_load(fp) or {}
    rmax_nm = doc.get("rmax_nm")
    if rmax_nm is None:
        raise RuntimeError("model_mixture: fit_sizes fit_params missing rmax_nm; cannot derive r_max.")
    rmax_nm_f = float(rmax_nm)
    if rmax_nm_f <= 0:
        raise ValueError(f"model_mixture: invalid rmax_nm from fit_sizes: {rmax_nm_f}")
    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {"text": f"model_mixture: fit_sizes completed (rmax={rmax_nm_f:.4g} nm)."},
        )
    return rmax_nm_f


def _resolve_mixture_radius_params(
    *,
    profile: str,
    output_dir: str,
    event_bus: Optional[EventBus],
    user_r_min: Optional[float] = None,
    user_r_max: Optional[float] = None,
    user_poly_min: Optional[float] = None,
    user_poly_max: Optional[float] = None,
) -> Dict[str, float]:
    """Resolve MIXTURE radius bounds: inputs/defaults in nm, returned values in Å for ATSAS."""
    if user_r_max is not None:
        r_max_nm = float(user_r_max)
    else:
        r_max_nm = _rmax_nm_from_fit_sizes(profile, output_dir, event_bus)

    r_min_nm = float(user_r_min) if user_r_min is not None else 0.1
    poly_max_nm = float(user_poly_max) if user_poly_max is not None else 0.5 * r_max_nm
    poly_min_nm = float(user_poly_min) if user_poly_min is not None else 0.05
    return {
        "r_min": _nm_to_angstrom(r_min_nm),
        "r_max": _nm_to_angstrom(r_max_nm),
        "poly_min": _nm_to_angstrom(poly_min_nm),
        "poly_max": _nm_to_angstrom(poly_max_nm),
    }


def model_mixture(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    q_min_nm: Optional[float] = None,
    q_max_nm: Optional[float] = None,
    maxit: Optional[int] = None,
    r_min: Optional[float] = None,
    r_max: Optional[float] = None,
    poly_min: Optional[float] = None,
    poly_max: Optional[float] = None,
    max_nph: Optional[int] = None,
    plot_I_q: Optional[bool] = None,
    plot_logI_logq: Optional[bool] = None,
    plot_logI_q: Optional[bool] = None,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    SAXS / small-angle x-ray scattering: run MIXTURE fits on a 1D subtracted curve, select the best model by BIC, and write a comparison plot, size distribution plot, and results CSV (mixture / multi-population size distributions).

    Prerequisites:

    - Requires the ATSAS `mixture` executable to be available on `PATH` (this skill shells out to `mixture`).

    ### Arguments

    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where the MIXTURE outputs are written.
    - `config_path` (str | None, default `None`): Optional path to a YAML config file with a `model_mixture` section. When omitted, bundled defaults apply.
    - `q_min_nm` / `q_max_nm` (float | None): Optional q bounds (nm^-1); set via CLI or user config (not in bundled template).
    - `maxit`, `max_nph`: MIXTURE parameters; defaults from bundled `model_mixture` section when omitted.
    - `plot_I_q` (bool, default `False`): Write I vs q fit comparison plot (labels show BIC).
    - `plot_logI_logq` (bool, default `False`): Write log I vs log q fit comparison plot (labels show BIC_log).
    - `plot_logI_q` (bool, default `True`): Write log I vs q fit comparison plot (labels show chi2).
    - `r_min` (float | None): MIXTURE minimum radius (nm). If omitted, defaults to `0.1`. Converted to Å internally for ATSAS MIXTURE.
    - `r_max` (float | None): MIXTURE maximum radius (nm). If omitted, defaults to `rmax_nm` from in-process `fit_sizes`.
    - `poly_min` (float | None): MIXTURE minimum polydispersity (nm). If omitted, defaults to `0.05`.
    - `poly_max` (float | None): MIXTURE maximum polydispersity (nm). If omitted, defaults to `0.5 × r_max`.
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    Important constraint:

    - If you set `q_max_nm`, you must also set `q_min_nm` (otherwise the skill raises `ValueError`).

    ### Returns

    `dict[str, str]` with:

    - `output_subdir`: The subdirectory that contains MIXTURE outputs.
    - `comparison_path`: Path to the I vs q comparison plot (empty when `plot_I_q=False`).
    - `comparison_loglog_path`: Path to the log I vs log q comparison plot (empty when `plot_logI_logq=False`).
    - `comparison_log_path`: Path to the log I vs q comparison plot (empty when `plot_logI_q=False`).
    - `distributions_path`: Path to the MIXTURE size distributions plot.
    - `results_csv_path`: Path to the MIXTURE results CSV.
    - `r_max_nm` / `poly_max_nm`: Resolved MIXTURE radius bounds (nm), including defaults when omitted.
    - `r_min_nm` / `poly_min_nm`: Resolved MIXTURE radius/polydispersity floors (nm).

    ### Python usage

    ```python
    from autosaxs.skill import model_mixture

    out = model_mixture(
        profile="subtracted/sub_sample_01.dat",
        output_dir="mixture",
        q_min_nm=0.8,
        q_max_nm=2.5,
        use_cache=False,
    )

    print(out["results_csv_path"])
    ```

    ### CLI usage

    ```bash
    autosaxs model-mixture subtracted/sub_sample_01.dat --output-dir mixture --q-min-nm 0.8 --q-max-nm 2.5
    ```
    """
    if q_max_nm is not None and q_min_nm is None:
        raise ValueError("model_mixture: q_min_nm must be set when q_max_nm is set")
    cfg_path = _resolve_config_path(config_path)
    merged = merge_skill_params(
        "model_mixture",
        config_path=cfg_path,
        q_min_nm=q_min_nm,
        q_max_nm=q_max_nm,
        maxit=maxit,
        r_min=r_min,
        r_max=r_max,
        poly_min=poly_min,
        poly_max=poly_max,
        max_nph=max_nph,
        plot_I_q=plot_I_q,
        plot_logI_logq=plot_logI_logq,
        plot_logI_q=plot_logI_q,
    )
    required = ("maxit", "max_nph")
    missing = [k for k in required if k not in merged or merged[k] is None]
    if missing:
        raise ValueError(f"model_mixture requires parameters (config or CLI): {missing}")
    q_range_nm = _q_range_from_merged(merged, q_min_nm, q_max_nm)
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    profile = coerce_dat_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("model_mixture input files must have .dat extension")
    input_batch = [{"profile": p} for p in expanded_profiles]
    mixture_param_overrides: Dict[str, Any] = {
        "maxit": int(merged["maxit"]),
        "max_nph": int(merged["max_nph"]),
        "plot_I_q": bool(merged.get("plot_I_q", False)),
        "plot_logI_logq": bool(merged.get("plot_logI_logq", False)),
        "plot_logI_q": bool(merged.get("plot_logI_q", True)),
    }
    for key in ("r_min", "r_max", "poly_min", "poly_max"):
        if key in merged and merged[key] is not None:
            mixture_param_overrides[key] = float(merged[key])
    return _model_mixture_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        q_range_nm=q_range_nm,
        mixture_param_overrides=mixture_param_overrides,
    )


@apply_batch(stem_from_keys="profile", per_sample_subdir="always")
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash_keys=["q_range_nm", "mixture_param_overrides"],
    include_config_in_hash=False,
    warn_if_no_cache=True,
)
def _model_mixture_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
    q_range_nm: Optional[tuple] = None,
    mixture_param_overrides: Optional[Dict] = None,
) -> Dict[str, Union[str, List[str]]]:
    """
    Run MIXTURE fits and select best by BIC; write comparison plot, distribution plot, results CSV.
    """
    from .mixture import fit_mixtures as _fit_mixtures

    _ = config, use_cache, sample_index
    profile = input_paths.get("profile")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("model_mixture requires input_paths['profile']")
    if not mixture_param_overrides:
        raise ValueError("model_mixture requires mixture_param_overrides")

    radius_params = _resolve_mixture_radius_params(
        profile=str(profile),
        output_dir=output_dir,
        event_bus=event_bus,
        user_r_min=mixture_param_overrides.get("r_min"),
        user_r_max=mixture_param_overrides.get("r_max"),
        user_poly_min=mixture_param_overrides.get("poly_min"),
        user_poly_max=mixture_param_overrides.get("poly_max"),
    )
    mixture_params = {
        "maxit": int(mixture_param_overrides["maxit"]),
        "max_nph": int(mixture_param_overrides["max_nph"]),
        **radius_params,
    }

    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "MIXTURE fit…"})
    result = _fit_mixtures(
        profile,
        output_dir=output_dir,
        fast_forward=False,
        q_range_nm=q_range_nm,
        max_nph=mixture_params["max_nph"],
        maxit=mixture_params["maxit"],
        r_min=mixture_params["r_min"],
        r_max=mixture_params["r_max"],
        poly_min=mixture_params["poly_min"],
        poly_max=mixture_params["poly_max"],
        plot_I_q=bool(mixture_param_overrides.get("plot_I_q", False)),
        plot_logI_logq=bool(mixture_param_overrides.get("plot_logI_logq", False)),
        plot_logI_q=bool(mixture_param_overrides.get("plot_logI_q", True)),
    )
    if result is None:
        raise RuntimeError("model_mixture failed")
    from autosaxs.core.report_fragments import write_skill_report_fragments
    from autosaxs.core.utils import _strip_sub_int_prefix as _strip_base

    obase = _strip_base(os.path.splitext(os.path.basename(profile))[0])
    dest_dir = str(result["output_subdir"])
    comp = _first_comparison_basename(result)
    dist = os.path.basename(result["distributions_path"])
    csvb = os.path.basename(result["results_csv_path"])
    md_parts = [
        "### MIXTURE (multi-phase spheres)\n",
        f"Best model: **{result.get('best_label', '')}**; BIC_log = {result.get('BIC_log')}\n",
    ]
    if comp:
        md_parts.append(f"![Comparison I(q)]({comp})\n")
    md_parts.append(f"![Size distributions]({dist})\n")
    summary_refs = [
        {
            "role": "mixture_scores_preview",
            "path": csvb,
            "format": "csv",
            "row": 0,
            "columns": ["label", "BIC_log", "n_phases"],
        },
    ]
    if comp:
        summary_refs.append({"role": "mixture_comparison_png", "path": comp, "format": "png"})
    write_skill_report_fragments(
        dest_dir,
        obase,
        "model_mixture",
        "".join(md_parts),
        summary_references=summary_refs,
    )
    return {
        "output_subdir": result["output_subdir"],
        "comparison_path": result.get("comparison_path", ""),
        "comparison_loglog_path": result.get("comparison_loglog_path", ""),
        "comparison_log_path": result.get("comparison_log_path", ""),
        "distributions_path": result["distributions_path"],
        "results_csv_path": result["results_csv_path"],
        "best_label": result.get("best_label", ""),
        "r_min_nm": _angstrom_to_nm(mixture_params["r_min"]),
        "r_max_nm": _angstrom_to_nm(mixture_params["r_max"]),
        "poly_min_nm": _angstrom_to_nm(mixture_params["poly_min"]),
        "poly_max_nm": _angstrom_to_nm(mixture_params["poly_max"]),
    }


def _first_comparison_basename(result: Dict[str, Any]) -> str:
    for key in ("comparison_log_path", "comparison_path", "comparison_loglog_path"):
        path = str(result.get(key) or "").strip()
        if path:
            return os.path.basename(path)
    return ""
