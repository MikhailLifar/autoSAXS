from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ....ui.preview_panel import ImageViewerDialog, open_image_viewer
from ...pipeline import LiveviewQueueStatus
from ....logic.path_display import contracted_path_label
from ..widgets.plots import (
    DatCurveViewerDialog,
    DropTiffImageCanvas,
    LogCurvePlot,
    open_compare_curves_dialog,
    open_dat_curve_dialog,
)


class LiveviewMiddlePanel(QWidget):
    tiff_files_dropped = pyqtSignal(object)  # list[str]
    history_step = pyqtSignal(int)  # -1 = older, +1 = newer
    process_history_file_requested = pyqtSignal()
    subtraction_wizard_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._current_image_path = ""
        self._current_curve_path = ""
        self._current_subtracted_path = ""
        self._compare_sample_path = ""
        self._compare_buffer_path = ""
        self._sub_subtract_opts: Dict[str, Any] = {}
        self._manual_preview_scale: Optional[float] = None

        self._nav_frame = QWidget()
        nav_lay = QHBoxLayout(self._nav_frame)
        nav_lay.setContentsMargins(0, 0, 0, 4)
        self._btn_hist_prev = QPushButton("<")
        self._btn_hist_prev.setFixedWidth(40)
        self._btn_hist_prev.setToolTip("Previous processed file (session)")
        self._btn_hist_prev.clicked.connect(lambda: self.history_step.emit(-1))
        self._btn_hist_next = QPushButton(">")
        self._btn_hist_next.setFixedWidth(40)
        self._btn_hist_next.setToolTip("Next processed file (session)")
        self._btn_hist_next.clicked.connect(lambda: self.history_step.emit(1))
        self._btn_process = QPushButton("Process")
        self._btn_process.setToolTip("Enqueue the selected file for the live pipeline (same as a new upload)")
        self._btn_process.clicked.connect(self.process_history_file_requested.emit)
        self._history_label = QLabel("")
        self._history_label.setWordWrap(True)
        self._history_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        nav_lay.addWidget(self._btn_hist_prev)
        nav_lay.addWidget(self._btn_hist_next)
        nav_lay.addWidget(self._btn_process)
        nav_lay.addWidget(self._history_label, 1)
        self._nav_frame.setVisible(False)
        self._btn_hist_prev.setEnabled(False)
        self._btn_hist_next.setEnabled(False)

        self._group_img = QGroupBox("2D")
        self._img = DropTiffImageCanvas()
        self._img.tiff_files_dropped.connect(self.tiff_files_dropped.emit)
        il = QVBoxLayout(self._group_img)
        il.addWidget(self._img)

        # States A / B / BD: single 1D curve (proxy or integrated q-space).
        self._group_main = QGroupBox("1D")
        self._main_plot = LogCurvePlot()
        gl = QVBoxLayout(self._group_main)
        gl.addWidget(self._main_plot)

        # States C / CD: two bottom plots per spec §4.4 (no single integrated plot).
        self._group_sub = QWidget()
        sub_outer = QVBoxLayout(self._group_sub)
        sub_outer.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        left_col = QVBoxLayout()
        left_col.addWidget(QLabel("S + buffer"))
        self._compare_plot = LogCurvePlot()
        left_col.addWidget(self._compare_plot, 1)
        right_col = QVBoxLayout()
        right_col.addWidget(QLabel("Sub"))
        self._subtracted_plot = LogCurvePlot()
        right_col.addWidget(self._subtracted_plot, 1)
        row.addLayout(left_col, 1)
        row.addLayout(right_col, 1)
        sub_outer.addLayout(row)
        self._group_sub.setVisible(False)

        self._status_frame = QFrame()
        self._status_frame.setFrameShape(QFrame.StyledPanel)
        self._status_line = QLabel("Idle")
        self._status_line.setWordWrap(True)
        self._current_line = QLabel("")
        self._current_line.setWordWrap(True)
        self._current_line.setStyleSheet("color: palette(mid);")
        self._queue_bar = QProgressBar()
        self._queue_bar.setTextVisible(False)
        self._queue_bar.setFixedHeight(8)
        self._queue_bar.setRange(0, 1)
        self._queue_bar.setValue(0)
        sf_lay = QVBoxLayout(self._status_frame)
        sf_lay.setContentsMargins(8, 6, 8, 6)
        sf_lay.addWidget(self._status_line)
        sf_lay.addWidget(self._current_line)
        sf_lay.addWidget(self._queue_bar)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._nav_frame, 0)
        lay.addWidget(self._group_img, 2)
        lay.addWidget(self._group_main, 1)
        lay.addWidget(self._group_sub, 1)
        lay.addWidget(self._status_frame)

        self._img.mpl_connect("button_press_event", self._on_mpl_click_open_2d)
        self._main_plot.mpl_connect("button_press_event", self._on_mpl_click_open_1d)
        self._compare_plot.mpl_connect("button_press_event", self._on_mpl_click_open_compare)
        self._subtracted_plot.mpl_connect("button_press_event", self._on_mpl_click_open_subtracted)

        # Raster (2D) vs interactive .dat curves (matplotlib toolbar).
        self._raster_preview_dialog: ImageViewerDialog | None = None
        self._curve_preview_dialog: DatCurveViewerDialog | None = None
        self._curve_x_label = "q (nm$^{-1}$)"

    def set_history_nav_visible(self, visible: bool) -> None:
        self._nav_frame.setVisible(bool(visible))

    def set_history_label(self, text: str) -> None:
        self._history_label.setText(text or "")

    def set_history_prev_enabled(self, enabled: bool) -> None:
        self._btn_hist_prev.setEnabled(bool(enabled))

    def set_history_next_enabled(self, enabled: bool) -> None:
        self._btn_hist_next.setEnabled(bool(enabled))

    def set_process_enabled(self, enabled: bool) -> None:
        self._btn_process.setEnabled(bool(enabled))

    def set_queue_status(self, status: LiveviewQueueStatus) -> None:
        rem = max(0, int(status.remaining))
        if rem == 0:
            self._status_line.setText("Idle")
            self._current_line.setText("")
            self._current_line.setToolTip("")
            self._queue_bar.setRange(0, 1)
            self._queue_bar.setValue(0)
            return
        self._status_line.setText(f"Queue · {rem}")
        cur = (status.current_path or "").strip()
        if cur:
            c_short, c_full = contracted_path_label(cur)
            self._current_line.setText(c_short)
            self._current_line.setToolTip(c_full)
        else:
            self._current_line.setText("")
            self._current_line.setToolTip("")
        self._queue_bar.setRange(0, 0)

    @staticmethod
    def _is_left_click_in_axes(ev: object) -> bool:
        if getattr(ev, "inaxes", None) is None:
            return False
        return int(getattr(ev, "button", 0)) == 1

    def _raster_viewer_dialog(self) -> ImageViewerDialog:
        if self._raster_preview_dialog is None:
            self._raster_preview_dialog = ImageViewerDialog(self)
        return self._raster_preview_dialog

    def _curve_viewer_dialog(self) -> DatCurveViewerDialog:
        if self._curve_preview_dialog is None:
            self._curve_preview_dialog = DatCurveViewerDialog(self)
        return self._curve_preview_dialog

    def _store_raster_viewer(self, dlg: ImageViewerDialog | None) -> None:
        if dlg is not None:
            self._raster_preview_dialog = dlg

    def _on_mpl_click_open_2d(self, ev: object) -> None:
        if not self._is_left_click_in_axes(ev):
            return
        self._open_2d_viewer()

    def _on_mpl_click_open_1d(self, ev: object) -> None:
        if not self._is_left_click_in_axes(ev):
            return
        self._open_1d_viewer()

    def _on_mpl_click_open_compare(self, ev: object) -> None:
        if not self._is_left_click_in_axes(ev):
            return
        self.subtraction_wizard_requested.emit()

    def _on_mpl_click_open_subtracted(self, ev: object) -> None:
        if not self._is_left_click_in_axes(ev):
            return
        self._open_subtracted_viewer()

    def _set_single_curve_mode(self, visible: bool) -> None:
        self._group_main.setVisible(visible)
        self._group_sub.setVisible(not visible)

    def show_curve(self, path: str, *, x_label: str = "q (nm$^{-1}$)") -> None:
        self._set_single_curve_mode(True)
        self._current_curve_path = path or ""
        self._curve_x_label = x_label
        self._main_plot.set_x_label(x_label)
        if not path:
            self._main_plot.clear()
            return
        self._main_plot.plot_dat(path)

    def show_subtraction_placeholder(self) -> None:
        """States C/CD: use two-panel bottom layout with no curves yet (hides single integrated plot)."""
        self._set_single_curve_mode(False)
        self._compare_sample_path = ""
        self._compare_buffer_path = ""
        self._current_subtracted_path = ""
        self._sub_subtract_opts = {}
        self._manual_preview_scale = None
        self._compare_plot.clear()
        self._subtracted_plot.clear()

    def show_subtraction_views(
        self,
        *,
        sample_dat: str,
        buffer_dat: str,
        subtracted_dat: str,
        subtract_options: Optional[Dict[str, Any]] = None,
    ) -> None:
        """State C / CD: two bottom plots; hide single integrated q plot."""
        self._set_single_curve_mode(False)
        self._compare_sample_path = sample_dat.strip()
        self._compare_buffer_path = buffer_dat.strip()
        self._current_subtracted_path = subtracted_dat.strip()
        self._sub_subtract_opts = dict(subtract_options or {})
        self._manual_preview_scale = None
        self._compare_plot.set_x_label("q (nm$^{-1}$)")
        self._subtracted_plot.set_x_label("q (nm$^{-1}$)")
        if self._compare_sample_path and self._compare_buffer_path:
            self._compare_plot.plot_sample_and_scaled_buffer(
                self._compare_sample_path,
                self._compare_buffer_path,
                subtracted_path=self._current_subtracted_path,
                subtract_options=self._sub_subtract_opts,
            )
        else:
            self._compare_plot.clear()
        if self._current_subtracted_path:
            self._subtracted_plot.plot_dat(self._current_subtracted_path, label="subtracted")
        else:
            self._subtracted_plot.clear()

    def current_subtraction_context(self) -> Dict[str, Any]:
        """Paths + subtract options for the currently displayed file (state C/CD)."""
        return {
            "sample_dat": self._compare_sample_path,
            "buffer_dat": self._compare_buffer_path,
            "subtracted_dat": self._current_subtracted_path,
            "subtract_options": dict(self._sub_subtract_opts or {}),
        }

    def preview_manual_subtraction_scale(self, scaling_factor: float) -> None:
        """
        Update compare + subtracted plots using a manual scaling factor (no file writes).
        """
        self._manual_preview_scale = float(scaling_factor)
        sp = (self._compare_sample_path or "").strip()
        bp = (self._compare_buffer_path or "").strip()
        if not sp or not bp:
            return
        self._compare_plot.plot_sample_and_scaled_buffer_manual(sp, bp, scaling_factor=self._manual_preview_scale)
        self._subtracted_plot.plot_subtracted_preview_manual(sp, bp, scaling_factor=self._manual_preview_scale)

    def show_image(self, path: str) -> None:
        self._current_image_path = path or ""
        if not path:
            self._img.clear()
            return
        self._img.show_tiff(path)

    def _open_2d_viewer(self) -> None:
        if not self._current_image_path:
            return
        self._store_raster_viewer(
            open_image_viewer(
                self,
                self._current_image_path,
                reuse=self._raster_viewer_dialog(),
                full_path_tooltip=self._current_image_path,
            )
        )

    def _open_1d_viewer(self) -> None:
        if not self._current_curve_path:
            return
        open_dat_curve_dialog(
            self,
            self._current_curve_path,
            reuse=self._curve_viewer_dialog(),
            x_label=self._curve_x_label,
        )

    def _open_compare_viewer(self) -> None:
        if not self._compare_sample_path or not self._compare_buffer_path:
            return
        open_compare_curves_dialog(
            self,
            self._compare_sample_path,
            self._compare_buffer_path,
            subtracted_path=self._current_subtracted_path,
            subtract_options=self._sub_subtract_opts,
            reuse=self._curve_viewer_dialog(),
        )

    def _open_subtracted_viewer(self) -> None:
        if not self._current_subtracted_path:
            return
        sub_short, _sub_full = contracted_path_label(self._current_subtracted_path)
        subtitled = f"Sub — {sub_short}"
        open_dat_curve_dialog(
            self,
            self._current_subtracted_path,
            reuse=self._curve_viewer_dialog(),
            x_label="q (nm$^{-1}$)",
            curve_label="subtracted",
            window_title=subtitled,
        )
