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
    Build an automatic detector mask: beam-stop disk, all negative-intensity
    pixels, and optionally statistical outliers in log-intensity (local IQR).
    """
    if window_size % 2 == 0:
        raise ValueError("window_size must be odd for symmetric neighborhood")

    data = np.asarray(data, dtype=float)
    negative_mask = data < 0.0

    beam_mask = np.fromfunction(
        lambda i, j: np.linalg.norm(
            [i - center_y_px, j - center_x_px], axis=0
        )
        <= r_beam_px,
        data.shape,
    )

    mask = beam_mask | negative_mask
    if not calc_abnormal_mask:
        return mask

    data_shifted = data - min(np.min(data), 0.0)
    log_data = np.log1p(data_shifted)

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
    return mask | abnormal_mask


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

    print("INFO: Starting effective mask calculation (before refinement)...")
    # mask_config may carry provenance keys (mode / requested_mode); only pass
    # parameters accepted by calc_beam_abnormal_mask.
    _automask_keys = ("calc_abnormal_mask", "window_size", "iqr_tol")
    automask_ops = {
        k: v for k, v in (mask_config or {}).items() if k in _automask_keys
    }
    print("INFO: Calculating automatic mask (beam-stop + negative pixels + optional IQR)...")
    automask = calc_beam_abnormal_mask(
        calib_data, center_y_px, center_x_px, r_beam_px, **automask_ops
    )
    print("INFO: Automatic mask calculated")

    if mask_path is not None:
        print(f"INFO: Reading user mask from file (will OR with auto, not overwrite): {mask_path}")
        file_mask = IntegratorExtended.read_mask(mask_path)
        mask = file_mask | automask
    else:
        mask = automask

    print("INFO: Effective mask calculation complete")

    print("INFO: Creating GeometryRefinement object...")
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
    print("INFO: GeometryRefinement object created. Starting refine3()...")
    print(f"INFO: refine3() fix parameters: {fix}")

    try:
        import threadpoolctl

        with threadpoolctl.threadpool_limits(limits=1, user_api="blas"):
            with threadpoolctl.threadpool_limits(limits=1, user_api="openmp"):
                with threadpoolctl.threadpool_limits(limits=1):
                    gr.refine3(fix=fix)
        print("INFO: refine3() completed successfully")
    except ImportError:
        gr.refine3(fix=fix)
        print("INFO: refine3() completed successfully")
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

    print("INFO: Creating IntegratorExtended object...")
    integrator = IntegratorExtended(
        ai_params={"wavelength": wavelength, **refined},
        detector_params={"detector_name": detector_name, "pixel_size": pixel_size},
        mask=mask,
        auto_mask=automask,
    )
    print("INFO: IntegratorExtended object created")

    print(f"INFO: Starting 1D integration (npt={npt})...")
    q_cal, I_cal, sigma = integrator.integrate1d(calib_data, npt=npt)
    print(f"INFO: 1D integration complete. q array length: {len(q_cal)}")

    tth_theor = np.array(calibrant.get_2th())
    q_theor = 4 * np.pi * np.sin(tth_theor / 2) / wavelength * 1e-9

    return {
        "integrator": integrator,
        "refined": refined,
        "curve_calibrated": (q_cal, I_cal, sigma),
        "theoretical_peaks": q_theor,
    }
