from typing import Optional, Any, Tuple, List
import os, re
import json, yaml
import glob
import subprocess

import numpy as np
import scipy.ndimage as ndi
from scipy.spatial.distance import cdist
from sklearn.cluster import DBSCAN
import pyFAI
import pyFAI.calibrant
from pyFAI.detectors import Pilatus1M
from pyFAI.geometryRefinement import GeometryRefinement
from pyFAI.calibrant import CALIBRANT_FACTORY

import fabio

# from sasmodels.core import load_model
# from sasmodels.fit import Fit
# import sasmodels.core
# import sasmodels.bumps_model
# import bumps.names as bn
# from bumps.fitproblem import FitProblem
# from bumps.fitters import fit

import matplotlib as mpl
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import seaborn as sns

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
    
    @staticmethod
    def read_mask(mask_path):
        _, ext = os.path.splitext(mask_path)
        if ext == '.npy':
            mask = np.load(mask_path).astype('bool')
        elif ext == '.txt':
            mask = np.loadtxt(mask_path).astype('bool')
        elif ext == '.msk':
            # assume fit2d .msk file
            mask = fabio.open(mask_path).data.astype('bool')
            mask = np.flip(mask, axis=0)  # noticed that the mask is read upside-down
        else:
            raise RuntimeError(f"Unsupported file extension for mask: {ext}")
        return mask
    
    @classmethod
    def from_disk(cls, directory):
        with open(os.path.join(directory, 'detector_params.json'), 'r') as fread:
            detector_params = json.load(fread)
        with open(os.path.join(directory, 'ai_params.json'), 'r') as fread:
            ai_params = json.load(fread)
        
        obj = cls(ai_params=ai_params, detector_params=detector_params, mask=None)
        
        mask_pattern = os.path.join(directory, 'mask.*')
        mask_path = glob.glob(mask_pattern)
        if len(mask_path) == 0:
            return obj
        if len(mask_path) == 1:            
            mask_path, = mask_path
            obj.set_mask(mask_path)
        else:
            raise RuntimeError(f'Too many files match mask pattern "{mask_pattern}"')
        
        return obj
    
    def set_mask(self, mask_path: str, combine_with_prev=False):
        mask = IntegratorExtended.read_mask(mask_path)
        if combine_with_prev and self.mask is not None:
            self.mask = self.mask | mask
        else:
            self.mask = mask
    
    def integrate1d(self, saxs_2d, npt):
        q, I, sigma = self.ai.integrate1d(saxs_2d, npt=npt, mask=self.mask, error_model='poisson')
        return q, I, sigma


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

    Args:
        data: 2D detector image.
        center_y_px/center_x_px: beam center in pixels.
        r_beam_px: radius of the beam-stop mask (pixels).
        window_size: odd side length of the local neighborhood for IQR stats.
        iqr_tol: multiplier for the IQR fence; higher is more permissive.
    """
    if window_size % 2 == 0:
        raise ValueError("window_size must be odd for symmetric neighborhood")

    data = np.asarray(data, dtype=float)

    # Base geometric mask for the beam stop.
    beam_mask = np.fromfunction(
        lambda i, j: np.linalg.norm(
            [i - center_y_px, j - center_x_px], axis=0
        )
        <= r_beam_px,
        data.shape,
    )

    # # debug: since log is performed, we need to avoid negative intensity values to not get mistakes
    # plt.hist(data[data<1.0].flatten())
    # plt.show()
    
    if calc_abnormal_mask:
        # Log transform to stabilize the exponential intensity decay.
        data = data - min(np.min(data), 0.0)  # to avoid numerical mistakes in log
        log_data = np.log1p(data)

        # Local quartiles with reflection padding for correct edge handling.
        q1 = ndi.percentile_filter(
            log_data, percentile=25, size=window_size, mode="reflect"
        )
        q3 = ndi.percentile_filter(
            log_data, percentile=75, size=window_size, mode="reflect"
        )
        iqr = q3 - q1

        # Fence thresholds; small epsilon avoids divide-by-zero in flat regions.
        eps = 1e-12
        lower = q1 - iqr_tol * iqr
        upper = q3 + iqr_tol * iqr
        abnormal_mask = (log_data < lower - eps) | (log_data > upper + eps)
        return beam_mask | abnormal_mask
    
    else:
        return beam_mask


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
           fix: Tuple[str]=('wavelength', 'rot3'), npt: int = 1000, mask_path=None, mask_config=None):
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

    # CRITICAL: Read and prepare mask BEFORE entering threadpool limits context
    # Mask operations (fabio, scipy) can trigger threading that conflicts with limits
    print(f'DEBUG: Starting mask calculation (before refinement)...')
    mask = None
    if mask_config is not None:
        mode = mask_config['mode']
        print(f'DEBUG: Mask config mode: {mode}')
        
        automask = None
        if mode in ['auto', 'combined']:
            print(f'DEBUG: Calculating automatic mask...')
            automask_ops = {k: v for k, v in mask_config.items() if k != 'mode'}
            automask = calc_beam_abnormal_mask(
                calib_data, center_y_px, center_x_px, r_beam_px, 
                **automask_ops)
            print(f'DEBUG: Automatic mask calculated')
        
        file_mask = None
        if mode in ['from_file', 'combined']:
            assert mask_path is not None
            print(f'DEBUG: Reading mask from file: {mask_path}')
            # Read mask BEFORE threadpool limits to avoid fabio/numpy threading conflicts
            file_mask = IntegratorExtended.read_mask(mask_path)
        
        # Combine masks based on mode
        if mode == 'auto':
            mask = automask
        elif mode == 'from_file':
            mask = file_mask
        elif mode == 'combined':
            assert file_mask is not None and automask is not None, 'file_mask and automask must be not None'
            mask = file_mask | automask
        
        if mask is None:
            raise RuntimeError(f"Cannot parse mask_config:\n{mask_config}")
    
    print(f'DEBUG: Mask calculation complete')

    # GeometryRefinement
    print(f'DEBUG: Creating GeometryRefinement object...')
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
    print(f'DEBUG: GeometryRefinement object created. Starting refine3()...')
    print(f'DEBUG: refine3() fix parameters: {fix}')

    # Refine geometry, fixing selected parameters (e.g., wavelength, rot3)
    # This is the computationally intensive step
    # Ensure threading limits are applied during refinement to prevent deadlocks
    try:
        import threadpoolctl
        # Set limits for all threadpool libraries
        # Note: 'blas' controls MKL, OpenBLAS, and other BLAS implementations
        # 'openmp' controls OpenMP threading
        with threadpoolctl.threadpool_limits(limits=1, user_api='blas'):
            with threadpoolctl.threadpool_limits(limits=1, user_api='openmp'):
                # Also set global limit as fallback
                with threadpoolctl.threadpool_limits(limits=1):
                    gr.refine3(fix=fix)
        print(f'DEBUG: refine3() completed successfully')
    except ImportError:
        # threadpoolctl not available, rely on environment variables
        gr.refine3(fix=fix)
        print(f'DEBUG: refine3() completed successfully')
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
    print(f'DEBUG: Creating IntegratorExtended object...')
    integrator = IntegratorExtended(
        ai_params={'wavelength': wavelength, **refined},
        detector_params={'detector_name': detector_name, 'pixel_size': pixel_size},
        mask=mask
    )
    print(f'DEBUG: IntegratorExtended object created')

    print(f'DEBUG: Starting 1D integration (npt={npt})...')
    q_cal, I_cal, sigma = integrator.integrate1d(calib_data, npt=npt)
    print(f'DEBUG: 1D integration complete. q array length: {len(q_cal)}')

    # Get theoretical/"ideal" calibrant ring positions
    tth_theor = np.array(calibrant.get_2th())
    q_theor = 4 * np.pi * np.sin(tth_theor / 2) / wavelength * 1e-9

    return {
        'integrator': integrator, 'refined': refined, 
        'curve_calibrated': (q_cal, I_cal, sigma), 'theoretical_peaks': q_theor}


def autocalib(calibration_image_path: str, config: dict, mask_path: Optional[str] = None) -> dict:
    """
    Performs calibration using the provided image and configuration.
    
    This function implements the calibration process based on Controller.autocalib,
    performing center refinement, ring search, and geometry refinement.
    
    Args:
        calibration_image_path: Full path to the calibration .tif image
        context: Context object with calibration parameters
        mask_path: Optional path to mask file (.msk, .npy, or .txt)
    
    Returns:
        Dictionary containing:
        - 'refined': Dictionary with refined calibration parameters:
            - dist: Sample-to-detector distance (m)
            - poni1: Point of normal incidence 1 (m)
            - poni2: Point of normal incidence 2 (m)
            - rot1: Detector rotation 1 (radians)
            - rot2: Detector rotation 2 (radians)
            - rot3: Detector rotation 3 (radians)
            - wavelength: X-ray wavelength (m)
        - 'integrator': IntegratorExtended object with mask applied
    """
    print(f'DEBUG: Autocalib is called. Parameters are: {", ".join(config.keys())}')
    # Load calibration image
    calib_data = read_from_tiff(calibration_image_path)
    
    # Step 1: Center refinement
    center_ref_params = {
        k: config['center_refinement'][k] 
        for k in ['q_start', 'q_stop', 'min_segment_len']
    }
    center_step_ret = find_center(calib_data, **center_ref_params)
    print(f'DEBUG: Center refinement is done. Parameters are: {", ".join(center_step_ret.keys())}')
    
    # Step 2: Calculate interring distance
    d_geom = config['detector_geometry']
    interring_dist_px = get_interring_dist_px(
        d_geom['dist'], d_geom['wavelength'], d_geom['pixel_size'][0],
        calibrant_name=config['calibrant_name']
    )
    
    # Step 3: Ring search
    ring_search_params = {
        k: config['ring_search'][k] 
        for k in ['q_stop', 'ring_I_threshold', 'r_max_px', 'r_step_px']
    }
    ring_search_params.update({
        'r_beam_px': config['r_beam_px'],
        'center_y_px': center_step_ret['center_y_px'],
        'center_x_px': center_step_ret['center_x_px'],
        'interring_dist_px': interring_dist_px
    })
    rings_step_ret = find_rings(calib_data, **ring_search_params)
    print(f'DEBUG: Ring search is done. Parameters are: {", ".join(rings_step_ret.keys())}')
    print(f'DEBUG: Number of rings found: {len(rings_step_ret.get("radii_px", []))}')

    # Step 4: Geometry refinement
    print(f'DEBUG: Starting geometry refinement...')
    geometry_params = {
        k: config['detector_geometry'][k] 
        for k in ['dist', 'wavelength', 'pixel_size', 'rot1', 'rot2', 'rot3']
    }
    geometry_params.update({
        'r_beam_px': config['r_beam_px'],
        'center_y_px': center_step_ret['center_y_px'],
        'center_x_px': center_step_ret['center_x_px'],
        'calibrant_name': config['calibrant_name'],
        'mask_path': mask_path,
        'mask_config': config['mask_config'] if 'mask_config' in config else {'mode': 'auto'}
    })
    print(f'DEBUG: Geometry params prepared. Calling refine()...')
    refine_step_ret = refine(calib_data, rings_step_ret['rings'], **geometry_params)
    print(f'DEBUG: Geometry refinement is done. Parameters are: {", ".join(refine_step_ret.keys())}')
    
    # Extract refined parameters and add wavelength
    refined = refine_step_ret['refined'].copy()
    refined['wavelength'] = config['detector_geometry']['wavelength']
    return {'refined': refined, 'integrator': refine_step_ret['integrator']}


def integrate_2d_to_1d(integrator, saxs_2d, npt=1000, destpath=None, metadata=None):
    q, I, sigma = integrator.integrate1d(saxs_2d, npt=npt)
    if destpath is not None:
        if metadata is None:
            metadata = dict()
        write_saxs(destpath, q, I, sigma, metadata)
    return q, I, sigma


def subtract_buffer(
    buffer_path, src_path, destpath,
    image_path=None, 
    method='match_tail', match_tail_ops=None, 
    ):
    q_buff, I_buff, sigma_buff, _ = read_saxs(buffer_path)

    scaling_factor = 1.

    q, I, sigma, _ = read_saxs(src_path)
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

    sigma_buffer_scaled = sigma_buff * scaling_factor 
    sigma_sub = sigma_buffer_scaled + sigma

    write_saxs(destpath, q, I_sub, sigma_sub, metadata={
                'type': 'sub',
                'sample_path': src_path,
                'buffer_path': buffer_path
            } 
        )
    
    return q, I_sub, I_buffer_scaled, sigma_sub, sigma_buffer_scaled


# DOES NOT WORK
# import numpy as np
# import yaml
# import bumps.names as bn  # pyright: ignore[reportUnusedImport]
# import sasmodels.core
# from sasmodels.data import Data1D
# from sasmodels.bumps_model import Model as SasModel, Experiment
# from bumps.fitproblem import FitProblem
# from bumps.fitters import fit


# def run_bumps_fit(model_name, data_file, q_min, q_max):
#     """
#     Fit 1D SAXS data using sasmodels + bumps with a YAML-defined parameter set.

