from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import numpy as np
from matplotlib.path import Path as MplPath
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QGuiApplication, QKeySequence
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..logic.calibration_storage import (
    calibration_subdir,
    ensure_path_in_calibration,
    ensure_tiff_in_calibration,
)
from ...logic.path_normalize import normalize_pathish
from ...logic.smart_defaults import (
    anchor_dir_from_resolved_path_list,
    browse_start_dir_for_resolved_paths,
    find_mask_near,
)
from ...ui.path_field import PathField
from .plots import DropTiffImageCanvas, Image2DPlot, mpl_navigation_toolbar


def _load_tiff_shape(path: str) -> Optional[tuple[int, int]]:
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
    a = np.asarray(arr)
    if a.ndim > 2:
        a = a.reshape((-1,) + a.shape[-2:])[0]
    try:
        return int(a.shape[0]), int(a.shape[1])
    except Exception:
        return None


def _read_mask_bool(path: str) -> Optional[np.ndarray]:
    """
    Load an existing mask as bool using the same loader as autosaxs expects.
    Returns None if path is empty/invalid.
    """
    p = (path or "").strip()
    if not p or not os.path.isfile(p):
        return None
    try:
        from autosaxs.core.integrator import IntegratorExtended

        m = IntegratorExtended.read_mask(p)
        return np.asarray(m, dtype=bool)
    except Exception:
        return None


def _polygon_to_mask(vertices_xy: list[tuple[float, float]], shape_hw: tuple[int, int]) -> np.ndarray:
    """
    Rasterize polygon interior to a boolean mask with True for masked pixels.
    vertices_xy are in image pixel coordinates (x=col, y=row) consistent with imshow origin='lower'.
    """
    H, W = int(shape_hw[0]), int(shape_hw[1])
    if H <= 0 or W <= 0 or len(vertices_xy) < 3:
        return np.zeros((max(H, 0), max(W, 0)), dtype=bool)

    xs = np.asarray([v[0] for v in vertices_xy], dtype=float)
    ys = np.asarray([v[1] for v in vertices_xy], dtype=float)
    if not (np.all(np.isfinite(xs)) and np.all(np.isfinite(ys))):
        return np.zeros((H, W), dtype=bool)

    # Tight bounding box for speed.
    x0 = int(max(0, np.floor(np.min(xs))))
    x1 = int(min(W - 1, np.ceil(np.max(xs))))
    y0 = int(max(0, np.floor(np.min(ys))))
    y1 = int(min(H - 1, np.ceil(np.max(ys))))
    if x1 < x0 or y1 < y0:
        return np.zeros((H, W), dtype=bool)

    # Use pixel centers (x+0.5, y+0.5) to avoid boundary/path degeneracies.
    xx, yy = np.meshgrid(
        np.arange(x0, x1 + 1, dtype=float) + 0.5,
        np.arange(y0, y1 + 1, dtype=float) + 0.5,
    )
    pts = np.column_stack([xx.ravel(), yy.ravel()])
    verts = np.asarray(vertices_xy, dtype=float)
    # Ensure the closing segment exists (fixes missing last triangle in some cases).
    if verts.shape[0] >= 1 and not np.allclose(verts[0], verts[-1]):
        verts = np.vstack([verts, verts[0]])
    path = MplPath(verts, closed=True)
    inside = path.contains_points(pts)
    out = np.zeros((H, W), dtype=bool)
    out[y0 : y1 + 1, x0 : x1 + 1] = inside.reshape((y1 - y0 + 1, x1 - x0 + 1))
    return out


def _rect_to_mask(
    x0: float, y0: float, x1: float, y1: float, shape_hw: tuple[int, int]
) -> np.ndarray:
    """Rasterize axis-aligned rectangle interior (masked=True)."""
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


class MaskMode(str, Enum):
    POLYGON = "polygon"
    PIXEL = "pixel"
    RECTANGULAR = "rectangular"


