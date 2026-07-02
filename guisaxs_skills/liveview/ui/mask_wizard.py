from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from matplotlib.path import Path as MplPath
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
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


@dataclass
class MaskModel:
    image_shape_hw: Optional[tuple[int, int]] = None
    base_mask: Optional[np.ndarray] = None  # from existing file, bool
    polygons: list[list[tuple[float, float]]] = field(default_factory=list)  # completed
    current: list[tuple[float, float]] = field(default_factory=list)  # in-progress

    def clear(self) -> None:
        self.polygons = []
        self.current = []

    def start_for_image(self, shape_hw: Optional[tuple[int, int]], *, base_mask: Optional[np.ndarray]) -> None:
        self.image_shape_hw = shape_hw
        self.base_mask = np.asarray(base_mask, dtype=bool) if base_mask is not None else None
        self.polygons = []
        self.current = []

    def undo_point(self) -> None:
        if self.current:
            self.current.pop()

    def undo_polygon(self) -> None:
        # Spec: remove current polygon if unfinished; else remove last finished polygon
        if self.current:
            self.current = []
            return
        if self.polygons:
            self.polygons.pop()

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
        return out


class MaskCanvas(DropTiffImageCanvas):
    """
    Matplotlib TIFF canvas with polygon drawing overlays (double-click to finish polygon).
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
        self.mpl_connect("button_press_event", self._on_click)
        # Ensure drag-and-drop hint doesn't overwrite the axes after an image is shown.

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
        if bool(getattr(ev, "dblclick", False)):
            # Finish polygon on double-click (spec). Include the click location as the last point
            # unless it is effectively identical to the previously added point.
            if not self._model.current:
                self._model.add_point(x_f, y_f)
            else:
                lx, ly = self._model.current[-1]
                if abs(lx - x_f) > 1e-9 or abs(ly - y_f) > 1e-9:
                    self._model.add_point(x_f, y_f)
            self._model.finish_polygon()
        else:
            self._model.add_point(x_f, y_f)
        self.edited.emit()
        self.refresh_overlays()

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

        # Completed polygon fills (outline only; mask overlay already shows union)
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
                h = max(720, int(0.80 * int(geo.height())))
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
        self._mask_field = PathField(mode="any", allow_multiple=False, expected_exts=(".txt", ".npy", ".msk"))
        self._mask_field.set_workdir(watchdir)

        self._btn_undo_point = QPushButton("Undo last point")
        self._btn_undo_poly = QPushButton("Undo last polygon")
        self._btn_clear = QPushButton("Clear all")
        self._btn_save = QPushButton("Save mask")

        self._btn_undo_point.clicked.connect(self._on_undo_point)
        self._btn_undo_poly.clicked.connect(self._on_undo_polygon)
        self._btn_clear.clicked.connect(self._on_clear)
        self._btn_save.clicked.connect(self._on_save)

        self._image_field.path_changed.connect(self._on_inputs_changed)
        self._mask_field.path_changed.connect(self._on_inputs_changed)

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

        grp_in = QGroupBox("Inputs")
        in_lay = QVBoxLayout(grp_in)
        in_lay.addWidget(QLabel("Calibration image (.tif):"))
        in_lay.addWidget(self._image_field)
        in_lay.addWidget(QLabel("Mask path (load/edit/save):"))
        in_lay.addWidget(self._mask_field)
        right_lay.addWidget(grp_in)

        grp_tools = QGroupBox("Tools")
        tools_lay = QVBoxLayout(grp_tools)
        tools_lay.addWidget(QLabel("Click to add points. Double-click to finish polygon."))
        tools_lay.addWidget(self._btn_undo_point)
        tools_lay.addWidget(self._btn_undo_poly)
        tools_lay.addWidget(self._btn_clear)
        tools_lay.addStretch(1)
        tools_lay.addWidget(self._btn_save)
        right_lay.addWidget(grp_tools, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

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

    def set_defaults(self, *, image_path: str, mask_path: str, default_save_dir: Optional[Path]) -> None:
        if image_path:
            self._image_field.set_text(image_path)
        if mask_path:
            self._mask_field.set_text(mask_path)
        if default_save_dir is not None and not self._mask_field.text().strip():
            try:
                default = (default_save_dir / "mask.txt").resolve()
                self._mask_field.set_text(str(default))
            except Exception:
                pass
        self._on_inputs_changed()

    def saved_mask_path(self) -> str:
        return (self._saved_mask_path or "").strip()

    def _mark_dirty(self) -> None:
        self._dirty = True

    def _on_tiff_dropped_to_canvas(self, paths_obj: object) -> None:
        if not isinstance(paths_obj, list):
            return
        paths = [p for p in paths_obj if isinstance(p, str) and p.strip()]
        if not paths:
            return
        self._image_field.set_text(paths[0])
        self._on_inputs_changed()

    def _on_inputs_changed(self) -> None:
        img = self._image_field.text().strip()
        if img:
            try:
                self._canvas.show_tiff(img)
            except Exception:
                self._canvas.clear()
        else:
            self._canvas.clear()

        shape = self._canvas.last_image_shape() or _load_tiff_shape(img)
        base = _read_mask_bool(self._mask_field.text().strip())
        # If base exists but shape not known yet, keep it but avoid union mismatches.
        self._model.start_for_image(shape, base_mask=base)
        self._canvas.refresh_overlays()
        # Changing inputs resets edit session; treat as not-dirty until the user draws.
        self._dirty = False

    def _on_undo_point(self) -> None:
        self._model.undo_point()
        self._canvas.refresh_overlays()
        self._mark_dirty()

    def _on_undo_polygon(self) -> None:
        self._model.undo_polygon()
        self._canvas.refresh_overlays()
        self._mark_dirty()

    def _on_clear(self) -> None:
        self._model.clear()
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
        dp = Path(dest).expanduser()
        dp = dp if dp.is_absolute() else (self._watchdir / dp)
        dp = dp.resolve()
        if dp.suffix.lower() != ".txt":
            QMessageBox.warning(self, "Mask", "For now, please save masks as NumPy .txt.")
            return

        m = self._model.mask_union()
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
        # Unsaved work = polygons or in-progress points, or any action since last save.
        has_geom = bool(self._model.polygons or self._model.current)
        if not (self._dirty and has_geom):
            return True
        resp = QMessageBox.question(
            self,
            "Unsaved mask edits",
            "You have unsaved mask edits. Close without saving?",
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        return resp == QMessageBox.Ok