#     The YAML file describes:
#       - initial_parameters: starting values (YAML names are mapped to sasmodels names)
#       - pdist_commands: optional polydispersity setup
#       - fit_parameters: which parameters to vary
#     """
#     yaml_path = os.path.join(GLOBALS_DIR, 'primus_models.yml')

#     # Load and crop data to requested q-range
#     q_data, I_data, I_error, meta = read_saxs(data_file)
#     mask = (q_data >= q_min) & (q_data <= q_max)
#     q_data = q_data[mask]
#     I_data = I_data[mask]
#     I_error = I_error[mask]

#     # Load model config
#     with open(yaml_path, 'r') as f:
#         models = yaml.safe_load(f)
#     model_config = models[model_name]

#     # Map YAML parameter names (legacy / uppercase) to sasmodels attribute names
#     name_map = {
#         'R': 'radius',
#         'SIG_R': 'radius_pd',
#         'L': 'length',
#         'SIG_L': 'length_pd',
#         'SCALE': 'scale',
#         'BKG': 'background',
#         'R_CORE': 'radius',
#         'T_SH': 'thickness',
#         'SIG_R_CORE': 'radius_pd',
#         'SLD_CORE': 'sld_core',
#         'SLD_SHELL': 'sld_shell',
#         'SLD_SOLV': 'sld_solvent',
#         'R_POLAR': 'radius_polar',
#         'R_EQ': 'radius_equatorial',
#         'SIG_R_POLAR': 'radius_polar_pd',
#         'RG': 'rg',
#         'RG_CLUSTER': 'radius',
#         'D': 'fractal_dim',
#         'CORR_LENGTH': 'correlation_length',
#         'D_SPACING': 'd_spacing',
#         'SIGMA_D': 'sigma_d',
#         'THICKNESS': 'thickness',
#         'SLD_HEAD': 'sld_head',
#         'SLD_TAIL': 'sld_tail',
#     }

