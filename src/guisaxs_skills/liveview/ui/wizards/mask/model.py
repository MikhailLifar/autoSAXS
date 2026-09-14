from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import numpy as np
from matplotlib.path import Path as MplPath


def load_tiff_shape(path: str) -> Optional[tuple[int, int]]:
    p = (path or "").strip()
    if not p:
        return None
    import os

    if not os.path.isfile(p):
        return None
    arr = None
    try:
        import fabio

        arr = fabio.open(p).data
    except Exception:
        arr = None
    if arr is None:
        try:
            import tifffile

            arr = tifffile.imread(p)
        except Exception:
            arr = None
    if arr is None:
        return None
    a = np.asarray(arr)
    if a.ndim > 2:
        a = a.reshape((-1,) + a.shape[-2:])[0]
    try:
        return int(a.shape[0]), int(a.shape[1])
    except Exception:
        return None


def read_mask_bool(path: str) -> Optional[np.ndarray]:
    """Load an existing mask as bool using the same loader as autosaxs expects."""
    import os

    p = (path or "").strip()
    if not p or not os.path.isfile(p):
        return None
    try:
        from autosaxs.core.integrator import IntegratorExtended

        m = IntegratorExtended.read_mask(p)
        return np.asarray(m, dtype=bool)
    except Exception:
        return None


def load_tiff_array(path: str) -> Optional[np.ndarray]:
    """Load TIFF as float2d (first frame if stack)."""
    import os

    p = (path or "").strip()
    if not p or not os.path.isfile(p):
        return None
    arr = None
    try:
        import fabio

        arr = fabio.open(p).data
    except Exception:
        arr = None
    if arr is None:
        try:
            import tifffile

            arr = tifffile.imread(p)
        except Exception:
            arr = None
    if arr is None:
        return None
    a = np.asarray(arr, dtype=float)
    if a.ndim > 2:
        a = a.reshape((-1,) + a.shape[-2:])[0]
    return a


def polygon_to_mask(vertices_xy: list[tuple[float, float]], shape_hw: tuple[int, int]) -> np.ndarray:
    """Rasterize polygon interior (True = masked). vertices in imshow origin='lower' pixel coords."""
    H, W = int(shape_hw[0]), int(shape_hw[1])
    if H <= 0 or W <= 0 or len(vertices_xy) < 3:
        return np.zeros((max(H, 0), max(W, 0)), dtype=bool)

    xs = np.asarray([v[0] for v in vertices_xy], dtype=float)
    ys = np.asarray([v[1] for v in vertices_xy], dtype=float)
    if not (np.all(np.isfinite(xs)) and np.all(np.isfinite(ys))):
        return np.zeros((H, W), dtype=bool)

    x0 = int(max(0, np.floor(np.min(xs))))
    x1 = int(min(W - 1, np.ceil(np.max(xs))))
    y0 = int(max(0, np.floor(np.min(ys))))
    y1 = int(min(H - 1, np.ceil(np.max(ys))))
    if x1 < x0 or y1 < y0:
        return np.zeros((H, W), dtype=bool)

    xx, yy = np.meshgrid(
        np.arange(x0, x1 + 1, dtype=float) + 0.5,
        np.arange(y0, y1 + 1, dtype=float) + 0.5,
    )
    pts = np.column_stack([xx.ravel(), yy.ravel()])
    verts = np.asarray(vertices_xy, dtype=float)
    if verts.shape[0] >= 1 and not np.allclose(verts[0], verts[-1]):
        verts = np.vstack([verts, verts[0]])
    path = MplPath(verts, closed=True)
    inside = path.contains_points(pts)
    out = np.zeros((H, W), dtype=bool)
    out[y0 : y1 + 1, x0 : x1 + 1] = inside.reshape((y1 - y0 + 1, x1 - x0 + 1))
    return out


def rect_to_mask(
    x0: float, y0: float, x1: float, y1: float, shape_hw: tuple[int, int]
) -> np.ndarray:
    H, W = int(shape_hw[0]), int(shape_hw[1])
    if H <= 0 or W <= 0:
        return np.zeros((max(H, 0), max(W, 0)), dtype=bool)
    xs = sorted((float(x0), float(x1)))
    ys = sorted((float(y0), float(y1)))
    col0 = int(max(0, np.floor(xs[0])))
    col1 = int(min(W - 1, np.ceil(xs[1])))
    row0 = int(max(0, np.floor(ys[0])))
    row1 = int(min(H - 1, np.ceil(ys[1])))
    out = np.zeros((H, W), dtype=bool)
    if col1 < col0 or row1 < row0:
        return out
    out[row0 : row1 + 1, col0 : col1 + 1] = True
    return out


