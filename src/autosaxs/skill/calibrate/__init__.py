from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np
import yaml
from pyFAI.calibrant import ALL_CALIBRANTS

from autosaxs.core.event_bus import EventBus, EventType
from autosaxs.core.utils import write_saxs
from autosaxs.core.viewer import PLTViewer

from ..config import merge_skill_params, resolve_optional_config_path
from ..skill_wrap import apply_batch, run_with_cache
from .autocalib import autocalib_ring_analysis
from ..common import (
    ConfigPathExpressionArg,
    SingletonMaskPathExpressionArg,
    SingletonTiffPathExpressionArg,
    coerce_singleton_mask_expression,
    coerce_singleton_tiff_path_expression,
)


ANGSTROM_TO_METER = 1e-10


def _resolve_config_path(config_path: Optional[ConfigPathExpressionArg]) -> Optional[str]:
    return resolve_optional_config_path(config_path)


def _wavelength_angstrom_to_meter(wavelength_a: float) -> float:
    return float(wavelength_a) * ANGSTROM_TO_METER


def _resolve_wavelength_angstrom(merged: Dict) -> float:
    wavelength_a = merged.get("wavelength")
    if wavelength_a is None:
        raise ValueError(
            "calibrate requires wavelength in Ångström; pass wavelength=... or set calibrate.wavelength in config"
        )
    wavelength_a = float(wavelength_a)
    if not np.isfinite(wavelength_a) or wavelength_a <= 0:
        raise ValueError(f"wavelength must be a positive finite value in Ångström; got {wavelength_a!r}")
    return wavelength_a


def _resolve_dist_guess_meters(merged: Dict) -> Optional[float]:
    dist_guess = merged.get("dist_guess")
    if dist_guess is None:
        return None
    dist_guess_m = float(dist_guess)
    if not np.isfinite(dist_guess_m) or dist_guess_m <= 0:
        raise ValueError(f"dist_guess must be a positive finite value in metres; got {dist_guess!r}")
    return dist_guess_m


def calibrate(
    calibrant_image: SingletonTiffPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    mask: SingletonMaskPathExpressionArg,
    mask_mode: Optional[str] = None,
    calibrant: Optional[str] = None,
    wavelength: Optional[float] = None,
    dist_guess: Optional[float] = None,
    use_cache: bool = False,
) -> Dict[str, str]:
    """
    SAXS / small-angle x-ray scattering: calibrate detector geometry using calibrant image. This is a prerequisite for `integrate` (azimuthal integration).

    ### Arguments

    - `calibrant_image` (str): Path to the calibrant image (e.g. TIFF).
    - `output_dir` (str, default `.`): Directory where results are written.
    - `config_path` (str | None, default `None`): Depricated. Path to a YAML config file with a `calibrate` section. When omitted, bundled defaults are used.
    - `mask` (str): Path to a detector pixel mask. Supports .txt (NuPy format), .msk (Fit2d)
    - `mask_mode` (str | None, default `None`): Mask mode selector (`f`/`from_file`, `a`/`auto`, `c`/`combined`). Defaults to `f`/`from_file`.
    - `calibrant` (str | None, default `None`): Calibrant name (must be in `pyFAI.calibrant.ALL_CALIBRANTS`). Defaults to `AgBh`.
    - `wavelength` (float | None, default `None`): X-ray wavelength in **Ångström**. Defaults to 1.445 Å.
    - `dist_guess` (float | None, default `None`): Optional initial sample–detector distance in **metres** passed to pyFAI before geometry refinement. When omitted, distance is estimated from the innermost calibrant ring. Usually works well if not set.
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    Important constraints:

    - `mask` is always required by the skill and the CLI.

    ### Short parameter list

    - mask_mode: Default: load mask from file as is.
    - calibrant: name of the calibrant, default: AgBh.
    - wavelength: X-ray wavelength in Ångström, default: 1.445 Å.
    - dist_guess: Optional: initial sample-detector distance in metres (algorithm works good if this is not set).

    ### Returns

    `dict[str, str]` with these output path roles:

    - `integrator_dir`: Directory containing the calibrated integrator (used by `integrate`).
    - `refined_path`: Path to the refined detector geometry YAML.
    - `calibration_plots_dir`: Directory containing calibration plots.
    - `calibration_curve_plot_path`: Path to the calibrantion q/I curve plot (PNG).
    - `calibration_curve_dat_path`: Path to the calibrantion q/I curve (`.dat`, same format as integrated 1D curves).
    - `calibration_mask_path`: Path to the detector pixel mask visualization (PNG).

    ### Python usage

    ```python
    from autosaxs.skill import calibrate

    out = calibrate(
        calibrant_image="AgBh.tif",
        output_dir="calibration/",
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
    autosaxs calibrate AgBh.tif --conf my_config.conf -o calibration/
    ```
    """
    cfg_path = _resolve_config_path(config_path)
    merged = merge_skill_params(
        "calibrate",
        config_path=cfg_path,
        mask_mode=mask_mode,
        calibrant=calibrant,
        wavelength=wavelength,
        dist_guess=dist_guess,
    )
    calibrant_eff = merged.get("calibrant", "AgBh")
    if calibrant_eff not in ALL_CALIBRANTS:
        raise ValueError(
            f"Unknown calibrant '{calibrant_eff}'. "
            f"Expected one of pyFAI.calibrant.ALL_CALIBRANTS: {sorted(ALL_CALIBRANTS.keys())}"
        )
    mask_mode_eff = merged.get("mask_mode", "f")
    wavelength_a = _resolve_wavelength_angstrom(merged)
    dist_guess_m = _resolve_dist_guess_meters(merged)
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    calibrant_image = coerce_singleton_tiff_path_expression(calibrant_image)
    mask_expr = coerce_singleton_mask_expression(mask)
    imgs = calibrant_image.unwrap()
    mask_path = mask_expr.unwrap()[0]
    input_batch: List[Dict[str, Union[str, List[str]]]] = []
    for im in imgs:
        inp: Dict[str, Union[str, List[str]]] = {"calibrant_image": im}
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
        wavelength_a=wavelength_a,
        dist_guess_m=dist_guess_m,
    )  # type: ignore[return-value]