#     def to_model_name(yaml_name: str) -> str:
#         return name_map.get(yaml_name, yaml_name.lower())

#     # Build sasmodels model + bumps wrapper
#     model_info = sasmodels.core.load_model_info(model_name)
#     sm_model = SasModel(model_info)

#     # Set initial parameters
#     for yaml_name, value in model_config['initial_parameters'].items():
#         m_name = to_model_name(yaml_name)
#         if hasattr(sm_model, m_name):
#             getattr(sm_model, m_name).value = value

#     # Configure polydispersity (simple lognormal width on chosen parameter)
#     pdist_commands = model_config.get('pdist_commands', [])
#     if pdist_commands:
#         param_to_pdist = None
#         pdist_type = 'lognormal'
#         pdist_sigma = 0.1
#         for cmd in pdist_commands:
#             if 'param' in cmd and 'value' not in cmd:
#                 param_to_pdist = cmd['param']
#             elif 'type' in cmd:
#                 pdist_type = cmd['type'].lower()
#             elif 'param' in cmd and 'value' in cmd:
#                 pdist_sigma = cmd['value']
#         if param_to_pdist:
#             base = to_model_name(param_to_pdist)
#             width_name = f'{base}_pd'
#             type_name = f'{base}_pd_type'
#             if hasattr(sm_model, width_name):
#                 getattr(sm_model, width_name).value = pdist_sigma
#             if hasattr(sm_model, type_name):
#                 getattr(sm_model, type_name).value = pdist_type