@dataclass
class MaskModel:
    image_shape_hw: Optional[tuple[int, int]] = None
    base_mask: Optional[np.ndarray] = None  # from existing file, bool
    mode: MaskMode = MaskMode.POLYGON
    polygons: list[list[tuple[float, float]]] = field(default_factory=list)  # completed
    current: list[tuple[float, float]] = field(default_factory=list)  # in-progress polygon
    rects: list[tuple[float, float, float, float]] = field(default_factory=list)  # x0,y0,x1,y1
    rect_first: Optional[tuple[float, float]] = None  # in-progress rectangle corner
    _pixel_toggles: list[tuple[int, int]] = field(default_factory=list)  # applied flip operations

    def clear(self, *, include_base: bool = False) -> None:
        self.polygons = []
        self.current = []
        self.rects = []
        self.rect_first = None
        self._pixel_toggles = []
        if include_base:
            self.base_mask = None

    def sync_context(
        self,
        shape_hw: Optional[tuple[int, int]],
        *,
        base_mask: Optional[np.ndarray],
        reset_edits: bool,
    ) -> None:
        if reset_edits:
            self.clear()
        self.image_shape_hw = shape_hw
        if base_mask is not None and shape_hw is not None:
            bm = np.asarray(base_mask, dtype=bool)
            if bm.shape == shape_hw:
                self.base_mask = bm
            else:
                self.base_mask = None
        elif reset_edits:
            self.base_mask = None

    def start_for_image(self, shape_hw: Optional[tuple[int, int]], *, base_mask: Optional[np.ndarray]) -> None:
        self.sync_context(shape_hw, base_mask=base_mask, reset_edits=True)

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

    def has_user_geometry(self) -> bool:
        if self.current or self.polygons or self.rects or self.rect_first is not None:
            return True
        if self._pixel_toggles:
            return True
        return False

    def undo_point(self) -> None:
        if self.mode == MaskMode.POLYGON:
            if self.current:
                self.current.pop()
                return
            if not self.polygons:
                return
            last = list(self.polygons.pop())
            if len(last) > 1:
                self.current = last[:-1]
            return
        if self.mode == MaskMode.RECTANGULAR:
            if self.rect_first is not None:
                self.rect_first = None
                return
            if not self.rects:
                return
            x0, y0, _x1, _y1 = self.rects.pop()
            self.rect_first = (x0, y0)

    def undo_shape(self) -> None:
        """Undo last polygon (polygon mode) or last rectangle (rectangular mode)."""
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

    def undo_pixel_edit(self) -> None:
        if self._pixel_toggles:
            self._pixel_toggles.pop()

    def undo(self) -> None:
        if self.mode == MaskMode.POLYGON or self.mode == MaskMode.RECTANGULAR:
            self.undo_point()
        elif self.mode == MaskMode.PIXEL:
            self.undo_pixel_edit()

    def add_point(self, x: float, y: float) -> None:
        if not (np.isfinite(x) and np.isfinite(y)):
            return
        self.current.append((float(x), float(y)))

    def finish_polygon(self) -> bool:
        if len(self.current) < 3:
            self.current = []
            return False
        self.polygons.append(list(self.current))
        self.current = []
        return True

    def _pixel_indices(self, x: float, y: float) -> Optional[tuple[int, int]]:
        if self.image_shape_hw is None:
            return None
        H, W = self.image_shape_hw
        # imshow default extent: pixel centers at integer coordinates.
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
        self.rects.append((x0, y0, float(x), float(y)))
        self.rect_first = None

    def mask_union(self) -> Optional[np.ndarray]:
        if self.image_shape_hw is None:
            return None
        H, W = self.image_shape_hw
        out = np.zeros((H, W), dtype=bool)
        if self.base_mask is not None:
            bm = np.asarray(self.base_mask, dtype=bool)
            if bm.shape == out.shape:
                out |= bm
        for poly in self.polygons:
            out |= _polygon_to_mask(poly, self.image_shape_hw)
        for rect in self.rects:
            out |= _rect_to_mask(*rect, self.image_shape_hw)
        for row, col in self._pixel_toggles:
            if 0 <= row < H and 0 <= col < W:
                out[row, col] = not bool(out[row, col])
        return out


