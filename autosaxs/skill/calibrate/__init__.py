from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np
import yaml
from pyFAI.calibrant import ALL_CALIBRANTS

from autosaxs.core.event_bus import EventBus, EventType
from autosaxs.core.viewer import PLTViewer

from ..config import merge_skill_params, resolve_optional_config_path
from ..skill_wrap import apply_batch, run_with_cache
from .autocalib import autocalib_ring_analysis
from ..common import (
    ConfigPathExpressionArg,
    SingletonMaskPathExpressionArg,
    SingletonTiffPathExpressionArg,
    coerce_optional_singleton_path_expression,
    coerce_singleton_tiff_path_expression,
)


def _resolve_config_path(config_path: Optional[ConfigPathExpressionArg]) -> Optional[str]:
    return resolve_optional_config_path(config_path)


def calibrate(
    calib_image: SingletonTiffPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    mask: Optional[SingletonMaskPathExpressionArg] = None,
    mask_mode: Optional[str] = None,
    calibrant: Optional[str] = None,
    use_cache: bool = False,
) -> Dict[str, str]:
    """
    SAXS / small-angle x-ray scattering: calibrate detector geometry using a calibration image and a config (ring-analysis + geometry refinement). This is a prerequisite for `integrate` (azimuthal integration).

    ### Arguments

    - `calib_image` (str): Path to the calibration image (e.g. TIFF) used for ring analysis.
    - `output_dir` (str, default `.`): Directory where results are written.
    - `config_path` (str | None, default `None`): Optional path to a YAML config file with a `calibrate` section. When omitted, bundled defaults from the installed `autosaxs` package are used.
    - `mask` (str | None, default `None`): Optional path to a mask used during ring analysis. Supports .txt (NuPy format), .msk (Fit2d)
    - `mask_mode` (str | None, default `None`): Mask mode selector (`f`/`from_file`, `a`/`auto`, `c`/`combined`). Defaults come from config when omitted.
    - `calibrant` (str | None, default `None`): Calibrant name (must be in `pyFAI.calibrant.ALL_CALIBRANTS`). Defaults come from config when omitted.
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    Important constraints:

    - If `mask_mode` is `f/from_file` or `c/combined`, `mask` **must** be provided (the skill raises `ValueError` otherwise).

    ### Returns

    `dict[str, str]` with these output path roles:

    - `integrator_dir`: Directory containing the calibrated integrator (used by `integrate`).
    - `refined_path`: Path to the refined calibration YAML.
    - `calibration_plots_dir`: Directory containing calibration plots.
    - `calibration_curve_plot_path`: Path to the calibration q/I curve plot (PNG).
    - `calibration_mask_path`: Path to the calibration mask visualization (PNG).

    ### Python usage

    ```python
    from autosaxs.skill import calibrate

    out = calibrate(
        calib_image="AgBh.tif",
        output_dir="calibration",
        mask="mask.msk",
        mask_mode="f",
        use_cache=False,
    )

    print(out["integrator_dir"])
    print(out["refined_path"])
    ```

    ### CLI usage

    ```bash
    autosaxs calibrate AgBh.tif --output-dir calibration --mask mask.msk
    autosaxs calibrate AgBh.tif --conf my_config.conf --output-dir calibration
    ```
    """
    cfg_path = _resolve_config_path(config_path)
    merged = merge_skill_params(
        "calibrate",
        config_path=cfg_path,
        mask_mode=mask_mode,
        calibrant=calibrant,
    )
    calibrant_eff = merged.get("calibrant", "AgBh")
    if calibrant_eff not in ALL_CALIBRANTS:
        raise ValueError(
            f"Unknown calibrant '{calibrant_eff}'. "
            f"Expected one of pyFAI.calibrant.ALL_CALIBRANTS: {sorted(ALL_CALIBRANTS.keys())}"
        )
    mask_mode_eff = merged.get("mask_mode", "f")
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    calib_image = coerce_singleton_tiff_path_expression(calib_image)
    mask_expr = coerce_optional_singleton_path_expression(mask)
    imgs = calib_image.unwrap()
    mask_path = mask_expr.unwrap()[0] if mask_expr is not None else None
    input_batch: List[Dict[str, Union[str, List[str]]]] = []
    for im in imgs:
        inp: Dict[str, Union[str, List[str]]] = {"calib_image": im}
        if mask_path is not None:
            inp["mask"] = mask_path
        input_batch.append(inp)
    input_paths: Union[Dict[str, Union[str, List[str]]], List[Dict[str, Union[str, List[str]]]]]
    input_paths = input_batch[0] if len(input_batch) == 1 else input_batch
    return _calibrate_paths(
        input_paths=input_paths,
        output_dir=output_dir,
        config=merged,
        event_bus=bus,
        use_cache=use_cache,
        mask_mode=mask_mode_eff,
        calibrant=calibrant_eff,
    )  # type: ignore[return-value]


