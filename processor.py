from typing import Optional, Any, Tuple, List
import os
import json

import numpy as np
import scipy.ndimage as ndi
from scipy.spatial.distance import cdist
from sklearn.cluster import DBSCAN
import pyFAI
import pyFAI.calibrant
from pyFAI.detectors import Pilatus1M
from pyFAI.geometryRefinement import GeometryRefinement
from pyFAI.calibrant import CALIBRANT_FACTORY

from utils import *


class IntegratorExtended:
    def __init__(self, ai_params, detector_params, mask):
        self.detector_params = detector_params
        self.ai_params = ai_params
        self.mask = mask
        
        self.detector = get_detector(**detector_params)
        self.ai = pyFAI.AzimuthalIntegrator(detector=self.detector, **self.ai_params)

    def to_disk(self, directory):
        os.makedirs(directory, exist_ok=True)  # Ensure the subdir exists
        with open(os.path.join(directory, 'detector_params.json'), 'w') as fwrite:
            json.dump(self.detector_params, fwrite)
        with open(os.path.join(directory, 'ai_params.json'), 'w') as fwrite:
            json.dump(self.ai_params, fwrite)
        if self.mask is not None:
            np.save(os.path.join(directory, 'mask.npy'), self.mask)
    
    @classmethod
    def from_disk(cls, directory):
        with open(os.path.join(directory, 'detector_params.json'), 'r') as fread:
            detector_params = json.load(fread)
        with open(os.path.join(directory, 'ai_params.json'), 'r') as fread:
            ai_params = json.load(fread)
        
        mask_path = os.path.join(directory, 'mask.npy')
        if os.path.exists(mask_path):
            mask = np.load(mask_path)
        else:
            mask = None
        
        return cls(mask=mask, detector_params=detector_params, ai_params=ai_params)
    
    def integrate1d(self, saxs_2d, npt):
        q, I = self.ai.integrate1d(saxs_2d, npt=npt, mask=self.mask)
        return q, I


def fit_circle(points: np.ndarray):
    y = points[:, 0]
    x = points[:, 1]
    n = len(x)
    sum_x = np.sum(x)
    sum_y = np.sum(y)
    sum_x2 = np.sum(x ** 2)
    sum_y2 = np.sum(y ** 2)
    sum_xy = np.sum(x * y)
    sum_x3 = np.sum(x ** 3)
    sum_y3 = np.sum(y ** 3)
    sum_x2y = np.sum(x ** 2 * y)
    sum_xy2 = np.sum(x * y ** 2)
    sum_x2_y2 = sum_x2 + sum_y2
    A = np.array([
        [sum_x2, sum_xy, sum_x],
        [sum_xy, sum_y2, sum_y],
        [sum_x,  sum_y,  n]
    ])
    B = np.array([
        -(sum_x3 + sum_xy2),
        -(sum_y3 + sum_x2y),
        -sum_x2_y2
    ])
    c, d, e = np.linalg.solve(A, B)
    b = -d / 2
    a = -c / 2
    r = np.sqrt(a ** 2 + b ** 2 - e)
    return (b, a, r)


def find_center(
    data: np.ndarray,
    q_start: float = 0.95,
    q_stop: float = 0.995,
    min_segment_len: int = 50,
    dbscan_eps=30.,
    dbscan_min_samples=10,
    ):
    """Robust center estimation using local pixel clustering and circle fitting."""
    q0 = np.quantile(data, q_start)
    q1 = np.quantile(data, q_stop)
    idx = np.where((data > q0) & (data < q1))
    ring_pixels = np.transpose(np.vstack(idx))
    dbscan = DBSCAN(min_samples=dbscan_min_samples, eps=dbscan_eps)
    cluster = dbscan.fit_predict(ring_pixels)
    ring_pixels = np.hstack([ring_pixels, cluster.reshape(-1, 1)])
    centers = []
    for c in np.unique(cluster):
        if c == -1:
            continue
        ring = ring_pixels[ring_pixels[:, 2] == c][:, [0, 1]]
        if len(ring) < min_segment_len:
            continue
        center_y, center_x, _ = fit_circle(ring)
        centers.append((center_y, center_x))
    center = np.median(centers, axis=0)
    return {
        'center_y_px': center[0], 
        'center_x_px': center[1], 
        'clusters': ring_pixels
    }


