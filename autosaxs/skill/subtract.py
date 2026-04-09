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
    PathExpressionArg,
    SingletonPathExpressionArg,
    coerce_path_expression,
    coerce_singleton_path_expression,
    expand_files_from_unwrapped,
)


def subtract(
    sample_1d: PathExpressionArg,
    buffer_1d: SingletonPathExpressionArg,
    output_dir: str = ".",
    *,
    method: str = "match_tail",
    q_min: Optional[float] = None,
    q_max: Optional[float] = None,
    use_cache: bool = True,
) -> Dict[str, Union[str, List[str]]]:
    """
    Subtract a buffer curve from a sample 1D profile (e.g. match-tail scaling), writing the subtracted curve and diagnostic plots.
    """
    match_tail_ops: Optional[Dict] = None
    if q_min is not None or q_max is not None:
        if q_min is None:
            raise ValueError("subtract: q_min must be set when q_max is set")
        match_tail_ops = {"q_range_abs": (q_min, q_max)}
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    sample_1d = coerce_path_expression(sample_1d)
    buffer_1d = coerce_singleton_path_expression(buffer_1d)
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
    return {
        "subtracted_1d": dest,
        "diff_plot_path": diff_plot_path,
        "diff_log_plot_path": diff_log_plot_path,
        "sub_plot_path": sub_plot_path,
    }

