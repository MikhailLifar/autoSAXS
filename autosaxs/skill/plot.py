from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np
import pandas as pd

from .deps import (
    EventBus,
    EventType,
    PLTViewer,
    _strip_sub_int_prefix,
    apply_batch,
    read_saxs,
    run_with_cache,
    write_data,
)
from .common import PathExpressionArg, coerce_path_expression, expand_files_from_unwrapped


def plot(
    profile: PathExpressionArg,
    output_dir: str = ".",
    *,
    guinier_q_min: Optional[float] = None,
    guinier_q_max: Optional[float] = None,
    use_cache: bool = True,
) -> Dict[str, Union[str, List[str]]]:
    """
    Generate standard plots for a 1D curve:

    - Guinier plot (log(I) vs q^2)
    - Kratky plot (I*q^2 vs q)
    - log-log plot (log(I) vs log(q))

    Also writes a Guinier `.dat` file (ln(I) vs q²) used downstream.

    ### Arguments

    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where plot files are written.
    - `guinier_q_min` (float | None, default `None`): Lower q bound for selecting Guinier range (enables `guinier_dat_path`).
    - `guinier_q_max` (float | None, default `None`): Upper q bound for selecting Guinier range.
    - `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

    Important constraint:

    - If you set `guinier_q_max`, you must also set `guinier_q_min` (otherwise the skill raises `ValueError`).

    ### Returns

    `dict[str, str]` with:

    - `guinier_plot_path`: Path to the Guinier PNG.
    - `kratky_plot_path`: Path to the Kratky PNG.
    - `loglog_plot_path`: Path to the log-log PNG.
    - `guinier_dat_path`: Path to the Guinier `.dat` (q², ln(I)) written by the skill (always written; independent of `guinier_q_min/max`).

    ### Python usage

    ```python
    from autosaxs.skill import plot

    out = plot(
        profile="subtracted/sub_sample_01.dat",
        output_dir="plots",
        guinier_q_min=0.01,
        guinier_q_max=0.05,
        use_cache=True,
    )

    print(out["guinier_dat_path"])
    ```

    ### CLI usage

    ```bash
    autosaxs plot subtracted/sub_sample_01.dat --output-dir plots --guinier-q-min 0.01 --guinier-q-max 0.05
    ```
    """
    guinier_region: Optional[tuple] = None
    if guinier_q_min is not None or guinier_q_max is not None:
        if guinier_q_min is None:
            raise ValueError("plot: guinier_q_min must be set when guinier_q_max is set")
        guinier_region = (guinier_q_min, guinier_q_max)
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    profile = coerce_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("plot input files must have .dat extension")
    input_batch = [{"profile": p} for p in expanded_profiles]
    return _plot_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
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
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index, guinier_region
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
        pd.DataFrame({"q^2": q * q, "log(I)": np.log(I)}),
        metadata={"type": "guinier", "parent": profile},
    )
    PLTViewer.view_curves(
        q * q,
        np.log(I),
        "ln(I) vs q^2",
        xlabel="q^2 (nm-2)",
        ylabel="ln(I) (a.u.)",
        legend=True,
        plotFilePath=guinier_plot_path,
    )
    write_data(
        os.path.join(output_dir, f"kratky_{base}.dat"),
        pd.DataFrame({"q": q, "I * q^2": q * q * I}),
        metadata={"type": "kratky", "parent": profile},
    )
    PLTViewer.view_curves(
        q,
        q * q * I,
        "I * q^2 vs q",
        xlabel="q (nm-1)",
        ylabel="I * q^2 (a.u.)",
        legend=True,
        plotFilePath=kratky_plot_path,
    )
    write_data(
        os.path.join(output_dir, f"loglog_{base}.dat"),
        pd.DataFrame({"log(q)": np.log(q), "log(I)": np.log(I)}),
        metadata={"type": "loglog", "parent": profile},
    )
    PLTViewer.view_curves(
        np.log(q),
        np.log(I),
        "ln(I) vs ln(q)",
        xlabel="ln(q)",
        ylabel="ln(I)",
        legend=True,
        plotFilePath=loglog_plot_path,
    )
    return {
        "guinier_plot_path": guinier_plot_path,
        "kratky_plot_path": kratky_plot_path,
        "loglog_plot_path": loglog_plot_path,
        "guinier_dat_path": guinier_dat_path,
    }