#     # Prepare data for bumps/sasmodels
#     data = Data1D(q_data, I_data, I_error)
#     data.qmin, data.qmax = q_min, q_max
#     experiment = Experiment(data=data, model=sm_model)
#     problem = FitProblem(experiment)

#     # Decide which parameters to fit
#     params_to_fit = model_config['fit_parameters']
#     fit_names_mapped = [to_model_name(name) for name in params_to_fit]
#     for par in problem._parameters:
#         # _parameters is a list of bumps Parameter objects
#         if par.name in fit_names_mapped:
#             par.range(-np.inf, np.inf)  # allow to vary broadly
#         else:
#             par.fixed = True

#     # Run LM fit (fast, deterministic)
#     fit_result = fit(problem, method='lm')

#     # Collect results mapped back to YAML names
#     final_results = {}
#     for yaml_name in params_to_fit:
#         m_name = to_model_name(yaml_name)
#         par = fit_result.par[m_name]
#         final_results[yaml_name] = (par.value, par.stderr)

#     return final_results


# --- Example Usage ---
# Assuming 'my_data.dat' and 'primus_models.yml' are in the same directory
# results = run_sasview_fit('sphere', 'my_data.dat', 0.01, 0.4)
# print(results)


# DOES NOT WORK
# def run_primus_fit(data_file, model_name, q_min, q_max):
#     template_path = os.path.join(TEMPLATES_DIR, 'primus.txt') 
#     yaml_path = os.path.join(GLOBALS_DIR, 'primus_models.yml')
    
