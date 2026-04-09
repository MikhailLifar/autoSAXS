from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

import matplotlib.pyplot as plt
import numpy as np
from scipy.optimize import minimize

from .deps import (
    EventBus,
    EventType,
    IntegratorExtended,
    apply_batch,
    read_from_tiff,
    run_with_cache,
    write_saxs,
)
from .common import (
    PathExpressionArg,
    SingletonPathExpressionArg,
    coerce_optional_singleton_path_expression,
    coerce_path_expression,
    expand_files_from_unwrapped,
)


def integrate_proxy(
    image: PathExpressionArg,
    output_dir: str = ".",
    *,
    mask: Optional[SingletonPathExpressionArg] = None,
    cy: Optional[float] = None,
    cx: Optional[float] = None,
    npt: int = 1000,
    use_cache: bool = True,
) -> Dict[str, Union[str, List[str]]]:
    """
    Integrate 2D TIFF image(s) to a 1D curve **without detector calibration**, using radial averaging in pixel-radius space.

    This is intended for quick-look / debugging when you don’t have a calibrated integrator yet. The output `.dat` stores metadata indicating the x-axis is `r_px` (pixels), not physical q.

    ### Arguments

    - `image` (str): 2D image path expression. Can be:
      - a single `.tif` file path
      - a directory (expands to `*.tif`, non-recursive)
      - a glob expression (including `**`)
    - `output_dir` (str, default `.`): Directory where integrated curves are written.
    - `mask` (str | None, default `None`): Optional mask path; same shape as the image. (`pyFAI` convention: masked pixels are excluded.)
    - `cy` (float | None, default `None`): Optional beam center y in pixels. Must be set together with `cx`.
    - `cx` (float | None, default `None`): Optional beam center x in pixels. Must be set together with `cy`.
    - `npt` (int, default `1000`): Number of points in the output x grid.
    - `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

    Notes:

    - If `cy/cx` are not provided, the skill **estimates** the center by radial-symmetry optimization and also writes a center diagnostic plot `*_center.png` into `output_dir`.
    - If center estimation fails for an input, that item is skipped and the skill may return an empty list for `integrated_1d`.

    ### Returns

    `dict[str, str | list[str]]` with:

    - `integrated_1d`: Path (or list of paths, if `image` is a directory) to integrated 1D `.dat` curves.

    ### Python usage

    ```python
    from autosaxs.skill import integrate_proxy

    out = integrate_proxy(
        image="raw/sample_01.tif",
        output_dir="integration_proxy",
        mask="mask.msk",
        npt=1000,
        use_cache=True,
    )

    print(out["integrated_1d"])
    ```

    ### CLI usage

    ```bash
    autosaxs integrate_proxy raw/sample_01.tif --output-dir integration_proxy --mask mask.msk --npt 1000
    ```
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    image = coerce_path_expression(image)
    mask_expr = coerce_optional_singleton_path_expression(mask)
    expanded_images = expand_files_from_unwrapped(image.unwrap(), kind="2d_tif")
    mask_path = mask_expr.unwrap()[0] if mask_expr is not None else None
    if mask_path is not None and not os.path.isfile(mask_path):
        raise FileNotFoundError("integrate_proxy mask must be an existing file path")
    if (cy is None) != (cx is None):
        raise ValueError("integrate_proxy requires cy and cx to be both None or both float values")

    for p in expanded_images:
        if Path(p).suffix.lower() != ".tif":
            raise ValueError("integrate_proxy input files must have .tif extension")

    input_batch = [
        {"image": im_path, **({"mask": mask_path} if mask_path is not None else {})} for im_path in expanded_images
    ]
    if len(input_batch) == 1:
        return _integrate_proxy_paths(
            input_paths=input_batch[0],
            output_dir=output_dir,
            event_bus=bus,
            use_cache=use_cache,
            cy=cy,
            cx=cx,
            npt=npt,
        )
    return _integrate_proxy_paths(
        input_paths=input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        cy=cy,
        cx=cx,
        npt=npt,
    )


def _radial_integrate_pixels(
    data: np.ndarray,
    center_y: float,
    center_x: float,
    npt: int,
    valid_mask: Optional[np.ndarray] = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    yy, xx = np.indices(data.shape)
    rr = np.sqrt((yy - float(center_y)) ** 2 + (xx - float(center_x)) ** 2)
    r_flat = rr.ravel()
    i_flat = np.asarray(data, dtype=float).ravel()
    valid = np.isfinite(i_flat) & np.isfinite(r_flat)
    if valid_mask is not None:
        valid = valid & valid_mask.ravel()
    if not np.any(valid):
        raise ValueError("integrate_proxy found no finite pixels in image")
    r_flat = r_flat[valid]
    i_flat = i_flat[valid]

    r_bin = np.floor(r_flat).astype(int)
    max_bin = int(np.max(r_bin))
    counts = np.bincount(r_bin, minlength=max_bin + 1).astype(float)
    sum_i = np.bincount(r_bin, weights=i_flat, minlength=max_bin + 1)
    sum_i2 = np.bincount(r_bin, weights=i_flat * i_flat, minlength=max_bin + 1)
    good = counts > 0
    if not np.any(good):
        raise ValueError("integrate_proxy produced empty radial bins")

    r_centers = np.arange(max_bin + 1, dtype=float)
    mean_i = np.zeros_like(r_centers, dtype=float)
    sigma_i = np.zeros_like(r_centers, dtype=float)
    mean_i[good] = sum_i[good] / counts[good]
    var_i = np.zeros_like(r_centers, dtype=float)
    var_i[good] = np.maximum(sum_i2[good] / counts[good] - mean_i[good] ** 2, 0.0)
    sigma_i[good] = np.sqrt(var_i[good]) / np.sqrt(np.maximum(counts[good], 1.0))

    r_used = r_centers[good]
    i_used = mean_i[good]
    s_used = sigma_i[good]
    if r_used.size == 1:
        q = np.full(int(npt), r_used[0], dtype=float)
        I = np.full(int(npt), i_used[0], dtype=float)
        sigma = np.full(int(npt), s_used[0], dtype=float)
        return q, I, sigma

    q = np.linspace(float(r_used.min()), float(r_used.max()), int(npt))
    I = np.interp(q, r_used, i_used)
    sigma = np.interp(q, r_used, s_used)
    return q, I, sigma


def _weighted_top_pixel_center_guess(
    data: np.ndarray,
    top_fraction: float = 0.005,
    valid_mask: Optional[np.ndarray] = None,
) -> tuple[float, float]:
    intensity = np.asarray(data, dtype=float)
    valid = np.isfinite(intensity)
    if valid_mask is not None:
        valid = valid & valid_mask
    if not np.any(valid):
        raise ValueError("integrate_proxy center guess failed: no finite pixels")

    vals = intensity[valid]
    k = max(int(np.ceil(vals.size * float(top_fraction))), 1)
    if k >= vals.size:
        threshold = float(np.nanmin(vals))
    else:
        threshold = float(np.partition(vals, vals.size - k)[vals.size - k])

    yy, xx = np.indices(intensity.shape)
    sel = valid & (intensity >= threshold)
    if not np.any(sel):
        raise ValueError("integrate_proxy center guess failed: no selected top pixels")

    weights = np.log1p(np.clip(intensity[sel], a_min=0.0, a_max=None))
    if not np.any(np.isfinite(weights)) or float(np.nansum(weights)) <= 0:
        weights = np.ones_like(weights, dtype=float)

    y0 = float(np.average(yy[sel].astype(float), weights=weights))
    x0 = float(np.average(xx[sel].astype(float), weights=weights))
    return y0, x0


def _radial_symmetry_objective(
    center_yx: np.ndarray,
    intensity_log: np.ndarray,
    radius_px: float = 100.0,
    min_count_per_bin: int = 8,
    valid_mask: Optional[np.ndarray] = None,
) -> float:
    cy = float(center_yx[0])
    cx = float(center_yx[1])
    h, w = intensity_log.shape
    yy, xx = np.indices((h, w))
    rr = np.sqrt((yy - cy) ** 2 + (xx - cx) ** 2)
    valid = np.isfinite(intensity_log) & (rr <= float(radius_px))
    if valid_mask is not None:
        valid = valid & valid_mask
    if np.count_nonzero(valid) < 50:
        return float("inf")

    r = rr[valid]
    I = intensity_log[valid]
    r_bin = np.floor(r).astype(int)
    max_bin = int(np.max(r_bin))
    counts = np.bincount(r_bin, minlength=max_bin + 1).astype(float)
    sum_i = np.bincount(r_bin, weights=I, minlength=max_bin + 1)
    sum_i2 = np.bincount(r_bin, weights=I * I, minlength=max_bin + 1)
    good = counts >= float(min_count_per_bin)
    if not np.any(good):
        return float("inf")

    mean = np.zeros_like(counts, dtype=float)
    var = np.zeros_like(counts, dtype=float)
    mean[good] = sum_i[good] / counts[good]
    var[good] = np.maximum(sum_i2[good] / counts[good] - mean[good] ** 2, 0.0)
    weighted_var = float(np.sum(var[good] * counts[good]) / np.sum(counts[good]))
    return weighted_var


def _estimate_center_radial_symmetry(
    data: np.ndarray,
    *,
    search_radius_px: float = 100.0,
    valid_mask: Optional[np.ndarray] = None,
) -> tuple[float, float, float, float]:
    y0, x0 = _weighted_top_pixel_center_guess(data, top_fraction=0.005, valid_mask=valid_mask)
    intensity_log = np.log1p(np.clip(np.asarray(data, dtype=float), a_min=0.0, a_max=None))
    intensity_log[~np.isfinite(data)] = np.nan
    h, w = intensity_log.shape

    y_lo = max(50.0, y0 - 100.0)
    y_hi = min(float(h - 50), y0 + 100.0)
    x_lo = max(50.0, x0 - 100.0)
    x_hi = min(float(w - 50), x0 + 100.0)

    if y_lo >= y_hi:
        y_lo, y_hi = 0.0, float(h - 1)
    if x_lo >= x_hi:
        x_lo, x_hi = 0.0, float(w - 1)

    y0 = float(np.clip(y0, y_lo, y_hi))
    x0 = float(np.clip(x0, x_lo, x_hi))
    bounds = [(y_lo, y_hi), (x_lo, x_hi)]
    result = minimize(
        lambda c: _radial_symmetry_objective(c, intensity_log, radius_px=search_radius_px, valid_mask=valid_mask),
        x0=np.asarray([y0, x0], dtype=float),
        method="Powell",
        bounds=bounds,
        options={"maxiter": 200, "xtol": 1e-2, "ftol": 1e-6},
    )
    if not result.success or not np.all(np.isfinite(result.x)):
        raise RuntimeError("integrate_proxy center optimization failed")

    cy = float(np.clip(result.x[0], 0.0, float(h - 1)))
    cx = float(np.clip(result.x[1], 0.0, float(w - 1)))
    return y0, x0, cy, cx


def _save_center_plot(image: np.ndarray, initial_y: float, initial_x: float, center_y: float, center_x: float, out_path: str) -> None:
    fig, ax = plt.subplots()
    ax.imshow(np.log1p(image), cmap="viridis", origin="lower")
    ax.scatter([initial_x], [initial_y], c=["yellow"], marker="x", s=80, label="initial")
    ax.scatter([center_x], [center_y], c=["red"], marker="+", s=120, label="final")
    ax.set_title("Center estimation")
    ax.set_xlabel("Pixel X")
    ax.set_ylabel("Pixel Y")
    ax.legend(loc="best")
    fig.savefig(out_path)
    plt.close(fig)


@apply_batch(stem_from_keys="image", single_output_dir=True)
@run_with_cache(
    path_keys_for_hash=["image", "mask"],
    kwargs_for_hash_keys=["cy", "cx", "npt"],
    include_config_in_hash=False,
)
def _integrate_proxy_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = True,
    sample_index: int = 0,
    cy: Optional[float] = None,
    cx: Optional[float] = None,
    npt: int = 1000,
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
    image = input_paths.get("image")
    if isinstance(image, list):
        image = image[0] if image else None
    if not image or not os.path.isfile(image):
        raise FileNotFoundError("integrate_proxy requires input_paths['image']")
    if Path(image).suffix.lower() != ".tif":
        raise ValueError("integrate_proxy requires input_paths['image'] with .tif extension")
    if (cy is None) != (cx is None):
        raise ValueError("integrate_proxy requires cy and cx to be both None or both float values")

    os.makedirs(output_dir, exist_ok=True)
    img_data = read_from_tiff(image).astype(float)
    mask_path = input_paths.get("mask")
    if isinstance(mask_path, list):
        mask_path = mask_path[0] if mask_path else None
    valid_mask: Optional[np.ndarray] = None
    if isinstance(mask_path, str):
        if not os.path.isfile(mask_path):
            raise FileNotFoundError("integrate_proxy requires input_paths['mask'] to be an existing file")
        mask_data = IntegratorExtended.read_mask(mask_path)
        if mask_data.shape != img_data.shape:
            raise ValueError("integrate_proxy mask shape must match image shape")
        valid_mask = ~mask_data

    base = os.path.splitext(os.path.basename(image))[0]
    dest = os.path.join(output_dir, f"int_{base}.dat")
    center_plot_path = os.path.join(output_dir, f"{base}_center.png")

    if cy is None and cx is None:
        if event_bus:
            event_bus.publish(EventType.MESSAGE, {"text": "Integrate proxy: estimating center by radial symmetry…"})
        try:
            initial_y, initial_x, center_y, center_x = _estimate_center_radial_symmetry(
                img_data,
                search_radius_px=100.0,
                valid_mask=valid_mask,
            )
        except Exception:
            print(
                f"Warning: integrate_proxy could not estimate center for {image}; skipping integration.",
                file=sys.stderr,
            )
            return {"integrated_1d": []}
    else:
        initial_y = float(cy)  # type: ignore[arg-type]
        initial_x = float(cx)  # type: ignore[arg-type]
        center_y = float(cy)  # type: ignore[arg-type]
        center_x = float(cx)  # type: ignore[arg-type]

    _save_center_plot(img_data, initial_y, initial_x, center_y, center_x, center_plot_path)

    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "Integrate proxy: radial averaging in pixel space…"})

    q, I, sigma = _radial_integrate_pixels(
        img_data,
        center_y=center_y,
        center_x=center_x,
        npt=npt,
        valid_mask=valid_mask,
    )
    write_saxs(
        dest,
        q,
        I,
        sigma,
        metadata={
            "type": "integrated_proxy_1d",
            "parent": image,
            "x_axis": "r_px",
            "x_axis_unit": "pixel",
            "center_y_px": center_y,
            "center_x_px": center_x,
        },
    )
    return {"integrated_1d": dest}