@apply_batch(stem_from_keys="calibrant_image", per_sample_subdir="never")
@run_with_cache(
    path_keys_for_hash=["calibrant_image", "mask"],
    kwargs_for_hash_keys=["calibrant", "mask_mode", "wavelength_a", "dist_guess_m"],
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
    wavelength_a: float = 1.445,
    dist_guess_m: Optional[float] = None,
) -> Dict[str, Union[str, List[str]]]:
    _ = use_cache, sample_index
    calibrant_image = input_paths.get("calibrant_image")
    if isinstance(calibrant_image, list):
        calibrant_image = calibrant_image[0] if calibrant_image else None
    if not calibrant_image or not os.path.isfile(calibrant_image):
        raise FileNotFoundError("calibrate requires input_paths['calibrant_image']")
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
    for key in ("mask_mode", "calibrant", "wavelength", "dist_guess"):
        cfg.pop(key, None)
    cfg["calibrant_name"] = calibrant
    d_geom = dict(cfg.get("detector_geometry") or {})
    d_geom.pop("dist", None)
    d_geom.pop("wavelength", None)
    d_geom["wavelength"] = _wavelength_angstrom_to_meter(wavelength_a)
    cfg["detector_geometry"] = d_geom
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
    stem = os.path.splitext(os.path.basename(calibrant_image))[0]
    calibration_curve_plot_path = os.path.join(calibration_plots_dir, "calibration_curve.png")
    calibration_curve_dat_path = os.path.join(calibration_plots_dir, "calibration_curve.dat")
    result = autocalib_ring_analysis(
        calibrant_image,
        cfg,
        mask_path=mask_path,
        plots_out_dir=Path(calibration_plots_dir),
        plot_stem=stem,
        calibration_curve_plot_path=Path(calibration_curve_plot_path),
        dist_guess_m=dist_guess_m,
    )
    curve_calibrated = result.get("curve_calibrated")
    if curve_calibrated is not None:
        q_cal, I_cal, sigma = curve_calibrated
        theoretical_peaks = result.get("theoretical_peaks")
        metadata = {
            "type": "calibration_curve",
            "calibrant_image": calibrant_image,
            "calibrant": calibrant,
        }
        if theoretical_peaks is not None:
            metadata["theoretical_peaks_q_nm_inv"] = np.asarray(theoretical_peaks).tolist()
        write_saxs(calibration_curve_dat_path, q_cal, I_cal, sigma, metadata)
    integrator_dir = os.path.join(output_dir, "integrator")
    result["integrator"].to_disk(integrator_dir)
    refined_path = os.path.join(output_dir, "refined.yml")
    with open(refined_path, "w") as f:
        yaml.dump(result["refined"], f, default_flow_style=False)
    calibration_mask_path = os.path.join(calibration_plots_dir, "calibration_mask.png")
    PLTViewer.view_mask(
        result["calib_data"],
        result["integrator"].mask,
        tiff_path=calibrant_image,
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
    if curve_calibrated is not None:
        summary_refs.append(
            {
                "role": "calibration_curve",
                "path": os.path.relpath(calibration_curve_dat_path, output_dir).replace(os.sep, "/"),
                "format": "saxs_dat",
            }
        )
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
        "calibration_curve_dat_path": calibration_curve_dat_path,
        "calibration_mask_path": calibration_mask_path,
    }