#     with open(yaml_path, 'r') as f:
#         models = yaml.safe_load(f)
    
#     model_config = models.get(model_name, {})
#     if not model_config:
#         raise RuntimeError(f'Unknown model: {model_name}')

#     init_params_str = "\n".join([f"PAR {k}={v}" for k, v in model_config['initial_parameters'].items()])
    
#     pdist_str_list = []
#     for cmd in model_config['pdist_commands']:
#         if 'param' in cmd and 'value' in cmd:
#             pdist_str_list.append(f"PAR {cmd['param']}={cmd['value']}")
#         elif 'param' in cmd:
#             pdist_str_list.append(f"PDIST {cmd['param']}")
#         elif 'type' in cmd:
#             pdist_str_list.append(f"PDIST {cmd['type']}")
#     pdist_str = "\n".join(pdist_str_list)

#     fit_params_str = "\n".join([f"FIT {p}" for p in model_config['fit_parameters']])

#     with open(template_path, 'r') as f:
#         script_template = f.read()

#     primus_script = script_template.format(
#         data_file=data_file,
#         model=model_name,
#         q_min=q_min,
#         q_max=q_max,
#         initial_parameters=init_params_str,
#         pdist_commands=pdist_str,
#         fit_parameters=fit_params_str
#     )

#     result = subprocess.run(
#         os.path.join(ATSAS_BIN_PREFIX, 'primus'),
#         input=primus_script,
#         text=True,
#         capture_output=True,
#         check=True,
#         shell=True
#     )
#     output = result.stdout

#     results = {}
#     param_section = False
#     param_pattern = re.compile(r'^\s*(\w+)\s*=\s*([\d.+-eE]+)\s*\+/-\s*([\d.+-eE]+)')

#     for line in output.splitlines():
#         if "FINAL PARAMETERS" in line:
#             param_section = True
#             continue
#         if param_section:
#             match = param_pattern.match(line)
#             if match:
#                 param_name = match.group(1)
#                 value = float(match.group(2))
#                 uncertainty = float(match.group(3))
#                 results[param_name] = (value, uncertainty)
#             else:
#                 break
                
#     return results, output
