from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import matplotlib.pyplot as plt
import numpy as np

from autosaxs.core.integrator import IntegratorExtended
from autosaxs.core.utils import write_saxs

from .deps import (
    EventBus,
    EventType,
    apply_batch,
    read_from_tiff,
    run_with_cache,
    _strip_sub_int_prefix,
)


def integrate_2d_to_1d(integrator, saxs_2d, npt=1000, destpath=None, metadata=None):
    q, I, sigma = integrator.integrate1d(saxs_2d, npt=npt)
    if destpath is not None:
        if metadata is None:
            metadata: Dict[str, Any] = {}
        write_saxs(destpath, q, I, sigma, metadata)
    return q, I, sigma


def _save_integration_validation_png(
    img_data: np.ndarray,
    mask: Optional[np.ndarray],
    image_path: str,
    out_path: str,
) -> None:
    img_float = np.asarray(img_data, dtype=float)
    fig, ax = plt.subplots()
    ax.imshow(np.log1p(img_float), cmap="viridis", origin="lower")
    if mask is not None:
        mask_bool = np.asarray(mask, dtype=bool)
        if mask_bool.shape == img_float.shape and np.any(mask_bool):
            overlay = np.zeros((*mask_bool.shape, 4), dtype=float)
            overlay[mask_bool] = (1.0, 0.0, 0.0, 0.5)
            ax.imshow(overlay, origin="lower")
    ax.set_title(f"Integration validation: {os.path.basename(image_path)}")
    ax.set_xlabel("Pixel X")
    ax.set_ylabel("Pixel Y")
    fig.savefig(out_path)
    plt.close(fig)

from autosaxs.core.report_fragments import write_skill_report_fragments

from .common import (
    ConfigPathExpressionArg,
    TiffPathExpressionArg,
    SingletonPathExpressionArg,
    coerce_path_expression,
    coerce_singleton_path_expression,
    expand_files_from_unwrapped,
)


def integrate(
    images: TiffPathExpressionArg,
    integrator_dir: SingletonPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    npt: int = 1000,
    use_cache: bool = False,
    validation_png: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    SAXS / small-angle x-ray scattering: integrate 2D SAXS images to 1D curves (q, I, sigma) using a calibrated integrator produced by `calibrate` (azimuthal integration; q-space).

    ### Arguments

    - `images` (str): Image path expression. Can be:
      - a single `.tif` file path
      - a directory (expands to `*.tif`, non-recursive)
      - a glob expression
      - a comma-separated list of file paths (e.g. from multi-file drag & drop)
    - `integrator_dir` (str): Path to the calibrated integrator directory (from `calibrate`).
    - `output_dir` (str, default `.`): Directory where integrated curves are written.
    - `npt` (int, default `1000`): Number of points in the output q grid.
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.
    - `validation_png` (bool, default `False`): If `True`, write a PNG next to each integrated curve showing the source image (log-intensity) with integrator-masked pixels highlighted in semi-transparent red.

    ### Returns

    `dict[str, str | list[str]]` with:

    - `integrated_1d`: List of paths to integrated 1D `.dat` curves (one per input image).
    - `validation_png` (only when `validation_png=True`): List of paths to validation PNG(s), one per input image.

    ### Python usage

    ```python
    from autosaxs.skill import integrate

    out = integrate(
        images="/data/sample_*.tif",
        integrator_dir="calibration/integrator",
        output_dir="integration",
        npt=1000,
        use_cache=False,
    )

    print(out["integrated_1d"])
    ```

    ### CLI usage

    ```bash
    autosaxs integrate "/data/sample_01.tif, /data/sample_02.tif" calibration/integrator \
      --output-dir integration --npt 1000
    ```
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    images = coerce_path_expression(images)
    integrator_dir = coerce_singleton_path_expression(integrator_dir)
    expanded_images = expand_files_from_unwrapped(images.unwrap(), kind="2d_tif")
    int_dir = integrator_dir.unwrap()[0]
    return _integrate_paths(
        input_paths={"images": expanded_images, "integrator_dir": int_dir},
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        npt=npt,
        validation_png=validation_png,
    )


@apply_batch(stem_from_keys="images", single_output_dir=True)
@run_with_cache(
    path_keys_for_hash=["images", "integrator_dir"],
    kwargs_for_hash_keys=["npt", "validation_png"],
    include_config_in_hash=False,
)
def _integrate_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
    npt: int = 1000,
    validation_png: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
    images = input_paths.get("images")
    if isinstance(images, str):
        images = [images]
    if not images:
        raise ValueError("integrate requires input_paths['images']")
    integrator_dir = input_paths.get("integrator_dir")
    if isinstance(integrator_dir, list):
        integrator_dir = integrator_dir[0] if integrator_dir else None
    if not integrator_dir or not os.path.isdir(integrator_dir):
        raise FileNotFoundError("integrate requires input_paths['integrator_dir']")
    integrator = IntegratorExtended.from_disk(integrator_dir)
    os.makedirs(output_dir, exist_ok=True)
    integrated: List[str] = []
    validation_pngs: List[str] = []
    for idx, im_path in enumerate(images):
        if event_bus:
            event_bus.publish(EventType.MESSAGE, {"text": f"Integration {idx + 1}/{len(images)}…"})
        data = read_from_tiff(im_path)
        base = os.path.splitext(os.path.basename(im_path))[0]
        dest = os.path.join(output_dir, f"int_{base}.dat")
        _q, _I, _sigma = integrate_2d_to_1d(integrator, data, npt=npt, destpath=dest)
        integrated.append(dest)
        if validation_png:
            validation_path = os.path.join(output_dir, f"validation_{base}.png")
            _save_integration_validation_png(data, integrator.mask, im_path, validation_path)
            validation_pngs.append(validation_path)
        frag_base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(im_path))[0])
        md_lines = [
            "### Azimuthal integration\n",
            f"Radial grid: **{npt}** points.\n",
        ]
        summary_refs = [
            {"role": "integrated_curve", "path": os.path.basename(dest), "format": "saxs_dat"},
        ]
        if validation_png:
            md_lines.append(
                f"![Integration validation]({os.path.basename(validation_pngs[-1])})\n",
            )
            summary_refs.append(
                {"role": "validation_png", "path": os.path.basename(validation_pngs[-1]), "format": "png"},
            )
        write_skill_report_fragments(
            output_dir,
            frag_base,
            "integrate",
            "".join(md_lines),
            summary_references=summary_refs,
        )
    out: Dict[str, Union[str, List[str]]] = {"integrated_1d": integrated}
    if validation_png:
        out["validation_png"] = validation_pngs
    return out

