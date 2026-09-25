from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from autosaxs.core.integrator import IntegratorExtended
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSplitter,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)
from PyQt5.QtGui import QKeySequence

from ....services.calibration.storage import calibration_subdir, ensure_tiff_in_calibration
from .....logic.path_normalize import normalize_pathish
from .....logic.smart_defaults import (
    anchor_dir_from_resolved_path_list,
    browse_start_dir_for_resolved_paths,
    find_mask_near,
)
from .....ui.path_field import PathField
from .....ui.toast import Toast
from ...widgets.plots import mpl_navigation_toolbar
from .canvas import MaskCanvas
from .histogram import IntensityHistogramPanel
from .model import (
    MaskMode,
    MaskModel,
    PaintPolarity,
    default_log1p_band,
    load_tiff_array,
    load_tiff_shape,
    read_mask_bool,
)

_MASK_SAVE_FILTERS = (
    "NumPy text mask (*.txt)",
    "NumPy binary mask (*.npy)",
    "Fit2D mask (*.msk)",
)
_MASK_SAVE_EXTS = (".txt", ".npy", ".msk")
_FILTER_TO_EXT = {
    "NumPy text mask (*.txt)": ".txt",
    "NumPy binary mask (*.npy)": ".npy",
    "Fit2D mask (*.msk)": ".msk",
}


