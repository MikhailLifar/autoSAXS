from __future__ import annotations

from typing import Tuple

import numpy as np
import scipy.ndimage as ndi
from scipy.stats import mannwhitneyu
from pyFAI.calibrant import CALIBRANT_FACTORY
from pyFAI.geometryRefinement import GeometryRefinement

from autosaxs.core.integrator import IntegratorExtended
from autosaxs.core.utils import get_detector


def calc_beam_abnormal_mask(
    data,
    center_y_px,
    center_x_px,
    r_beam_px,
    calc_abnormal_mask: bool = True,
    window_size: int = 7,
    iqr_tol: float = 1.5,
):
    """
    Build a mask that combines the beam-stop region with statistical outlier
    detection in log-intensity space using a local IQR test.
    """
    if window_size % 2 == 0:
        raise ValueError("window_size must be odd for symmetric neighborhood")

    data = np.asarray(data, dtype=float)

    beam_mask = np.fromfunction(
        lambda i, j: np.linalg.norm(
            [i - center_y_px, j - center_x_px], axis=0
        )
        <= r_beam_px,
        data.shape,
    )

    if calc_abnormal_mask:
        data = data - min(np.min(data), 0.0)
        log_data = np.log1p(data)

        q1 = ndi.percentile_filter(
            log_data, percentile=25, size=window_size, mode="reflect"
        )
        q3 = ndi.percentile_filter(
            log_data, percentile=75, size=window_size, mode="reflect"
        )
        iqr = q3 - q1

        eps = 1e-12
        lower = q1 - iqr_tol * iqr
        upper = q3 + iqr_tol * iqr
        abnormal_mask = (log_data < lower - eps) | (log_data > upper + eps)
        return beam_mask | abnormal_mask

    return beam_mask


def get_r_beam_px(
    image: np.ndarray,
    center_y_px,
    center_x_px,
    *,
    r_min: float = 5.0,
    r_max: float = 50.0,
    ring_width: float = 2.0,
    min_ring_pixels: int = 10,
    alpha: float = 0.05,
    max_sample: int = 400,
    refine_quantile_alpha: float = 0.05,
):
    """
    Estimate the radius of the dark beam-stop circle at the beam center.
    """
    y_coords, x_coords = np.ogrid[: image.shape[0], : image.shape[1]]
    r = np.sqrt((y_coords - center_y_px) ** 2 + (x_coords - center_x_px) ** 2)
    flat_r = r.ravel()
    flat_img = image.ravel()

    def test_r(r_cand: int) -> bool:
        inside_idx = np.flatnonzero(flat_r < r_cand)
        ring_idx = np.flatnonzero(
            (flat_r >= r_cand) & (flat_r < r_cand + ring_width)
        )
        n_in, n_out = len(inside_idx), len(ring_idx)
        if n_out < min_ring_pixels or n_in < min_ring_pixels:
            return False
        if max_sample > 0 and n_in > max_sample:
            inside_idx = np.random.choice(inside_idx, max_sample, replace=False)
        if max_sample > 0 and n_out > max_sample:
            ring_idx = np.random.choice(ring_idx, max_sample, replace=False)
        inside = flat_img[inside_idx]
        outside = flat_img[ring_idx]
        _, p = mannwhitneyu(
            inside, outside, alternative="less", method="asymptotic"
        )
        return bool(p < alpha and np.median(inside) < np.median(outside))

    r_int_max = int(np.floor(r_max))
    r_int_min = int(np.ceil(r_min))
    step = 2
    found = None
    for r_cand in range(r_int_min, r_int_max + 1, step):
        if test_r(r_cand):
            found = r_cand
            break
    if found is None:
        return None
    for r_cand in range(max(r_int_min, found - step + 1), found):
        if test_r(r_cand):
            found = r_cand
            break
    r0 = found

    n_rings = 13
    q_vals = np.full(n_rings, np.nan)
    for i in range(n_rings):
        lo, hi = r0 - 2 + i, r0 + i
        mask = (flat_r >= lo) & (flat_r < hi)
        if np.sum(mask) >= min_ring_pixels:
            q_vals[i] = np.quantile(flat_img[mask], refine_quantile_alpha)
    increases = np.diff(q_vals)
    if not np.any(np.isfinite(increases)):
        r_beam_px = float(r0) + 0.5 * ring_width
        return float(np.clip(r_beam_px, r_min, r_max))
    i_max = int(np.nanargmax(increases))
    r_beam_px = float(r0 + i_max)
    return float(np.clip(r_beam_px, r_min, r_max))