class MaskCanvas(DropTiffImageCanvas):
    """
    Matplotlib TIFF canvas with mask drawing overlays (mode-dependent interaction).
    """

    edited = pyqtSignal()

    def __init__(self, *, model: MaskModel) -> None:
        super().__init__()
        self._model = model
        self._toolbar: Optional[NavigationToolbar2QT] = None
        self._mask_overlay = None
        self._poly_fill_artists = []
        self._poly_line_artist = None
        self._poly_pts_artist = None
        self._rect_artists = []
        self.mpl_connect("button_press_event", self._on_click)

    def set_toolbar(self, toolbar: Optional[NavigationToolbar2QT]) -> None:
        self._toolbar = toolbar

    def set_model(self, model: MaskModel) -> None:
        self._model = model
        self.refresh_overlays()

    def _is_left_click_in_axes(self, ev: object) -> bool:
        if getattr(ev, "inaxes", None) is None:
            return False
        return int(getattr(ev, "button", 0)) == 1

    def _on_click(self, ev: object) -> None:
        # If zoom/pan is active, don't treat clicks as drawing.
        if self._toolbar is not None and str(getattr(self._toolbar, "mode", "") or ""):
            return
        if not self._is_left_click_in_axes(ev):
            return
        x = getattr(ev, "xdata", None)
        y = getattr(ev, "ydata", None)
        if x is None or y is None:
            return
        try:
            x_f = float(x)
            y_f = float(y)
        except Exception:
            return
        mode = self._model.mode
        if mode == MaskMode.POLYGON:
            self._on_click_polygon(x_f, y_f, bool(getattr(ev, "dblclick", False)))
        elif mode == MaskMode.PIXEL:
            if bool(getattr(ev, "dblclick", False)):
                return
            self._model.toggle_pixel(x_f, y_f)
        elif mode == MaskMode.RECTANGULAR:
            if bool(getattr(ev, "dblclick", False)):
                return
            self._model.add_rect_click(x_f, y_f)
        self.edited.emit()
        self.refresh_overlays()

    def _on_click_polygon(self, x_f: float, y_f: float, dblclick: bool) -> None:
        if dblclick:
            if not self._model.current:
                self._model.add_point(x_f, y_f)
            else:
                lx, ly = self._model.current[-1]
                if abs(lx - x_f) > 1e-9 or abs(ly - y_f) > 1e-9:
                    self._model.add_point(x_f, y_f)
            self._model.finish_polygon()
        else:
            self._model.add_point(x_f, y_f)

    def _clear_overlay_artists(self) -> None:
        for a in list(self._poly_fill_artists):
            try:
                a.remove()
            except Exception:
                pass
        self._poly_fill_artists = []
        for a in (self._poly_line_artist, self._poly_pts_artist, self._mask_overlay):
            if a is None:
                continue
            try:
                a.remove()
            except Exception:
                pass
        self._poly_line_artist = None
        self._poly_pts_artist = None
        self._mask_overlay = None
        for a in list(self._rect_artists):
            try:
                a.remove()
            except Exception:
                pass
        self._rect_artists = []

    def refresh_overlays(self) -> None:
        ax = self._ax
        self._clear_overlay_artists()

        # Mask overlay from union (base + polygons)
        m = self._model.mask_union()
        if m is not None and m.size:
            try:
                rgba = np.zeros((m.shape[0], m.shape[1], 4), dtype=float)
                rgba[m, 0] = 1.0
                rgba[m, 3] = 0.40
                self._mask_overlay = ax.imshow(
                    rgba,
                    origin="lower",
                    aspect="equal",
                    interpolation="nearest",
                    zorder=10,
                )
            except Exception:
                self._mask_overlay = None

        # Completed polygon outlines
        for poly in self._model.polygons:
            if len(poly) < 3:
                continue
            xs = [p[0] for p in poly] + [poly[0][0]]
            ys = [p[1] for p in poly] + [poly[0][1]]
            try:
                (ln,) = ax.plot(xs, ys, color="white", linewidth=1.0, alpha=0.9)
                self._poly_fill_artists.append(ln)
            except Exception:
                pass

        # Completed rectangle outlines
        for x0, y0, x1, y1 in self._model.rects:
            xs = [x0, x1, x1, x0, x0]
            ys = [y0, y0, y1, y1, y0]
            try:
                (ln,) = ax.plot(xs, ys, color="white", linewidth=1.0, alpha=0.9, linestyle="--")
                self._rect_artists.append(ln)
            except Exception:
                pass

        # In-progress rectangle (first corner only)
        if self._model.rect_first is not None:
            x0, y0 = self._model.rect_first
            try:
                (pt,) = ax.plot([x0], [y0], marker="s", linestyle="None", color="white", markersize=6, alpha=0.95)
                self._rect_artists.append(pt)
            except Exception:
                pass

        # Current polygon (in-progress)
        cur = list(self._model.current)
        if cur:
            xs = [p[0] for p in cur]
            ys = [p[1] for p in cur]
            try:
                (pts,) = ax.plot(xs, ys, marker="o", linestyle="None", color="white", markersize=4, alpha=0.95)
                self._poly_pts_artist = pts
            except Exception:
                self._poly_pts_artist = None
            if len(cur) >= 2:
                try:
                    (ln,) = ax.plot(xs, ys, color="white", linewidth=1.0, alpha=0.9)
                    self._poly_line_artist = ln
                except Exception:
                    self._poly_line_artist = None

        self.draw_idle()