class MaskWizardDialog(QDialog):
    attention_context_changed = pyqtSignal()
    mask_committed = pyqtSignal(str)

    def __init__(
        self,
        *,
        watchdir: Path,
        default_image_path: str = "",
        default_mask_path: str = "",
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
        self.setWindowFlags(
            Qt.Window
            | Qt.CustomizeWindowHint
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowCloseButtonHint
            | Qt.WindowMinMaxButtonsHint
        )
        self.setSizeGripEnabled(True)
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

        self._histogram = IntensityHistogramPanel()
        self._histogram.range_changed.connect(self._on_threshold_preview)
        self._histogram.range_committed.connect(self._on_threshold_committed)

        self._image_field = PathField(mode="any", allow_multiple=False, expected_exts=(".tif", ".tiff"))
        self._image_field.set_workdir(watchdir)
        self._mask_field = PathField(
            mode="any",
            allow_multiple=False,
            expected_exts=(".txt", ".npy", ".msk"),
        )
        self._mask_field.set_workdir(watchdir)
        cal_dir = str(calibration_subdir(watchdir))
        self._image_field.set_browse_start_dir(cal_dir)
        self._mask_field.set_browse_start_dir(cal_dir)

        self._btn_undo_point = QPushButton("Undo last point")
        self._btn_undo_shape = QPushButton("Undo last polygon")
        self._btn_undo_pixel = QPushButton("Undo last edit")
        self._btn_clear = QPushButton("Clear all")
        self._btn_save = QPushButton("Save mask")
        self._btn_apply_threshold = QPushButton("Apply threshold")
        self._btn_reset_threshold = QPushButton("Reset range")

        self._mode_combo = QComboBox()
        self._mode_combo.addItem("Polygon", MaskMode.POLYGON.value)
        self._mode_combo.addItem("Pixel", MaskMode.PIXEL.value)
        self._mode_combo.addItem("Rectangular", MaskMode.RECTANGULAR.value)
        self._mode_combo.addItem("Threshold", MaskMode.THRESHOLD.value)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        self._btn_polarity_mask = QToolButton()
        self._btn_polarity_mask.setText("Mask")
        self._btn_polarity_mask.setCheckable(True)
        self._btn_polarity_mask.setChecked(True)
        self._btn_polarity_mask.setToolTip("Paint regions that should be masked")
        self._btn_polarity_unmask = QToolButton()
        self._btn_polarity_unmask.setText("Unmask")
        self._btn_polarity_unmask.setCheckable(True)
        self._btn_polarity_unmask.setToolTip("Clear masking inside drawn regions")
        self._polarity_group = QButtonGroup(self)
        self._polarity_group.setExclusive(True)
        self._polarity_group.addButton(self._btn_polarity_mask)
        self._polarity_group.addButton(self._btn_polarity_unmask)
        self._btn_polarity_mask.clicked.connect(lambda: self._set_polarity(PaintPolarity.MASK))
        self._btn_polarity_unmask.clicked.connect(lambda: self._set_polarity(PaintPolarity.UNMASK))
        self._polarity_wrap = QWidget()
        pol_lay = QHBoxLayout(self._polarity_wrap)
        pol_lay.setContentsMargins(0, 0, 0, 0)
        pol_lay.setSpacing(0)
        pol_lay.addWidget(self._btn_polarity_mask)
        pol_lay.addWidget(self._btn_polarity_unmask)
        self._polarity_wrap.setStyleSheet(
            "QToolButton { padding: 4px 12px; border: 1px solid #4a5560; background: #2a323a; color: #dce3ea; }"
            "QToolButton:checked { background: #5ec8a0; color: #102018; border-color: #5ec8a0; font-weight: 600; }"
            "QToolButton#unmaskBtn:checked { background: #5b8def; border-color: #5b8def; color: #0e1624; }"
        )
        self._btn_polarity_unmask.setObjectName("unmaskBtn")

        self._btn_undo_point.clicked.connect(self._on_undo_point)
        self._btn_undo_shape.clicked.connect(self._on_undo_shape)
        self._btn_undo_pixel.clicked.connect(self._on_undo_pixel)
        self._btn_clear.clicked.connect(self._on_clear)
        self._btn_save.clicked.connect(self._on_save)
        self._btn_apply_threshold.clicked.connect(self._on_apply_threshold)
        self._btn_reset_threshold.clicked.connect(self._on_reset_threshold)

        undo_shortcut = QShortcut(QKeySequence.Undo, self)
        undo_shortcut.activated.connect(self._on_undo)

        self._image_field.set_smart_drop_handler(self._smart_drop_paths)
        self._mask_field.set_smart_drop_handler(self._smart_drop_paths)
        self._image_field.path_changed.connect(self._on_image_field_changed)
        self._mask_field.path_changed.connect(self._on_mask_field_changed)
        self._image_field.path_changed.connect(self.attention_context_changed.emit)
        self._mask_field.path_changed.connect(self.attention_context_changed.emit)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.addWidget(self._toolbar, 0)
        img_hist = QSplitter(Qt.Vertical)
        img_hist.setChildrenCollapsible(False)
        img_hist.addWidget(self._canvas)
        img_hist.addWidget(self._histogram)
        img_hist.setStretchFactor(0, 7)
        img_hist.setStretchFactor(1, 3)
        left_lay.addWidget(img_hist, 1)
        splitter.addWidget(left)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)

        grp_in = QGroupBox("Inputs")
        in_lay = QVBoxLayout(grp_in)
        in_lay.addWidget(QLabel("Calibration image (.tif):"))
        in_lay.addWidget(self._image_field)
        in_lay.addWidget(QLabel("Mask path:"))
        in_lay.addWidget(self._mask_field)
        right_lay.addWidget(grp_in)

        grp_tools = QGroupBox("Tools")
        tools_lay = QVBoxLayout(grp_tools)
        tools_header = QHBoxLayout()
        tools_header.addStretch(1)
        self._btn_drawing_help = QPushButton("?")
        self._btn_drawing_help.setObjectName("helpButton")
        self._btn_drawing_help.setFixedSize(26, 26)
        self._btn_drawing_help.setToolTip("How to draw masks in the current mode")
        self._btn_drawing_help.clicked.connect(self._on_drawing_help)
        tools_header.addWidget(self._btn_drawing_help, 0, Qt.AlignTop | Qt.AlignRight)
        tools_lay.addLayout(tools_header)
        note = QLabel(
            "Note: you should not mask the beam-stop.\n"
            "Calibration writes it into effective_mask.npy automatically."
        )
        note.setWordWrap(True)
        tools_lay.addWidget(note)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        mode_row.addWidget(self._mode_combo, 1)
        mode_row.addWidget(self._polarity_wrap, 0)
        tools_lay.addLayout(mode_row)
        tools_lay.addWidget(self._btn_undo_point)
        tools_lay.addWidget(self._btn_undo_shape)
        tools_lay.addWidget(self._btn_undo_pixel)
        tools_lay.addWidget(self._btn_apply_threshold)
        tools_lay.addWidget(self._btn_reset_threshold)
        tools_lay.addWidget(self._btn_clear)
        tools_lay.addStretch(1)
        tools_lay.addWidget(self._btn_save)
        right_lay.addWidget(grp_tools, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)

        lay = QVBoxLayout(self)
        lay.addWidget(splitter, 1)

        self.set_defaults(image_path=default_image_path, mask_path=default_mask_path)
        self._update_mode_controls()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.attention_context_changed.emit()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        super().hideEvent(event)
        path = (self._mask_field.text().strip() or self._saved_mask_path or "").strip()
        if path:
            self.mask_committed.emit(path)
        self.attention_context_changed.emit()

    def has_calibrant_image(self) -> bool:
        return bool(self._image_field.text().strip())

    def has_mask(self) -> bool:
        return bool(self._mask_field.text().strip() or self._saved_mask_path)

    def mask_browse_button(self):
        return self._mask_field.browse_button

    def image_browse_button(self):
        return self._image_field.browse_button

    def _current_mode(self) -> MaskMode:
        value = self._mode_combo.currentData()
        try:
            return MaskMode(str(value))
        except Exception:
            return MaskMode.POLYGON

    def _set_polarity(self, polarity: PaintPolarity) -> None:
        self._model.set_paint_polarity(polarity)
        self._canvas.refresh_overlays()

    def _on_mode_changed(self, _index: int = 0) -> None:
        self._model.set_mode(self._current_mode())
        self._canvas.set_threshold_preview(None)
        if self._current_mode() == MaskMode.THRESHOLD and self._model.threshold_active:
            lo = self._model.threshold_lo
            hi = self._model.threshold_hi
            if lo is not None and hi is not None:
                self._histogram.set_range(lo, hi, emit=False)
        self._canvas.refresh_overlays()
        self._update_mode_controls()

    def _update_mode_controls(self) -> None:
        mode = self._current_mode()
        is_polygon = mode == MaskMode.POLYGON
        is_pixel = mode == MaskMode.PIXEL
        is_rect = mode == MaskMode.RECTANGULAR
        is_thr = mode == MaskMode.THRESHOLD

        self._polarity_wrap.setVisible(is_polygon or is_rect)
        self._btn_undo_point.setVisible(is_polygon or is_rect)
        self._btn_undo_point.setText("Undo last point" if is_polygon else "Undo last corner")
        self._btn_undo_shape.setVisible(is_polygon or is_rect or is_thr)
        if is_thr:
            self._btn_undo_shape.setText("Clear threshold")
        else:
            self._btn_undo_shape.setText("Undo last polygon" if is_polygon else "Undo last rectangle")
        self._btn_undo_pixel.setVisible(is_pixel)
        self._btn_apply_threshold.setVisible(is_thr)
        self._btn_reset_threshold.setVisible(is_thr)
        # Histogram / threshold interaction only in Threshold mode.
        self._histogram.setVisible(is_thr)
        self._histogram.setEnabled(is_thr)

    def _ensure_default_threshold_applied(self) -> None:
        """Apply default keep-band [0, max] so raw I<0 are masked as soon as an image is loaded."""
        intensity = self._model.intensity
        if intensity is None or self._model.image_shape_hw is None:
            return
        lo, hi = default_log1p_band(intensity)
        self._model.apply_threshold_band(lo, hi)
        self._histogram.set_range(lo, hi, emit=False)
        self._canvas.set_threshold_preview(None)
        self._canvas.refresh_overlays()

    def set_defaults(self, *, image_path: str, mask_path: str) -> None:
        self._applying_defaults = True
        try:
            current_img = self._image_field.text().strip()
            if image_path:
                if not current_img or current_img == self._calib_sync_image_path:
                    self._image_field.set_text(image_path)
                self._calib_sync_image_path = image_path.strip()
            if mask_path:
                self._mask_field.set_text(mask_path)
            self._on_image_field_changed()
        finally:
            self._applying_defaults = False

    def _push_image_to_calibration_parent(self) -> None:
        parent = self.parent()
        # Prefer explicit callback if parent is left panel hosting shared wizard.
        if parent is not None and hasattr(parent, "on_mask_wizard_image_changed"):
            img = self._image_field.text().strip()
            if img:
                parent.on_mask_wizard_image_changed(img)  # type: ignore[attr-defined]
            return
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
        if not paths or len(paths) != 1:
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
        self._try_load_mask_from_field()

    def _try_load_mask_from_field(self) -> None:
        mask_path = self._mask_field.text().strip()
        self._ctx_mask_path = mask_path
        if not mask_path:
            self._model.base_mask = None
            if self._ctx_shape is not None:
                self._ensure_default_threshold_applied()
                self._canvas.refresh_overlays()
            return
        p = Path(mask_path).expanduser()
        if not p.is_file():
            return
        base = read_mask_bool(str(p))
        if base is None:
            return
        mask_shape = (int(base.shape[0]), int(base.shape[1]))
        img = self._image_field.text().strip()
        # Mask without TIFF: show blank (cmap-min) frame sized to the mask.
        if not img or not os.path.isfile(img):
            if self._ctx_shape != mask_shape:
                self._dirty = False
            self._canvas.show_blank_frame(mask_shape)
            self._model.sync_context(
                mask_shape,
                base_mask=base,
                reset_edits=True,
                intensity=None,
            )
            self._ctx_shape = mask_shape
            self._histogram.set_intensity(None)
            self._canvas.set_threshold_preview(None)
            self._canvas.refresh_overlays()
            self._dirty = False
            self._update_mode_controls()
            return
        if self._ctx_shape is None:
            return
        if tuple(base.shape) != self._ctx_shape:
            Toast(
                text=(
                    f"Mask shape {int(base.shape[0])}×{int(base.shape[1])} does not match "
                    f"image shape {self._ctx_shape[0]}×{self._ctx_shape[1]}"
                ),
                parent=self,
            ).show_near_bottom()
            self._mask_field.set_text("")
            self._ctx_mask_path = ""
            self._model.base_mask = None
            if self._ctx_shape is not None:
                self._ensure_default_threshold_applied()
                self._canvas.refresh_overlays()
            return
        self._model.sync_context(
            self._ctx_shape,
            base_mask=base,
            reset_edits=True,
            intensity=self._model.intensity,
        )
        self._ensure_default_threshold_applied()
        self._canvas.refresh_overlays()
        self._dirty = False

    def _reload_image_context(self) -> None:
        if self._suppress_context_reload:
            return
        img = self._image_field.text().strip()
        intensity = None
        if img and os.path.isfile(img):
            try:
                self._canvas.show_tiff(img)
            except Exception:
                self._canvas.clear()
            intensity = load_tiff_array(img)
            shape = self._canvas.last_image_shape() or load_tiff_shape(img)
            if intensity is not None and shape is not None and intensity.shape != shape:
                intensity = None
            reset_edits = shape != self._ctx_shape
            base = None if reset_edits else self._model.base_mask
            self._model.sync_context(shape, base_mask=base, reset_edits=reset_edits, intensity=intensity)
            self._ctx_shape = shape
            self._histogram.set_intensity(intensity)
            self._canvas.set_threshold_preview(None)
            if reset_edits:
                self._dirty = False
            self._try_load_mask_from_field()
            if not self._model.threshold_active:
                self._ensure_default_threshold_applied()
            else:
                self._canvas.refresh_overlays()
            self._update_mode_controls()
            return

        # No TIFF: if a mask file is present, draw it on a blank frame; else clear.
        mask_path = self._mask_field.text().strip()
        if mask_path and os.path.isfile(mask_path):
            self._try_load_mask_from_field()
            return

        self._canvas.clear()
        self._model.sync_context(None, base_mask=None, reset_edits=True, intensity=None)
        self._ctx_shape = None
        self._histogram.set_intensity(None)
        self._canvas.set_threshold_preview(None)
        self._dirty = False
        self._update_mode_controls()
    def _on_threshold_preview(self, lo: float, hi: float) -> None:
        if self._current_mode() != MaskMode.THRESHOLD:
            self._canvas.set_threshold_preview(None)
            return
        preview = self._model.threshold_preview_mask(lo, hi)
        self._canvas.set_threshold_preview(preview)

    def _on_threshold_committed(self, lo: float, hi: float) -> None:
        if self._current_mode() != MaskMode.THRESHOLD:
            return
        # Live preview only until Apply; still refresh preview on release.
        self._on_threshold_preview(lo, hi)

    def _on_apply_threshold(self) -> None:
        self._model.apply_threshold_band(self._histogram.lo(), self._histogram.hi())
        self._canvas.set_threshold_preview(None)
        self._canvas.refresh_overlays()
        self._mark_dirty()

    def _on_reset_threshold(self) -> None:
        self._histogram.reset_to_default()
        self._model.apply_threshold_band(self._histogram.lo(), self._histogram.hi())
        self._canvas.set_threshold_preview(None)
        self._canvas.refresh_overlays()
        self._mark_dirty()

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
                "• Use Mask / Unmask to choose whether the rectangle adds or clears masking.\n"
                "• First click sets one corner; second click finishes the rectangle.\n"
                "• Ctrl+Z undoes the last corner, or reopens the last rectangle.\n\n"
                "Unmask strokes are shown dashed in blue."
            )
        elif mode == MaskMode.THRESHOLD:
            text = (
                "• Drag the handles on the log(1+I) histogram to choose the keep band.\n"
                "• Pixels outside the band are masked (raw I < 0 are always outside).\n"
                "• Double-click the histogram to reset to [0, max].\n"
                "• Click Apply threshold to commit the band into the mask."
            )
        else:
            text = (
                "• Use Mask / Unmask to choose whether the polygon adds or clears masking.\n"
                "• Click on the image to add a polygon point.\n"
                "• Ctrl+Z removes the last point.\n"
                "• Double-click to finish the current polygon.\n\n"
                "Unmask strokes are shown dashed in blue."
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
        self._canvas.set_threshold_preview(None)
        self._canvas.refresh_overlays()
        self._mark_dirty()

    def _default_save_mask_path(self) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return (self._watchdir / f"mask-{ts}.txt").resolve()

    def _browse_save_mask_path(self) -> Optional[Path]:
        start = str(self._default_save_mask_path())
        dlg = QFileDialog(self, "Save mask", start)
        dlg.setAcceptMode(QFileDialog.AcceptSave)
        dlg.setOption(QFileDialog.DontUseNativeDialog, True)
        dlg.setViewMode(QFileDialog.Detail)
        dlg.setMinimumSize(980, 720)
        dlg.resize(1100, 760)
        dlg.setNameFilters(list(_MASK_SAVE_FILTERS))
        dlg.selectNameFilter(_MASK_SAVE_FILTERS[0])
        dlg.selectFile(Path(start).name)
        view = dlg.findChild(QTreeView)
        if view is not None and view.header() is not None:
            view.header().setStretchLastSection(False)
            view.header().setSectionResizeMode(view.header().Interactive)
            view.header().resizeSection(0, 520)
            view.header().resizeSection(1, 70)
            view.header().resizeSection(2, 120)
            view.header().resizeSection(3, 140)
        if not dlg.exec_():
            return None
        selected = dlg.selectedFiles()
        if not selected:
            return None
        raw = (selected[0] or "").strip()
        if not raw:
            return None
        dp = Path(raw).expanduser()
        suf = dp.suffix.lower()
        if suf not in _MASK_SAVE_EXTS:
            ext = _FILTER_TO_EXT.get(dlg.selectedNameFilter(), ".txt")
            dp = dp.with_suffix(ext)
        return dp.resolve()

    def _on_save(self) -> None:
        dp = self._browse_save_mask_path()
        if dp is None:
            return

        m = self._model.mask_for_save()
        if m is None:
            QMessageBox.warning(self, "Mask", "No mask to save (load an image or draw a mask first).")
            return

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
            IntegratorExtended.write_mask(str(dp), m)
        except Exception as e:
            QMessageBox.critical(self, "Mask", f"Failed to save mask:\n\n{e}")
            return

        self._saved_mask_path = str(dp)
        self._suppress_context_reload = True
        try:
            img = self._image_field.text().strip()
            if img and os.path.isfile(img):
                try:
                    stored = ensure_tiff_in_calibration(self._watchdir, img)
                    self._image_field.set_text(stored)
                except (OSError, FileNotFoundError) as e:
                    QMessageBox.warning(self, "Mask", str(e))
                    return
            self._mask_field.set_text(self._saved_mask_path)
        finally:
            self._suppress_context_reload = False

        saved_base = read_mask_bool(self._saved_mask_path)
        self._model.sync_context(
            self._ctx_shape,
            base_mask=saved_base,
            reset_edits=True,
            intensity=self._model.intensity,
        )
        self._ctx_mask_path = self._saved_mask_path
        self._canvas.set_threshold_preview(None)
        self._canvas.refresh_overlays()
        self._dirty = False
        QMessageBox.information(self, "Mask", f"Saved mask:\n\n{self._saved_mask_path}")
        self.mask_committed.emit(self._saved_mask_path)
        self.attention_context_changed.emit()

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
