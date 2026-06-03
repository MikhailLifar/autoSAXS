from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np

from .deps import (
    EventBus,
    EventType,
    PLTViewer,
    _strip_sub_int_prefix,
    apply_batch,
    read_saxs,
    run_with_cache,
    subtract_buffer,
)
from .common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    SingletonDatPathExpressionArg,
    coerce_dat_path_expression,
    coerce_singleton_dat_path_expression,
    expand_files_from_unwrapped,
)
from .config import merge_skill_params, resolve_optional_config_path


def _resolve_config_path(config_path: Optional[ConfigPathExpressionArg]) -> Optional[str]:
    return resolve_optional_config_path(config_path)


def subtract(
    sample_1d: DatPathExpressionArg,
    buffer_1d: SingletonDatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    method: Optional[str] = None,
    q_min: Optional[float] = None,
    q_max: Optional[float] = None,
    sample_form: Optional[str] = None,
    buffer_form: Optional[str] = None,
    point_match_factor: Optional[float] = None,
    scaling_factor: Optional[float] = None,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    SAXS / small-angle x-ray scattering: subtract a buffer curve from a sample 1D profile (background subtraction). Scaling uses either `point_match` (default)
    or legacy `match_tail`, optionally restricted to a q window (`q_min` / `q_max`).

    ### Arguments

    - `sample_1d` (str): Sample path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `buffer_1d` (str): Path to the buffer 1D `.dat` curve (must be an existing file).
    - `output_dir` (str, default `.`): Directory where subtraction outputs are written.
    - `config_path` (str | None, default `None`): Optional path to a YAML config file with a `subtract` section. When omitted, bundled defaults apply for method/forms; q-window keys come from CLI or user file only.
    - `method` (str | None, default `None`): `point_match` or `match_tail`. Defaults from bundled config when omitted.
    - `q_min` (float | None, default `None`): Lower bound of q-range (CLI or user config; not in bundled template).
    - `q_max` (float | None, default `None`): Upper bound of q-range; for `point_match` the match uses this as q intersect (upper edge of the window).
    - `sample_form` / `buffer_form` (str | None): For `point_match` only — each is `linear`, `Porod`, or `Porod-plus-linear`.
    - `point_match_factor` (float | None, default `None`): For `point_match`, scale satisfies `point_match_factor * I_sample_fit(q_max) = scale * I_buffer_fit(q_max)`.
    - `scaling_factor` (float | None, default `None`): If provided, overrides automatic scaling and uses this factor directly (must be finite and > 0).
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    Required q window:

    - `q_min` and `q_max` must both be set (CLI, Python API, or user config). There are no defaults; the skill raises `ValueError` if either is missing.

    ### Returns

    `dict[str, str]` with:

    - `subtracted_1d`: Path to the subtracted curve `.dat`.
    - `diff_plot_path`: Path to a diff plot PNG.
    - `diff_log_plot_path`: Path to a diff plot PNG with log(I) vs q.
    - `sub_plot_path`: Path to a subtracted curve plot PNG.

    Subtraction quality (`correct` or `over-subtracted`) is written into the subtracted `.dat` metadata
    (``subtract.correctness``) and into per-sample report fragments (individual Markdown and summary YAML).

    ### Python usage

    ```python
    from autosaxs.skill import subtract

    out = subtract(
        sample_1d="integration/int_sample_01.dat",
        buffer_1d="integration/int_buffer.dat",
        output_dir="subtracted",
        method="point_match",
        q_min=4.0,
        q_max=6.0,
        use_cache=False,
    )

    print(out["subtracted_1d"])
    ```

    ### CLI usage

    ```bash
    autosaxs subtract integration/int_sample_01.dat integration/int_buffer.dat \
      --output-dir subtracted --method point_match --q-min 4.0 --q-max 6.0
    ```
    """
    cfg_path = _resolve_config_path(config_path)
    merged = merge_skill_params(
        "subtract",
        config_path=cfg_path,
        method=method,
        q_min=q_min,
        q_max=q_max,
        sample_form=sample_form,
        buffer_form=buffer_form,
        point_match_factor=point_match_factor,
        scaling_factor=scaling_factor,
    )
    method_eff = str(merged.get("method", "point_match")).strip().lower().replace("-", "_")
    q_min_eff = merged.get("q_min", q_min)
    q_max_eff = merged.get("q_max", q_max)
    if q_min_eff is None or q_max_eff is None:
        raise ValueError("subtract: q_min and q_max must both be set (no defaults)")
    sample_form_eff = merged.get("sample_form", "Porod-plus-linear")
    buffer_form_eff = merged.get("buffer_form", "linear")
    point_match_factor_eff = float(merged.get("point_match_factor", 0.995))
    scaling_factor_eff = merged.get("scaling_factor", scaling_factor)
    match_tail_ops: Dict = {"q_range_abs": (float(q_min_eff), float(q_max_eff))}
    if method_eff == "point_match":
        match_tail_ops["sample_form"] = sample_form_eff
        match_tail_ops["buffer_form"] = buffer_form_eff
        match_tail_ops["point_match_factor"] = point_match_factor_eff
    match_tail_ops_out: Dict = match_tail_ops
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    sample_1d = coerce_dat_path_expression(sample_1d)
    buffer_1d = coerce_singleton_dat_path_expression(buffer_1d)
    buff = buffer_1d.unwrap()[0]
    if not buff or not os.path.isfile(buff):
        raise FileNotFoundError("subtract requires buffer_1d to be an existing file")
    expanded_samples = expand_files_from_unwrapped(sample_1d.unwrap(), kind="1d_dat")
    for p in expanded_samples:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("subtract input sample_1d files must have .dat extension")
    input_batch = [{"sample_1d": p, "buffer_1d": buff} for p in expanded_samples]
    return _subtract_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        method=method_eff,
        match_tail_ops=match_tail_ops_out,
        scaling_factor=scaling_factor_eff,
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
    use_cache: bool = False,
    sample_index: int = 0,
    method: str = "point_match",
    match_tail_ops: Optional[Dict] = None,  # required q_range_abs when called via subtract()
    scaling_factor: Optional[float] = None,
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
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
        buffer_1d,
        sample_1d,
        dest,
        method=method,
        match_tail_ops=match_tail_ops,
        scaling_factor=scaling_factor,
    )
    q_sample, I_sample, sigma_sample, _ = read_saxs(sample_1d)
    diff_plot_path = os.path.join(output_dir, f"diff_{base}.png")
    diff_log_plot_path = os.path.join(output_dir, f"diff_log_{base}.png")
    sub_plot_path = os.path.join(output_dir, f"sub_{base}.png")
    PLTViewer.view_curves(
        q_sample,
        I_sample,
        "sample",
        q,
        I_buff_scaled,
        "buffer scaled",
        sigmas=(sigma_sample, sigma_buff_scaled),
        legend=True,
        plotFilePath=diff_plot_path,
        save=False,
    )
    I_sample_log = np.where(np.asarray(I_sample, dtype=float) > 0.0, np.log(np.asarray(I_sample, dtype=float)), np.nan)
    I_buff_log = np.where(np.asarray(I_buff_scaled, dtype=float) > 0.0, np.log(np.asarray(I_buff_scaled, dtype=float)), np.nan)
    PLTViewer.view_curves(
        q_sample,
        I_sample_log,
        "sample (log)",
        q,
        I_buff_log,
        "buffer scaled (log)",
        xlabel="q (nm-1)",
        ylabel="ln(I) (a.u.)",
        legend=True,
        plotFilePath=diff_log_plot_path,
        save=False,
    )
    PLTViewer.view_curves(
        q,
        I_sub,
        "sample",
        sigmas=(sigma_sub,),
        legend=True,
        plotFilePath=sub_plot_path,
        save=False,
    )
    from autosaxs.core.report_fragments import write_skill_report_fragments

    _, _, _sigma, meta = read_saxs(dest)
    subtract_meta = meta.get("subtract") if isinstance(meta, dict) else {}
    correctness = (
        str(subtract_meta.get("correctness"))
        if isinstance(subtract_meta, dict) and subtract_meta.get("correctness")
        else "correct"
    )
    md_lines = [
        "### Buffer subtraction\n",
        f"Scaling method: **{method}**.\n",
        f"Subtraction quality: **{correctness}**.\n",
        f"![Difference sample vs scaled buffer]({os.path.basename(diff_plot_path)})\n",
        f"![Difference log scale]({os.path.basename(diff_log_plot_path)})\n",
        f"![Subtracted curve]({os.path.basename(sub_plot_path)})\n",
    ]
    summary_refs = [
        {"role": "subtracted_curve", "path": os.path.basename(dest), "format": "saxs_dat", "display_name": "subtracted"},
    ]
    write_skill_report_fragments(
        output_dir,
        base,
        "subtract",
        "".join(md_lines),
        summary_references=summary_refs,
        summary_extra={"correctness": correctness},
    )
    return {
        "subtracted_1d": dest,
        "diff_plot_path": diff_plot_path,
        "diff_log_plot_path": diff_log_plot_path,
        "sub_plot_path": sub_plot_path,
    }