class MaskWizardDialog(QDialog):
    def __init__(
        self,
        *,
        watchdir: Path,
        default_image_path: str = "",
        default_mask_path: str = "",
        default_save_dir: Optional[Path] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._watchdir = watchdir
        self._saved_mask_path: str = ""
        self._dirty: bool = False
        self._ctx_shape: Optional[tuple[int, int]] = None
        self._ctx_mask_path: str = ""
        self._suppress_context_reload: bool = False
        self._applying_defaults: bool = False
        self._calib_sync_image_path: str = ""

        self.setWindowTitle("Create / edit mask")
        # Make it a top-level window so WMs show min/max controls.
        self.setWindowFlags(
            Qt.Window
            | Qt.CustomizeWindowHint
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowCloseButtonHint
            | Qt.WindowMinMaxButtonsHint
        )
        self.setSizeGripEnabled(True)
        # Default size: 75% width, 80% height of available screen.
        try:
            scr = QGuiApplication.primaryScreen()
            geo = scr.availableGeometry() if scr is not None else None
            if geo is not None:
                w = max(980, int(0.75 * int(geo.width())))
                h = max(720, int(0.90 * int(geo.height())))
                self.resize(w, h)
                self.setMinimumSize(860, 640)
        except Exception:
            self.resize(1280, 860)
            self.setMinimumWidth(980)

        self._model = MaskModel()
        self._canvas = MaskCanvas(model=self._model)
        self._toolbar = mpl_navigation_toolbar(self._canvas, self)
        self._canvas.set_toolbar(self._toolbar)
        self._canvas.tiff_files_dropped.connect(self._on_tiff_dropped_to_canvas)
        self._canvas.edited.connect(self._mark_dirty)

        self._image_field = PathField(mode="any", allow_multiple=False, expected_exts=(".tif", ".tiff"))
        self._image_field.set_workdir(watchdir)
        self._mask_field = PathField(
            mode="any",
            allow_multiple=False,
            show_load=True,
            expected_exts=(".txt", ".npy", ".msk"),
        )
        self._mask_field.set_workdir(watchdir)
        load_btn = self._mask_field.load_button
        if load_btn is not None:
            load_btn.setToolTip("Load the mask file at the path above (replaces the current mask overlay)")
        cal_dir = str(calibration_subdir(watchdir))
        self._image_field.set_browse_start_dir(cal_dir)
        self._mask_field.set_browse_start_dir(cal_dir)

        self._btn_undo_point = QPushButton("Undo last point")
        self._btn_undo_shape = QPushButton("Undo last polygon")
        self._btn_undo_pixel = QPushButton("Undo last edit")
        self._btn_clear = QPushButton("Clear all")
        self._btn_save = QPushButton("Save mask")

        self._mode_combo = QComboBox()
        self._mode_combo.addItem("Polygon", MaskMode.POLYGON.value)
        self._mode_combo.addItem("Pixel", MaskMode.PIXEL.value)
        self._mode_combo.addItem("Rectangular", MaskMode.RECTANGULAR.value)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        self._btn_undo_point.clicked.connect(self._on_undo_point)
        self._btn_undo_shape.clicked.connect(self._on_undo_shape)
        self._btn_undo_pixel.clicked.connect(self._on_undo_pixel)
        self._btn_clear.clicked.connect(self._on_clear)
        self._btn_save.clicked.connect(self._on_save)

        undo_shortcut = QShortcut(QKeySequence.Undo, self)
        undo_shortcut.activated.connect(self._on_undo)

        self._image_field.set_smart_drop_handler(self._smart_drop_paths)
        self._mask_field.set_smart_drop_handler(self._smart_drop_paths)
        self._image_field.path_changed.connect(self._on_image_field_changed)
        self._mask_field.path_changed.connect(self._on_mask_field_changed)
        self._mask_field.load_clicked.connect(self._on_load_mask_from_field)

        # Layout
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.addWidget(self._toolbar, 0)
        left_lay.addWidget(self._canvas, 1)
        splitter.addWidget(left)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)

        right_header = QWidget()
        right_header_lay = QHBoxLayout(right_header)
        right_header_lay.setContentsMargins(0, 0, 0, 0)
        right_header_lay.addStretch(1)
        self._btn_drawing_help = QPushButton("?")
        self._btn_drawing_help.setObjectName("helpButton")
        self._btn_drawing_help.setFixedSize(26, 26)
        self._btn_drawing_help.setToolTip("How to draw masks in the current mode")
        self._btn_drawing_help.clicked.connect(self._on_drawing_help)
        right_header_lay.addWidget(self._btn_drawing_help, 0, Qt.AlignTop | Qt.AlignRight)
        right_lay.addWidget(right_header, 0)

        grp_in = QGroupBox("Inputs")
        in_lay = QVBoxLayout(grp_in)
        in_lay.addWidget(QLabel("Calibration image (.tif):"))
        in_lay.addWidget(self._image_field)
        in_lay.addWidget(QLabel("Mask path (save destination; press Load to read a file):"))
        in_lay.addWidget(self._mask_field)
        right_lay.addWidget(grp_in)

        grp_tools = QGroupBox("Tools")
        tools_lay = QVBoxLayout(grp_tools)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        mode_row.addWidget(self._mode_combo, 1)
        tools_lay.addLayout(mode_row)
        tools_lay.addWidget(self._btn_undo_point)
        tools_lay.addWidget(self._btn_undo_shape)
        tools_lay.addWidget(self._btn_undo_pixel)
        tools_lay.addWidget(self._btn_clear)
        tools_lay.addStretch(1)
        tools_lay.addWidget(self._btn_save)
        right_lay.addWidget(grp_tools, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

        lay = QVBoxLayout(self)
        lay.addWidget(splitter, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        lay.addWidget(buttons, 0)

        # Defaults
        self.set_defaults(
            image_path=default_image_path,
            mask_path=default_mask_path,
            default_save_dir=default_save_dir,
        )
        self._update_mode_controls()

    def _current_mode(self) -> MaskMode:
        value = self._mode_combo.currentData()
        try:
            return MaskMode(str(value))
        except Exception:
            return MaskMode.POLYGON

    def _on_mode_changed(self, _index: int = 0) -> None:
        self._model.set_mode(self._current_mode())
        self._canvas.refresh_overlays()
        self._update_mode_controls()

    def _update_mode_controls(self) -> None:
        mode = self._current_mode()
        is_polygon = mode == MaskMode.POLYGON
        is_pixel = mode == MaskMode.PIXEL
        is_rect = mode == MaskMode.RECTANGULAR

        self._btn_undo_point.setVisible(is_polygon or is_rect)
        self._btn_undo_point.setText("Undo last point" if is_polygon else "Undo last corner")

        self._btn_undo_shape.setVisible(is_polygon or is_rect)
        self._btn_undo_shape.setText("Undo last polygon" if is_polygon else "Undo last rectangle")

        self._btn_undo_pixel.setVisible(is_pixel)

    def set_defaults(self, *, image_path: str, mask_path: str, default_save_dir: Optional[Path]) -> None:
        self._applying_defaults = True
        try:
            current_img = self._image_field.text().strip()
            if image_path:
                if not current_img or current_img == self._calib_sync_image_path:
                    self._image_field.set_text(image_path)
                self._calib_sync_image_path = image_path.strip()
            if mask_path:
                self._mask_field.set_text(mask_path)
            if default_save_dir is not None and not self._mask_field.text().strip():
                try:
                    default = calibration_subdir(self._watchdir) / "mask.txt"
                    self._mask_field.set_text(str(default))
                except Exception:
                    pass
            self._on_image_field_changed()
        finally:
            self._applying_defaults = False

    def _push_image_to_calibration_parent(self) -> None:
        parent = self.parent()
        if parent is None or not hasattr(parent, "_calib_image_field"):
            return
        calib_field = parent._calib_image_field()  # type: ignore[attr-defined]
        if calib_field is None:
            return
        img = self._image_field.text().strip()
        if not img or img == calib_field.text().strip():
            return
        calib_field.set_text(img)
        if hasattr(parent, "_refresh_viewer_from_form"):
            parent._refresh_viewer_from_form()  # type: ignore[attr-defined]

    def saved_mask_path(self) -> str:
        return (self._saved_mask_path or "").strip()

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _set_calibration_image_path(self, path: str) -> None:
        try:
            stored = ensure_tiff_in_calibration(self._watchdir, path)
        except (OSError, FileNotFoundError) as e:
            QMessageBox.warning(self, "Calibration image", str(e))
            return
        self._image_field.set_text(stored)
        self._on_image_field_changed()

    def _on_tiff_dropped_to_canvas(self, paths_obj: object) -> None:
        if not isinstance(paths_obj, list):
            return
        paths = [p for p in paths_obj if isinstance(p, str) and p.strip()]
        if not paths:
            return
        self._set_calibration_image_path(paths[0])

    @staticmethod
    def _drop_target_for_path(path: str) -> Optional[str]:
        ext = Path(path).suffix.lower()
        if ext in (".tif", ".tiff"):
            return "image"
        if ext in (".txt", ".npy", ".msk"):
            return "mask"
        return None

    def _smart_drop_paths(self, paths: list[str], source: PathField) -> bool:
        if not paths:
            return False
        if len(paths) != 1:
            return False
        raw = normalize_pathish(paths[0])
        if not raw:
            return False
        target = self._drop_target_for_path(raw)
        if target is None:
            return False
        if target == "image" and source is self._image_field:
            return False
        if target == "mask" and source is self._mask_field:
            return False
        if target == "image":
            self._set_calibration_image_path(raw)
        else:
            self._mask_field.set_text(raw)
        return True

    def _refresh_path_browse_starts(self) -> None:
        workdir = self._watchdir
        cal_dir = str(calibration_subdir(workdir))
        img_paths = [normalize_pathish(p) for p in self._image_field.paths() if normalize_pathish(p)]
        img_start = browse_start_dir_for_resolved_paths(img_paths, workdir) or cal_dir
        self._image_field.set_browse_start_dir(img_start)

        mask_paths = [normalize_pathish(p) for p in self._mask_field.paths() if normalize_pathish(p)]
        mask_start = browse_start_dir_for_resolved_paths(mask_paths, workdir)
        if mask_start is None:
            ad = anchor_dir_from_resolved_path_list(img_paths, workdir)
            mask_start = str(ad.resolve()) if ad is not None else cal_dir
        self._mask_field.set_browse_start_dir(mask_start)

    def _maybe_suggest_mask_path(self) -> None:
        if self._mask_field.text().strip():
            return
        img_paths = [normalize_pathish(p) for p in self._image_field.paths() if normalize_pathish(p)]
        ad = anchor_dir_from_resolved_path_list(img_paths, self._watchdir)
        if ad is None:
            return
        mpath = find_mask_near(ad)
        if mpath is not None:
            self._mask_field.set_text(str(mpath.resolve()))
            self._on_mask_field_changed()

    def _on_image_field_changed(self) -> None:
        self._reload_image_context()
        self._refresh_path_browse_starts()
        self._maybe_suggest_mask_path()
        if not self._applying_defaults:
            self._calib_sync_image_path = self._image_field.text().strip()
            self._push_image_to_calibration_parent()

    def _on_mask_field_changed(self) -> None:
        self._refresh_path_browse_starts()
        self._ctx_mask_path = self._mask_field.text().strip()

    def _on_load_mask_from_field(self) -> None:
        mask_path = self._mask_field.text().strip()
        if not mask_path:
            QMessageBox.warning(self, "Load mask", "Mask path is empty.")
            return
        if self._ctx_shape is None:
            QMessageBox.warning(self, "Load mask", "Load a calibration image first.")
            return
        p = Path(mask_path).expanduser()
        if not p.is_file():
            QMessageBox.warning(self, "Load mask", f"File not found:\n\n{mask_path}")
            return
        base = _read_mask_bool(str(p))
        if base is None:
            QMessageBox.warning(self, "Load mask", f"Could not read mask:\n\n{mask_path}")
            return
        if tuple(base.shape) != self._ctx_shape:
            QMessageBox.warning(
                self,
                "Load mask",
                f"Mask shape {base.shape} does not match image shape {self._ctx_shape}.",
            )
            return
        self._model.sync_context(self._ctx_shape, base_mask=base, reset_edits=True)
        self._ctx_mask_path = mask_path
        self._canvas.refresh_overlays()
        self._dirty = False

    def _reload_image_context(self) -> None:
        if self._suppress_context_reload:
            return
        img = self._image_field.text().strip()
        if img:
            try:
                self._canvas.show_tiff(img)
            except Exception:
                self._canvas.clear()
        else:
            self._canvas.clear()

        shape = self._canvas.last_image_shape() or _load_tiff_shape(img)
        reset_edits = shape != self._ctx_shape
        base = None if reset_edits else self._model.base_mask

        self._model.sync_context(shape, base_mask=base, reset_edits=reset_edits)
        self._ctx_shape = shape
        self._ctx_mask_path = self._mask_field.text().strip()
        self._canvas.refresh_overlays()
        if reset_edits:
            self._dirty = False

    def _on_drawing_help(self) -> None:
        mode = self._current_mode()
        if mode == MaskMode.PIXEL:
            text = (
                "• Click a pixel to mask or unmask it.\n"
                "• Ctrl+Z undoes the last pixel toggle.\n\n"
                "Masked pixels are shown in red."
            )
        elif mode == MaskMode.RECTANGULAR:
            text = (
                "• First click sets the top-left corner.\n"
                "• Second click sets the bottom-right corner.\n"
                "• Ctrl+Z undoes the last corner, or reopens the last rectangle.\n\n"
                "You can draw multiple rectangles; their interiors are combined."
            )
        else:
            text = (
                "• Click on the image to add a polygon point.\n"
                "• Ctrl+Z removes the last point.\n"
                "• Double-click to finish the current polygon.\n\n"
                "You can draw multiple polygons; their interiors are combined."
            )
        QMessageBox.information(self, "Drawing masks", text)

    def _on_undo(self) -> None:
        self._model.undo()
        self._canvas.refresh_overlays()
        self._mark_dirty()

    def _on_undo_point(self) -> None:
        self._model.undo_point()
        self._canvas.refresh_overlays()
        self._mark_dirty()

    def _on_undo_shape(self) -> None:
        self._model.undo_shape()
        self._canvas.refresh_overlays()
        self._mark_dirty()

    def _on_undo_pixel(self) -> None:
        self._model.undo_pixel_edit()
        self._canvas.refresh_overlays()
        self._mark_dirty()

    def _on_clear(self) -> None:
        self._model.clear(include_base=True)
        self._canvas.refresh_overlays()
        self._mark_dirty()

    def _on_save(self) -> None:
        img = self._image_field.text().strip()
        if not img or not os.path.isfile(img):
            QMessageBox.warning(self, "Mask", "Calibration image path is missing or not a file.")
            return

        dest = self._mask_field.text().strip()
        if not dest:
            QMessageBox.warning(self, "Mask", "Mask path is empty. Choose where to save the mask.")
            return
        try:
            dp = ensure_path_in_calibration(self._watchdir, dest, default_name="mask.txt")
        except Exception as e:
            QMessageBox.warning(self, "Mask", str(e))
            return
        if dp.suffix.lower() != ".txt":
            QMessageBox.warning(self, "Mask", "For now, please save masks as NumPy .txt.")
            return

        m = self._model.mask_for_save()
        if m is None:
            QMessageBox.warning(self, "Mask", "No image is loaded; cannot compute mask.")
            return

        # Overwrite confirmation
        if dp.exists():
            resp = QMessageBox.question(
                self,
                "Overwrite mask?",
                f"Overwrite existing file?\n\n{str(dp)}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return
        try:
            dp.parent.mkdir(parents=True, exist_ok=True)
            np.savetxt(str(dp), m.astype(int), fmt="%d")
        except Exception as e:
            QMessageBox.critical(self, "Mask", f"Failed to save mask:\n\n{e}")
            return

        self._saved_mask_path = str(dp)
        self._suppress_context_reload = True
        try:
            try:
                stored = ensure_tiff_in_calibration(self._watchdir, img)
                self._image_field.set_text(stored)
            except (OSError, FileNotFoundError) as e:
                QMessageBox.warning(self, "Mask", str(e))
                return
            self._mask_field.set_text(self._saved_mask_path)
        finally:
            self._suppress_context_reload = False

        saved_base = _read_mask_bool(self._saved_mask_path)
        self._model.sync_context(self._ctx_shape, base_mask=saved_base, reset_edits=True)
        self._ctx_mask_path = self._saved_mask_path
        self._canvas.refresh_overlays()
        self._dirty = False
        QMessageBox.information(self, "Mask", f"Saved mask:\n\n{self._saved_mask_path}")

    def reject(self) -> None:  # type: ignore[override]
        if self._confirm_close_if_dirty():
            super().reject()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._confirm_close_if_dirty():
            event.accept()
        else:
            event.ignore()

    def _confirm_close_if_dirty(self) -> bool:
        if not (self._dirty and self._model.has_user_geometry()):
            return True
        resp = QMessageBox.question(
            self,
            "Unsaved mask edits",
            "You have unsaved mask edits. Close without saving?",
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        return resp == QMessageBox.Ok