def log1p_keep_band_mask(
    intensity: np.ndarray,
    lo: float,
    hi: float,
) -> np.ndarray:
    """
    Mask pixels outside [lo, hi] in log1p space.

    Raw I < 0 never enter the keep band (log1p is only meaningful for I >= 0 here).
    No intensity shifting.
    """
    data = np.asarray(intensity, dtype=float)
    out = np.ones(data.shape, dtype=bool)
    finite = np.isfinite(data)
    nonneg = finite & (data >= 0.0)
    logv = np.full(data.shape, np.nan, dtype=float)
    logv[nonneg] = np.log1p(data[nonneg])
    keep = nonneg & (logv >= float(lo)) & (logv <= float(hi))
    out[keep] = False
    return out


def default_log1p_band(intensity: np.ndarray) -> tuple[float, float]:
    """Default keep band [0, max(log1p(I))] over finite non-negative pixels."""
    data = np.asarray(intensity, dtype=float)
    nonneg = np.isfinite(data) & (data >= 0.0)
    if not np.any(nonneg):
        return 0.0, 0.0
    hi = float(np.max(np.log1p(data[nonneg])))
    if not np.isfinite(hi) or hi < 0.0:
        hi = 0.0
    return 0.0, hi


class MaskMode(str, Enum):
    POLYGON = "polygon"
    PIXEL = "pixel"
    RECTANGULAR = "rectangular"
    THRESHOLD = "threshold"


class PaintPolarity(str, Enum):
    MASK = "mask"
    UNMASK = "unmask"