@apply_batch(stem_from_keys="calib_image", per_sample_subdir="never")
@run_with_cache(
    path_keys_for_hash=["calib_image", "mask"],
    kwargs_for_hash_keys=["calibrant", "mask_mode"],
    include_config_in_hash=True,
)
def _calibrate_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
    mask_mode: str = "f",
    calibrant: str = "AgBh",
) -> Dict[str, Union[str, List[str]]]:
    _ = use_cache, sample_index
    calib_image = input_paths.get("calib_image")
    if isinstance(calib_image, list):
        calib_image = calib_image[0] if calib_image else None
    if not calib_image or not os.path.isfile(calib_image):
        raise FileNotFoundError("calibrate requires input_paths['calib_image']")
    if not config:
        raise ValueError("calibrate requires config (merged calibrate section)")
    mask_mode_map = {
        "a": "auto",
        "f": "from_file",
        "c": "combined",
        "auto": "auto",
        "from_file": "from_file",
        "combined": "combined",
    }
    if mask_mode not in mask_mode_map:
        raise ValueError("mask_mode must be one of: a, f, c, auto, from_file, combined")
    if calibrant not in ALL_CALIBRANTS:
        raise ValueError(
            f"Unknown calibrant '{calibrant}'. "
            f"Expected one of pyFAI.calibrant.ALL_CALIBRANTS: {sorted(ALL_CALIBRANTS.keys())}"
        )
    cfg = dict(config)
    for key in ("mask_mode", "calibrant"):
        cfg.pop(key, None)
    cfg["calibrant_name"] = calibrant
    cfg_mask_config = dict(cfg.get("mask_config") or {})
    cfg_mask_config["mode"] = mask_mode_map[mask_mode]
    cfg["mask_config"] = cfg_mask_config
    mask_path = input_paths.get("mask")
    if isinstance(mask_path, list):
        mask_path = mask_path[0] if mask_path else None
    if cfg_mask_config["mode"] in ("from_file", "combined") and not mask_path:
        raise ValueError("mask path is required when mask_mode is 'f'/'from_file' or 'c'/'combined'")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "Calibration: ring analysis and geometry refinement…"})
    os.makedirs(output_dir, exist_ok=True)
    calibration_plots_dir = os.path.join(output_dir, "calibration_plots")
    os.makedirs(calibration_plots_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(calib_image))[0]
    calibration_curve_plot_path = os.path.join(calibration_plots_dir, "calibration_curve.png")
    result = autocalib_ring_analysis(
        calib_image,
        cfg,
        mask_path=mask_path,
        plots_out_dir=Path(calibration_plots_dir),
        plot_stem=stem,
        calibration_curve_plot_path=Path(calibration_curve_plot_path),
    )
    integrator_dir = os.path.join(output_dir, "integrator")
    result["integrator"].to_disk(integrator_dir)
    refined_path = os.path.join(output_dir, "refined.yml")
    with open(refined_path, "w") as f:
        yaml.dump(result["refined"], f, default_flow_style=False)
    calibration_mask_path = os.path.join(calibration_plots_dir, "calibration_mask.png")
    PLTViewer.view_mask(
        result["calib_data"],
        result["integrator"].mask,
        tiff_path=calib_image,
        show_duration=None,
        plotFilePath=calibration_mask_path,
    )
    from autosaxs.core.report_fragments import write_skill_report_fragments

    calib_base = stem
    md_lines = [
        "### Detector calibration\n",
        f"![Calibration curve]({os.path.relpath(calibration_curve_plot_path, output_dir).replace(os.sep, '/')})\n",
        f"![Calibration mask]({os.path.relpath(calibration_mask_path, output_dir).replace(os.sep, '/')})\n",
    ]
    summary_refs = [
        {"role": "refined_geometry", "path": os.path.basename(refined_path), "format": "text"},
    ]
    write_skill_report_fragments(
        output_dir,
        calib_base,
        "calibrate",
        "".join(md_lines),
        summary_references=summary_refs,
    )
    return {
        "integrator_dir": integrator_dir,
        "refined_path": refined_path,
        "calibration_plots_dir": calibration_plots_dir,
        "calibration_curve_plot_path": calibration_curve_plot_path,
        "calibration_mask_path": calibration_mask_path,
    }
