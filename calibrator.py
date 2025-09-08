from typing import Optional, Any, Tuple
import numpy as np
import pyFAI
import pyFAI.calibrant
from pyFAI.detectors import Pilatus1M
from pyFAI.geometryRefinement import GeometryRefinement
from pyFAI.calibrant import CALIBRANT_FACTORY


class Calibrator:
    def __init__(self):
        """
        Calibrator maintains experimental data and ring search configuration.
        Core fields are initialized to None; use set_ring_search to configure.
        """
        # Experimental image data
        self.data: Optional[Any] = None
        
        # Ring search algorithm parameters
        self.q_start: float = 0.95
        self.q_stop: float = 0.995
        self.min_segment_len: int = 50
        self.I_threshold: float = 80.
        self.r_max: float = 700  # pixels
        self.r_step: float = 3  # pixels
        self.peak_width: int = 60  # pixels
        # You may add other ring detection algorithm internals if needed

        # Initial guess/fields for measurement/calibration parameters
        self.dist: Optional[float] = None  # meters
        self.wavelength: Optional[float] = None  # meters
        self.pixel_size: Optional[Tuple[float, float]] = None  # meters
        self.beam_center_y: Optional[float] = None  # pixels
        self.beam_center_x: Optional[float] = None  # pixels
        self.rot1: float = 0.
        self.rot2: float = 0.
        self.rot3: float = 0.

    def set_initial_point(self, **kwargs):
        """
        Set initial geometry parameters for the detector calibration.
        
        Parameters:
        -----------
        **kwargs : key-value pairs
            Valid parameters: 'dist', 'wavelength', 'pixel_size', 
            'beam_center_x', 'beam_center_y',
            'rot1', 'rot2', 'rot3'
        """
        valid_keys = [
            'dist', 'wavelength', 'pixel_size',
            'beam_center_x', 'beam_center_y',
            'rot1', 'rot2', 'rot3'
        ]
        for k, v in kwargs.items():
            if k in valid_keys:
                setattr(self, k, v)
            else:
                raise ValueError(f"Unrecognized geometry parameter: {k}")

    def set_ring_search(self, **kwargs):
        """
        Set parameters for the ring search/detection algorithm.
        All key-value pairs provided as kwargs will overwrite attributes
        if they are recognized ring search algorithm fields.
        """
        valid_keys = [
            'q_start', 'q_stop', 'min_segment_len', 'I_threshold',
            'r_max', 'r_step', 'peak_width'
        ]
        for k, v in kwargs.items():
            if k in valid_keys:
                setattr(self, k, v)
            else:
                raise ValueError(f"Unrecognized ring search parameter: {k}")

    def refine(self, calibrant_name: str = 'AgBh', fix: Tuple[str]=('wavelength', 'rot3'), npt: int = 1000):
        """
        Refine the detector geometry to calibrant rings, returning:
          - refined_params: [dist, poni1, poni2, rot1, rot2, rot3]
          - calibrated_curve: tuple (q, I) using the refined geometry
          - q_theor: theoretical peak positions for the calibrant
        """
        assert self.data is not None, 'Experimental data (self.data) must be set.'
        assert self.pixel_size, 'pixel_size must be set.'
        assert self.wavelength, 'wavelength must be set.'
        assert self.dist is not None, 'Initial geometry guess (dist) required.'

        if self.beam_center_y is None or self.beam_center_x is None:
            center = find_center(self.data, 
                                q_start=self.q_start, q_stop=self.q_stop, 
                                min_segment_len=self.min_segment_len)
            self.beam_center_y, self.beam_center_x = center
        else:
            center = np.array([self.beam_center_y, self.beam_center_x])

        # Get ring pixels for geometry refinement
        rings, r_rings, _ = get_rings(
            self.data, center,
            q_start=self.q_start,
            q_stop=self.q_stop,
            min_segment_len=self.min_segment_len,
            r_max=self.r_max,
            r_step=self.r_step,
            peak_width=self.peak_width,
            I_threshold=self.I_threshold,
        )

        # Calibrant (powder) model
        calibrant = CALIBRANT_FACTORY(calibrant_name)
        calibrant.set_wavelength(self.wavelength)
        detector = Pilatus1M(self.pixel_size[0], self.pixel_size[1])
        poni1 = self.pixel_size[0] * self.beam_center_y
        poni2 = self.pixel_size[1] * self.beam_center_x

        # GeometryRefinement
        gr = GeometryRefinement(
            rings,
            calibrant=calibrant,
            dist=self.dist,
            poni1=poni1,
            poni2=poni2,
            rot1=self.rot1,
            rot2=self.rot2,
            rot3=self.rot3,
            detector=detector,
            wavelength=self.wavelength,
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
        # Use refined geometry to integrate (q, I) - "calibrated" curve
        ai = pyFAI.AzimuthalIntegrator(
            **refined,
            detector=detector,
            wavelength=self.wavelength
        )

        q_cal, I_cal = ai.integrate1d(self.data, npt=npt)

        # Get theoretical/"ideal" calibrant ring positions
        tth_theor = np.array(calibrant.get_2th())
        q_theor = 4 * np.pi * np.sin(tth_theor / 2) / self.wavelength * 1e-9

        return refined, (q_cal, I_cal), q_theor

# ===== UTILITY FUNCTIONS =====
import scipy.ndimage as ndi
from scipy.spatial.distance import cdist
from sklearn.cluster import DBSCAN

def find_center(
    data: np.ndarray,
    q_start: float = 0.95,
    q_stop: float = 0.995,
    min_segment_len: int = 50,
):
    """Robust center estimation using local pixel clustering and circle fitting."""
    q0 = np.quantile(data, q_start)
    q1 = np.quantile(data, q_stop)
    idx = np.where((data > q0) & (data < q1))
    ring_pixels = np.transpose(np.vstack(idx))
    dbscan = DBSCAN(min_samples=10, eps=30.)
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
    return center

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

def get_rings(
    data: np.ndarray, center: np.ndarray,
    q_start=0.95, q_stop=0.995, min_segment_len=50,
    r_max=700, r_step=3, peak_width=60, I_threshold=80.,
):
    """
    Locates detector rings via azimuthal search and local maxima.
    Returns (ring pixel array, radii used, integrated profile).
    """
    data = np.copy(data)
    data[data > np.quantile(data, q_stop)] = 0
    center = center.reshape(1, -1)
    # radial distances
    rs = np.arange(0, r_max + 1, r_step).reshape(-1, 1)
    pixel_coords = np.fromfunction(
        lambda i, j: (i // data.shape[1]) * (j == 0) + (i % data.shape[1]) * (j == 1),
        (data.shape[0] * data.shape[1], 2),
        dtype=int)
    r_i = cdist(center, pixel_coords)
    I_i = (data[pixel_coords[:, 0], pixel_coords[:, 1]]).reshape(-1, 1)
    integrated = np.maximum(10. - np.abs(rs - r_i), 0) @ I_i / (rs + 10.)
    integrated = integrated.flatten()

    idx_rings = find_local_maxima(integrated, peak_width // r_step, peak_width // r_step)
    r_rings = rs[idx_rings]
    rings = []
    for i, r in enumerate(r_rings):
        ring = pixel_coords[((10. - np.abs(r - r_i)) > 0.).flatten() & (I_i > I_threshold).flatten()]
        if len(ring) > min_segment_len:
            rings.append(np.hstack([ring, np.full((len(ring), 1), i)]))
    if len(rings) == 0:
        raise RuntimeError("No rings found with current threshold/search parameters")
    rings = np.vstack(rings)
    return rings, r_rings, (rs, integrated)

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

