from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

from autosaxs.core.event_bus import EventBus, EventType
from autosaxs.core.utils import load_config

from ..skill_wrap import apply_batch, run_with_cache
from ..common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    coerce_dat_path_expression,
    coerce_config_path_expression,
    expand_files_from_unwrapped,
)


def fit_mixture(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    q_min_nm: Optional[float] = None,
    q_max_nm: Optional[float] = None,
    use_cache: bool = True,
) -> Dict[str, Union[str, List[str]]]:
    """
    Run MIXTURE fits on a 1D subtracted curve, select the best model by BIC, and write a comparison plot, size distribution plot, and results CSV.

    ### Arguments

    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where the MIXTURE outputs are written.
    - `config_path` (str | None, default `None`): Path to the autosaxs YAML config (must include a `mixture` section). Required for this skill.
    - `q_min_nm` (float | None, default `None`): Optional q minimum bound (nm^-1) for the fitting range.
    - `q_max_nm` (float | None, default `None`): Optional q maximum bound (nm^-1) for the fitting range.
    - `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

    Important constraint:

    - If you set `q_max_nm`, you must also set `q_min_nm` (otherwise the skill raises `ValueError`).

    ### Returns

    `dict[str, str]` with:

    - `output_subdir`: The subdirectory that contains MIXTURE outputs.
    - `comparison_path`: Path to the MIXTURE comparison plot.
    - `distributions_path`: Path to the MIXTURE size distributions plot.
    - `results_csv_path`: Path to the MIXTURE results CSV.

    ### Python usage

    ```python
    from autosaxs.skill import fit_mixture

    out = fit_mixture(
        profile="subtracted/sub_sample_01.dat",
        output_dir="mixture",
        config_path="config_autosaxs.yml",
        q_min_nm=0.8,
        q_max_nm=2.5,
        use_cache=True,
    )

    print(out["results_csv_path"])
    ```

    ### CLI usage

    ```bash
    autosaxs fit_mixture subtracted/sub_sample_01.dat --output-dir mixture --config-path config_autosaxs.yml \
      --q-min-nm 0.8 --q-max-nm 2.5
    ```
    """
    q_range_nm: Optional[tuple] = None
    if q_min_nm is not None or q_max_nm is not None:
        if q_min_nm is None:
            raise ValueError("fit_mixture: q_min_nm must be set when q_max_nm is set")
        q_range_nm = (q_min_nm, q_max_nm)
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    profile = coerce_dat_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("fit_mixture input files must have .dat extension")
    if config_path is None:
        raise ValueError("fit_mixture requires config_path (path to YAML config containing a 'mixture' section)")
    config_path = coerce_config_path_expression(config_path)
    cfg_path = config_path.unwrap()[0]
    if not os.path.isfile(cfg_path):
        raise FileNotFoundError(f"fit_mixture config_path not found: {cfg_path!r}")
    input_batch = [{"profile": p, "config_path": cfg_path} for p in expanded_profiles]
    return _fit_mixture_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        q_range_nm=q_range_nm,
    )


@apply_batch(stem_from_keys="profile", per_sample_subdir="always")
@run_with_cache(
    path_keys_for_hash=["profile", "config_path"],
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
) -> Dict[str, Union[str, List[str]]]:
    """
    Run MIXTURE fits and select best by BIC; write comparison plot, distribution plot, results CSV.
    """
    from .mixture import fit_mixtures as _fit_mixtures

    _ = use_cache, sample_index
    profile = input_paths.get("profile")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("fit_mixture requires input_paths['profile']")

    cfg = config
    if not cfg:
        cfg_path = input_paths.get("config_path")
        if isinstance(cfg_path, list):
            cfg_path = cfg_path[0] if cfg_path else None
        if not cfg_path or not isinstance(cfg_path, str):
            raise ValueError("fit_mixture requires config_path (or an explicit in-memory config)")
        if not os.path.isfile(cfg_path):
            raise FileNotFoundError(f"fit_mixture requires an existing config_path, got: {cfg_path!r}")
        cfg = load_config(cfg_path)
    mixture_cfg = dict((cfg or {}).get("mixture") or {})
    required = ("maxit", "r_min", "r_max", "poly_min", "poly_max", "max_nph")
    missing = [k for k in required if k not in mixture_cfg]
    if missing:
        raise ValueError(f"fit_mixture requires config['mixture'] keys: {missing}")

    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "MIXTURE fit…"})
    result = _fit_mixtures(
        profile,
        output_dir=output_dir,
        fast_forward=False,
        q_range_nm=q_range_nm,
        max_nph=mixture_cfg["max_nph"],
        maxit=mixture_cfg["maxit"],
        r_min=mixture_cfg["r_min"],
        r_max=mixture_cfg["r_max"],
        poly_min=mixture_cfg["poly_min"],
        poly_max=mixture_cfg["poly_max"],
    )
    if result is None:
        raise RuntimeError("fit_mixture failed")
    return {
        "output_subdir": result["output_subdir"],
        "comparison_path": result["comparison_path"],
        "distributions_path": result["distributions_path"],
        "results_csv_path": result["results_csv_path"],
    }

