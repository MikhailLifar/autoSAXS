from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import matplotlib.pyplot as plt
import numpy as np
import yaml

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


def _read_integrator_calibrant_path(integrator_dir: str) -> str:
    """Return calibrant image path from integrator/provenance.yml, or empty string."""
    prov_path = os.path.join(integrator_dir, "provenance.yml")
    if not os.path.isfile(prov_path):
        return ""
    try:
        with open(prov_path, "r") as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            return ""
        raw = data.get("calibrant_image") or data.get("calibrant_path") or ""
        return str(raw) if raw else ""
    except Exception:
        return ""


def integrate_curve_metadata(
    *,
    integrator_dir: str,
    mask_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Canonical autosaxs metadata for a calibrated integrated 1D curve."""
    return {
        "autoSAXS": True,
        "type": "int",
        "calibrant_path": _read_integrator_calibrant_path(integrator_dir),
        "integrator_dir_path": os.path.abspath(integrator_dir),
        "mask_path": os.path.abspath(mask_path) if mask_path else "",
    }


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
    SingletonMaskPathExpressionArg,
    TiffPathExpressionArg,
    SingletonPathExpressionArg,
    coerce_optional_singleton_mask_expression,
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
    mask: Optional[SingletonMaskPathExpressionArg] = None,
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
    - `mask` (str | None, default `None`): Optional mask override (`.txt` / `.npy` / `.msk`). When set, does not rewrite `integrator_dir`. If `auto_mask.npy` is present in `integrator_dir` (written by `calibrate`), the run uses `auto_mask | override`; otherwise the override replaces the stored effective mask as-is.
    - `npt` (int, default `1000`): Number of points in the output q grid.
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.
    - `validation_png` (bool, default `False`): If `True`, write a PNG next to each integrated curve showing the source image (log-intensity) with integrator-masked pixels highlighted in semi-transparent red.

    ### Short parameter list

    - mask: Optional mask override for this integrate run.
    - npt: Number of integrated points, default: 1000
    - validation_png: Show validation image

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
    autosaxs integrate sample.tif calibration/integrator --mask my_mask.npy -o integration
    ```
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    images = coerce_path_expression(images)
    integrator_dir = coerce_singleton_path_expression(integrator_dir)
    mask_expr = coerce_optional_singleton_mask_expression(mask)
    expanded_images = expand_files_from_unwrapped(images.unwrap(), kind="2d_tif")
    int_dir = integrator_dir.unwrap()[0]
    mask_path = mask_expr.unwrap()[0] if mask_expr is not None else None
    if mask_path is not None and not os.path.isfile(mask_path):
        raise FileNotFoundError(f"integrate mask must be an existing file path; got {mask_path!r}")
    input_paths: Dict[str, Union[str, List[str]]] = {
        "images": expanded_images,
        "integrator_dir": int_dir,
    }
    if mask_path is not None:
        input_paths["mask"] = mask_path
    return _integrate_paths(
        input_paths=input_paths,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        npt=npt,
        validation_png=validation_png,
    )


@apply_batch(stem_from_keys="images", single_output_dir=True)
@run_with_cache(
    path_keys_for_hash=["images", "integrator_dir", "mask"],
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
    mask_override = input_paths.get("mask")
    if isinstance(mask_override, list):
        mask_override = mask_override[0] if mask_override else None
    if mask_override:
        integrator.apply_user_mask_override(mask_override)
    os.makedirs(output_dir, exist_ok=True)
    curve_meta = integrate_curve_metadata(
        integrator_dir=str(integrator_dir),
        mask_path=str(mask_override) if mask_override else None,
    )
    integrated: List[str] = []
    validation_pngs: List[str] = []
    for idx, im_path in enumerate(images):
        if event_bus:
            event_bus.publish(EventType.MESSAGE, {"text": f"Integration {idx + 1}/{len(images)}…"})
        data = read_from_tiff(im_path)
        base = os.path.splitext(os.path.basename(im_path))[0]
        dest = os.path.join(output_dir, f"int_{base}.dat")
        _q, _I, _sigma = integrate_2d_to_1d(
            integrator, data, npt=npt, destpath=dest, metadata=curve_meta
        )
        integrated.append(dest)
        if validation_png:
            validation_path = os.path.join(output_dir, f"validation_{base}.png")
            _save_integration_validation_png(data, integrator.mask, im_path, validation_path)
            validation_pngs.append(validation_path)
        frag_base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(im_path))[0])
        md_lines = [
            "### Azimuthal integration\n",
            f"Radial grid: **{npt}** points.\n",
            f"![Integrated curve]({os.path.basename(dest)})\n",
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


# Allow `from autosaxs.skill import integrate` to return a callable even if Python
# resolves `integrate` as this *module* (not the `integrate()` function).
import sys
import types


class _CallableSkillModule(types.ModuleType):
    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[name-defined]
        fn = getattr(self, self.__name__.rsplit(".", 1)[-1])
        return fn(*args, **kwargs)


sys.modules[__name__].__class__ = _CallableSkillModule

