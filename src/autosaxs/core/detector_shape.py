"""Detector frame / mask shape helpers (H, W).

Single measure site for mask↔image compatibility used by skills and liveview.
"""

from __future__ import annotations

from typing import Tuple

import numpy as np

from autosaxs.core.integrator import IntegratorExtended
from autosaxs.core.utils import read_from_tiff


def as_hw(arr) -> Tuple[int, int]:
    """Return (H, W) for a 2D array; first frame if multi-frame. Raises ValueError if unusable."""
    a = np.asarray(arr)
    if a.ndim > 2:
        a = a.reshape((-1,) + a.shape[-2:])[0]
    if a.ndim != 2 or int(a.shape[0]) < 1 or int(a.shape[1]) < 1:
        raise ValueError(f"expected non-empty 2D image/mask, got shape {getattr(a, 'shape', None)}")
    return int(a.shape[0]), int(a.shape[1])


def frame_shape_hw(path: str) -> Tuple[int, int]:
    """Shape of a detector TIFF (same load path as integrate)."""
    return as_hw(read_from_tiff(path))


def mask_shape_hw(path: str) -> Tuple[int, int]:
    """Shape of a mask file (``.npy`` / ``.txt`` / ``.msk`` via ``IntegratorExtended.read_mask``)."""
    return as_hw(IntegratorExtended.read_mask(path))


def require_mask_matches_frame(
    *,
    frame_path: str,
    mask_path: str,
    context: str = "",
) -> Tuple[int, int]:
    """
    Raise ``ValueError`` if mask shape != frame shape.

    Returns the shared ``(H, W)`` on success.
    """
    fh = frame_shape_hw(frame_path)
    mh = mask_shape_hw(mask_path)
    if fh != mh:
        prefix = f"{context}: " if context else ""
        raise ValueError(
            f"{prefix}mask shape {mh[0]}×{mh[1]} does not match image shape {fh[0]}×{fh[1]}"
        )
    return fh