def find_local_maxima(arr, left_neighbors=1, right_neighbors=1):
    n = len(arr)
    if left_neighbors < 0 or right_neighbors < 0:
        raise ValueError("Number of neighbors must be non-negative")
    window_size = left_neighbors + right_neighbors + 1
    if window_size > n or n == 0:
        return np.array([], dtype=int)
    windows = np.lib.stride_tricks.sliding_window_view(arr, window_size)
    center = left_neighbors
    centers = windows[:, center]
    is_max = np.all(centers[:, None] >= np.delete(windows, center, axis=1), axis=1)
    return np.where(is_max)[0] + left_neighbors


def get_interring_dist_px(dist_guess, lmbd, px_size, calibrant_name='AgBh'):
    # # for some reason the formula does not work
    # d = CALIBRANT_FACTORY(calibrant_name=calibrant_name).get_dSpacing()[0] * 1.e-10
    # interring_dist = dist_guess * lmbd / d / px_size
    # # print(f'D-spacing: {d}')
    # # print(f'Interring dist: {interring_dist}')

    # so have to use some crutch here
    interring_dist = 40 * dist_guess / 0.7 * lmbd / 1.445e-10

    return interring_dist


def find_rings(
    calib_data, center_y_px, center_x_px, r_max_px,
    interring_dist_px, 
    q_stop=0.995, r_beam_px=35, r_step_px=3, ring_I_threshold=80.0, min_segment_len=50,
    ):
    """
    Locates detector rings via azimuthal search and local maxima.
    Returns (ring pixel array, radii used, integrated profile).
    """
    
    data = np.copy(calib_data)
    data[data > np.quantile(data, q_stop)] = 0
    center = np.array([center_y_px, center_x_px]).reshape(1, -1)
    # radial distances
    rs = np.arange(r_beam_px, r_max_px + 1, r_step_px).reshape(-1, 1)
    pixel_coords = np.fromfunction(
        lambda i, j: (i // data.shape[1]) * (j == 0) + (i % data.shape[1]) * (j == 1),
        (data.shape[0] * data.shape[1], 2),
        dtype=int)
    r_i = cdist(center, pixel_coords)
    # integration
    I_i = (data[pixel_coords[:, 0], pixel_coords[:, 1]]).reshape(-1, 1)
    integrated = np.maximum(10. - np.abs(rs - r_i), 0) @ I_i / (rs + 10.)
    integrated = integrated.flatten()

    # finding rings through peaks
    peak_width_estim = int(0.9 * interring_dist_px / r_step_px)
    idx_rings = find_local_maxima(
        integrated, peak_width_estim, peak_width_estim)
    r_rings = rs[idx_rings]
    rings = []
    for i, r in enumerate(r_rings):
        ring = pixel_coords[((10. - np.abs(r - r_i)) > 0.).flatten() & (I_i > ring_I_threshold).flatten()]
        if len(ring) > min_segment_len:
            rings.append(np.hstack([ring, np.full((len(ring), 1), i)]))
    if len(rings) == 0:
        raise RuntimeError("No rings found with current threshold/search parameters")
    rings = np.vstack(rings)
    return {'rings': rings, 'radii_px': r_rings, 'integrated': (rs, integrated)}


def get_mask(data, center_y_px, center_x_px, r_beam_px):
    return np.fromfunction(lambda i, j: np.linalg.norm([i - center_y_px, j - center_x_px], axis=0) <= r_beam_px,
                           data.shape)


def get_detector(detector_name='Pilatus1M', pixel_size=None):
    assert pixel_size is not None
    if detector_name == 'Pilatus1M':
        return Pilatus1M(pixel_size[0], pixel_size[1])
    else:
        raise ValueError(f'Unknown detector: {detector_name}')


def refine(calib_data, rings, wavelength, dist, pixel_size, center_y_px, center_x_px,
           calibrant_name,
           r_beam_px,
           rot1=0, rot2=0, rot3=0, detector_name='Pilatus1M', 
           fix: Tuple[str]=('wavelength', 'rot3'), npt: int = 1000):
    """
    Refine the detector geometry to calibrant rings, returning:
        - refined_params: [dist, poni1, poni2, rot1, rot2, rot3]
        - calibrated_curve: tuple (q, I) using the refined geometry
        - q_theor: theoretical peak positions for the calibrant
    """
    assert detector_name is not None and all(s is not None for s in pixel_size), 'detector and pixel_size must be set.'
    assert wavelength is not None, 'wavelength must be set.'

    detector = get_detector(detector_name, pixel_size)

    # Calibrant (powder) model
    calibrant = CALIBRANT_FACTORY(calibrant_name)
    calibrant.set_wavelength(wavelength)
    poni1 = pixel_size[0] * center_y_px
    poni2 = pixel_size[1] * center_x_px

    # GeometryRefinement
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

    # Refine geometry, fixing selected parameters (e.g., wavelength, rot3)
    gr.refine3(fix=fix)
    refined = {
        'dist': gr._dist,
        'poni1': gr._poni1,
        'poni2': gr._poni2,
        'rot1': gr._rot1,
        'rot2': gr._rot2,
        'rot3': gr._rot3 % (2 * np.pi)
    }
    for k, v in refined.items():
        refined[k] = float(v)

    # Use refined geometry to integrate (q, I) - "calibrated" curve
    integrator = IntegratorExtended(
        ai_params={'wavelength': wavelength, **refined},
        detector_params={'detector_name': detector_name, 'pixel_size': pixel_size},
        mask=get_mask(calib_data, center_y_px, center_x_px, r_beam_px)
        # mask=None
    )

    q_cal, I_cal = integrator.integrate1d(calib_data, npt=npt)

    # Get theoretical/"ideal" calibrant ring positions
    tth_theor = np.array(calibrant.get_2th())
    q_theor = 4 * np.pi * np.sin(tth_theor / 2) / wavelength * 1e-9

    return {'integrator': integrator, 'refined': refined, 'curve_calibrated': (q_cal, I_cal), 'theoretical_peaks': q_theor}


def integrate_2d_to_1d(integrator, saxs_2d, npt=1000, destpath=None, metadata=None):
    q, I = integrator.integrate1d(saxs_2d, npt=npt)
    if destpath is not None:
        if metadata is None:
            metadata = dict()
        write_saxs(destpath, q, I, metadata)
    return q, I


def subtract_buffer(
    buffer_path, src_path, destpath,
    image_path=None, 
    method='match_tail', match_tail_ops=None, 
    ):
    q_buff, I_buff, _ = read_saxs(buffer_path)

    scaling_factor = 1.

    q, I, _ = read_saxs(src_path)
    if method == 'match_tail':
        algo_ops = {'q_range_abs': None, 
                    'q_range_rel': (0.8, None), 
                    'approach_factor': 0.98}
        if match_tail_ops is None:
            match_tail_ops = dict()
        algo_ops.update(match_tail_ops)

        assert algo_ops['q_range_abs'] is None or algo_ops['q_range_rel'] is None, 'cant set both q_range_abs and q_range_rel'

        if not np.array_equal(q, q_buff):
            # If not, we need to interpolate the buffer to match sample q-values
            I_buff = np.interp(q, q_buff, I_buff)

        q_max = np.max(q)
        if algo_ops['q_range_rel'] is not None:
            q0, q1 = algo_ops['q_range_rel']
            if q1 is None:
                q1 = 1.
            algo_ops['q_range_abs'] = q0 * q_max, q1 * q_max
        q0, q1 = algo_ops['q_range_abs']
        if q1 is None:
            q1 = q_max
        idx = (q0 < q) & (q < q1)

        q_tail = q[idx]
        I_tail = I[idx]
        I_buff_tail = I_buff[idx]

        I_tail = whittaker_smooth(I_tail, lmbd=1.e+10, d=3)
        I_buff_tail = whittaker_smooth(I_buff_tail, lmbd=1.e+10, d=3)
        
        # idx = I_buff_tail > 1.e-4 * np.max(I_buff)
        # q_tail = q_tail[idx]
        # I_tail = I_tail[idx]
        # I_buff_tail = I_buff_tail[idx]

        ratios = I_tail / I_buff_tail
        scaling_factor = np.min(ratios)

    scaling_factor *= algo_ops['approach_factor']
    I_buffer_scaled = I_buff * scaling_factor
    I_sub = I - I_buffer_scaled

    write_saxs(destpath, q, I_sub, metadata={
                'type': 'sub',
                'sample_path': src_path,
                'buffer_path': buffer_path
            } 
        )
    
    return q, I_sub, I_buffer_scaled