@dataclass
class MaskModel:
    image_shape_hw: Optional[tuple[int, int]] = None
    base_mask: Optional[np.ndarray] = None
    mode: MaskMode = MaskMode.POLYGON
    paint_polarity: PaintPolarity = PaintPolarity.MASK
    # completed polygons: (vertices, polarity)
    polygons: list[tuple[list[tuple[float, float]], PaintPolarity]] = field(default_factory=list)
    current: list[tuple[float, float]] = field(default_factory=list)
    # completed rects: (x0,y0,x1,y1, polarity)
    rects: list[tuple[float, float, float, float, PaintPolarity]] = field(default_factory=list)
    rect_first: Optional[tuple[float, float]] = None
    _pixel_toggles: list[tuple[int, int]] = field(default_factory=list)
    # Threshold band in log1p space; None = inactive
    threshold_lo: Optional[float] = None
    threshold_hi: Optional[float] = None
    threshold_active: bool = False
    # Image intensity for threshold (same shape as image); not persisted
    intensity: Optional[np.ndarray] = None

    def clear(self, *, include_base: bool = False) -> None:
        self.polygons = []
        self.current = []
        self.rects = []
        self.rect_first = None
        self._pixel_toggles = []
        self.threshold_active = False
        self.threshold_lo = None
        self.threshold_hi = None
        if include_base:
            self.base_mask = None

    def sync_context(
        self,
        shape_hw: Optional[tuple[int, int]],
        *,
        base_mask: Optional[np.ndarray],
        reset_edits: bool,
        intensity: Optional[np.ndarray] = None,
    ) -> None:
        if reset_edits:
            self.clear()
        self.image_shape_hw = shape_hw
        if intensity is not None and shape_hw is not None:
            arr = np.asarray(intensity, dtype=float)
            if arr.shape == shape_hw:
                self.intensity = arr
            else:
                self.intensity = None
        elif reset_edits:
            self.intensity = None
        if base_mask is not None and shape_hw is not None:
            bm = np.asarray(base_mask, dtype=bool)
            if bm.shape == shape_hw:
                self.base_mask = bm
            else:
                self.base_mask = None
        elif reset_edits:
            self.base_mask = None

    def start_for_image(
        self,
        shape_hw: Optional[tuple[int, int]],
        *,
        base_mask: Optional[np.ndarray],
        intensity: Optional[np.ndarray] = None,
    ) -> None:
        self.sync_context(shape_hw, base_mask=base_mask, reset_edits=True, intensity=intensity)

    def commit_in_progress(self) -> None:
        if self.mode == MaskMode.POLYGON:
            if len(self.current) >= 3:
                self.finish_polygon()
            else:
                self.current = []
            return
        if self.mode == MaskMode.RECTANGULAR:
            self.rect_first = None

    def mask_for_save(self) -> Optional[np.ndarray]:
        self.commit_in_progress()
        return self.mask_union()

    def set_mode(self, mode: MaskMode) -> None:
        self.mode = mode
        self.current = []
        self.rect_first = None

    def set_paint_polarity(self, polarity: PaintPolarity) -> None:
        self.paint_polarity = polarity

    def apply_threshold_band(self, lo: float, hi: float) -> None:
        self.threshold_lo = float(lo)
        self.threshold_hi = float(hi)
        self.threshold_active = True

    def clear_threshold(self) -> None:
        self.threshold_active = False
        self.threshold_lo = None
        self.threshold_hi = None

    def has_user_geometry(self) -> bool:
        if self.current or self.polygons or self.rects or self.rect_first is not None:
            return True
        if self._pixel_toggles:
            return True
        if self.threshold_active:
            return True
        return False

    def undo_point(self) -> None:
        if self.mode == MaskMode.POLYGON:
            if self.current:
                self.current.pop()
                return
            if not self.polygons:
                return
            last_verts, _pol = self.polygons.pop()
            last = list(last_verts)
            if len(last) > 1:
                self.current = last[:-1]
            return
        if self.mode == MaskMode.RECTANGULAR:
            if self.rect_first is not None:
                self.rect_first = None
                return
            if not self.rects:
                return
            x0, y0, _x1, _y1, _pol = self.rects.pop()
            self.rect_first = (x0, y0)

    def undo_shape(self) -> None:
        if self.mode == MaskMode.POLYGON:
            if self.current:
                self.current = []
                return
            if self.polygons:
                self.polygons.pop()
            return
        if self.mode == MaskMode.RECTANGULAR:
            if self.rect_first is not None:
                self.rect_first = None
                return
            if self.rects:
                self.rects.pop()
        if self.mode == MaskMode.THRESHOLD:
            self.clear_threshold()

    def undo_pixel_edit(self) -> None:
        if self._pixel_toggles:
            self._pixel_toggles.pop()

    def undo(self) -> None:
        if self.mode in (MaskMode.POLYGON, MaskMode.RECTANGULAR):
            self.undo_point()
        elif self.mode == MaskMode.PIXEL:
            self.undo_pixel_edit()
        elif self.mode == MaskMode.THRESHOLD:
            self.clear_threshold()

    def add_point(self, x: float, y: float) -> None:
        if not (np.isfinite(x) and np.isfinite(y)):
            return
        self.current.append((float(x), float(y)))

    def finish_polygon(self) -> bool:
        if len(self.current) < 3:
            self.current = []
            return False
        self.polygons.append((list(self.current), self.paint_polarity))
        self.current = []
        return True

    def _pixel_indices(self, x: float, y: float) -> Optional[tuple[int, int]]:
        if self.image_shape_hw is None:
            return None
        H, W = self.image_shape_hw
        col = int(np.clip(np.round(float(x)), 0, W - 1))
        row = int(np.clip(np.round(float(y)), 0, H - 1))
        return row, col

    def toggle_pixel(self, x: float, y: float) -> None:
        idx = self._pixel_indices(x, y)
        if idx is None:
            return
        self._pixel_toggles.append(idx)

    def add_rect_click(self, x: float, y: float) -> None:
        if not (np.isfinite(x) and np.isfinite(y)):
            return
        pt = (float(x), float(y))
        if self.rect_first is None:
            self.rect_first = pt
            return
        x0, y0 = self.rect_first
        self.rects.append((x0, y0, float(x), float(y), self.paint_polarity))
        self.rect_first = None

    def threshold_preview_mask(self, lo: float, hi: float) -> Optional[np.ndarray]:
        if self.intensity is None or self.image_shape_hw is None:
            return None
        if self.intensity.shape != self.image_shape_hw:
            return None
        return log1p_keep_band_mask(self.intensity, lo, hi)

    def mask_union(self) -> Optional[np.ndarray]:
        if self.image_shape_hw is None:
            return None
        H, W = self.image_shape_hw
        out = np.zeros((H, W), dtype=bool)
        if self.base_mask is not None:
            bm = np.asarray(self.base_mask, dtype=bool)
            if bm.shape == out.shape:
                out |= bm
        if (
            self.threshold_active
            and self.threshold_lo is not None
            and self.threshold_hi is not None
            and self.intensity is not None
            and self.intensity.shape == out.shape
        ):
            out |= log1p_keep_band_mask(self.intensity, self.threshold_lo, self.threshold_hi)
        for verts, pol in self.polygons:
            region = polygon_to_mask(verts, self.image_shape_hw)
            if pol == PaintPolarity.MASK:
                out |= region
            else:
                out &= ~region
        for x0, y0, x1, y1, pol in self.rects:
            region = rect_to_mask(x0, y0, x1, y1, self.image_shape_hw)
            if pol == PaintPolarity.MASK:
                out |= region
            else:
                out &= ~region
        for row, col in self._pixel_toggles:
            if 0 <= row < H and 0 <= col < W:
                out[row, col] = not bool(out[row, col])
        return out