def refine(
    calib_data,
    rings,
    wavelength,
    dist,
    pixel_size,
    center_y_px,
    center_x_px,
    calibrant_name,
    r_beam_px,
    rot1=0,
    rot2=0,
    rot3=0,
    detector_name="Pilatus1M",
    fix: Tuple[str] = ("wavelength", "rot3"),
    npt: int = 1000,
    mask_path=None,
    mask_config=None,
):
    """
    Refine the detector geometry to calibrant rings, returning integrator,
    refined parameters, calibrated curve, and theoretical peak positions.
    """
    assert detector_name is not None and all(s is not None for s in pixel_size), (
        "detector and pixel_size must be set."
    )
    assert wavelength is not None, "wavelength must be set."

    detector = get_detector(detector_name, pixel_size)

    calibrant = CALIBRANT_FACTORY(calibrant_name)
    calibrant.set_wavelength(wavelength)
    poni1 = pixel_size[0] * center_y_px
    poni2 = pixel_size[1] * center_x_px

    print("DEBUG: Starting mask calculation (before refinement)...")
    mask = None
    if mask_config is not None:
        mode = mask_config["mode"]
        print(f"DEBUG: Mask config mode: {mode}")

        automask = None
        if mode in ["auto", "combined"]:
            print("DEBUG: Calculating automatic mask...")
            automask_ops = {k: v for k, v in mask_config.items() if k != "mode"}
            automask = calc_beam_abnormal_mask(
                calib_data, center_y_px, center_x_px, r_beam_px, **automask_ops
            )
            print("DEBUG: Automatic mask calculated")

        file_mask = None
        if mode in ["from_file", "combined"]:
            assert mask_path is not None
            print(f"DEBUG: Reading mask from file: {mask_path}")
            file_mask = IntegratorExtended.read_mask(mask_path)

        center_only_mask = None
        if mode == "from_file":
            print("DEBUG: Adding center (beam-stop) mask for from_file mode (no IQR filtering)")
            center_only_mask = calc_beam_abnormal_mask(
                calib_data,
                center_y_px,
                center_x_px,
                r_beam_px,
                calc_abnormal_mask=False,
            )

        if mode == "auto":
            mask = automask
        elif mode == "from_file":
            mask = file_mask | center_only_mask
        elif mode == "combined":
            assert file_mask is not None and automask is not None, (
                "file_mask and automask must be not None"
            )
            mask = file_mask | automask

        if mask is None:
            raise RuntimeError(f"Cannot parse mask_config:\n{mask_config}")

    print("DEBUG: Mask calculation complete")

    print("DEBUG: Creating GeometryRefinement object...")
    gr = GeometryRefinement(
        rings,
        calibrant=calibrant,
        dist=dist,
        poni1=poni1,
        poni2=poni2,
        rot1=rot1,
        rot2=rot2,
        rot3=rot3,
        detector=detector,
        wavelength=wavelength,
    )
    print("DEBUG: GeometryRefinement object created. Starting refine3()...")
    print(f"DEBUG: refine3() fix parameters: {fix}")

    try:
        import threadpoolctl

        with threadpoolctl.threadpool_limits(limits=1, user_api="blas"):
            with threadpoolctl.threadpool_limits(limits=1, user_api="openmp"):
                with threadpoolctl.threadpool_limits(limits=1):
                    gr.refine3(fix=fix)
        print("DEBUG: refine3() completed successfully")
    except ImportError:
        gr.refine3(fix=fix)
        print("DEBUG: refine3() completed successfully")
    refined = {
        "dist": gr._dist,
        "poni1": gr._poni1,
        "poni2": gr._poni2,
        "rot1": gr._rot1,
        "rot2": gr._rot2,
        "rot3": gr._rot3 % (2 * np.pi),
    }
    for k, v in refined.items():
        refined[k] = float(v)

    print("DEBUG: Creating IntegratorExtended object...")
    integrator = IntegratorExtended(
        ai_params={"wavelength": wavelength, **refined},
        detector_params={"detector_name": detector_name, "pixel_size": pixel_size},
        mask=mask,
    )
    print("DEBUG: IntegratorExtended object created")

    print(f"DEBUG: Starting 1D integration (npt={npt})...")
    q_cal, I_cal, sigma = integrator.integrate1d(calib_data, npt=npt)
    print(f"DEBUG: 1D integration complete. q array length: {len(q_cal)}")

    tth_theor = np.array(calibrant.get_2th())
    q_theor = 4 * np.pi * np.sin(tth_theor / 2) / wavelength * 1e-9

    return {
        "integrator": integrator,
        "refined": refined,
        "curve_calibrated": (q_cal, I_cal, sigma),
        "theoretical_peaks": q_theor,
    }
