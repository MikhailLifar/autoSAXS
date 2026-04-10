from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ...ui.preview_panel import open_image_viewer
from ..pipeline import LiveviewQueueStatus
from .plots import DropTiffImageCanvas, LogCurvePlot, mpl_navigation_toolbar


class LiveviewMiddlePanel(QWidget):
    tiff_files_dropped = pyqtSignal(object)  # list[str]

    def __init__(self) -> None:
        super().__init__()
        self._current_image_path = ""
        self._current_curve_path = ""
        self._current_subtracted_path = ""
        self._compare_sample_path = ""
        self._compare_buffer_path = ""
        self._sub_subtract_opts: Dict[str, Any] = {}

        self._group_img = QGroupBox("Latest image (2D) — drop .tif here")
        self._img = DropTiffImageCanvas()
        self._img.tiff_files_dropped.connect(self.tiff_files_dropped.emit)
        il = QVBoxLayout(self._group_img)
        il.addWidget(self._img)

        # States A / B / BD: single 1D curve (proxy or integrated q-space).
        self._group_main = QGroupBox("Latest curve")
        self._main_plot = LogCurvePlot()
        gl = QVBoxLayout(self._group_main)
        gl.addWidget(self._main_plot)

        # States C / CD: two bottom plots per spec §4.4 (no single integrated plot).
        self._group_sub = QWidget()
        sub_outer = QVBoxLayout(self._group_sub)
        sub_outer.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        left_col = QVBoxLayout()
        left_col.addWidget(QLabel("Sample + scaled buffer (log I vs q)"))
        self._compare_plot = LogCurvePlot()
        left_col.addWidget(self._compare_plot, 1)
        right_col = QVBoxLayout()
        right_col.addWidget(QLabel("Subtracted"))
        self._subtracted_plot = LogCurvePlot()
        right_col.addWidget(self._subtracted_plot, 1)
        row.addLayout(left_col, 1)
        row.addLayout(right_col, 1)
        sub_outer.addLayout(row)
        self._group_sub.setVisible(False)

        self._status_frame = QFrame()
        self._status_frame.setFrameShape(QFrame.StyledPanel)
        self._status_line = QLabel("Idle — no images in queue")
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
        lay.addWidget(self._group_img, 2)
        lay.addWidget(self._group_main, 1)
        lay.addWidget(self._group_sub, 1)
        lay.addWidget(self._status_frame)

        self._img.mpl_connect("button_press_event", lambda ev: self._open_2d_viewer())
        self._main_plot.mpl_connect("button_press_event", lambda ev: self._open_1d_viewer())
        self._compare_plot.mpl_connect("button_press_event", lambda ev: self._open_compare_viewer())
        self._subtracted_plot.mpl_connect("button_press_event", lambda ev: self._open_subtracted_viewer())

        self._viewer_1d: QDialog | None = None
        self._viewer_compare: QDialog | None = None
        self._viewer_subtracted: QDialog | None = None
        self._viewer_panel_1d: LogCurvePlot | None = None
        self._viewer_panel_compare: LogCurvePlot | None = None
        self._viewer_panel_subtracted: LogCurvePlot | None = None
        self._curve_x_label = "q (nm$^{-1}$)"

    def set_queue_status(self, status: LiveviewQueueStatus) -> None:
        rem = max(0, int(status.remaining))
        if rem == 0:
            self._status_line.setText("Idle — no images in queue")
            self._current_line.setText("")
            self._queue_bar.setRange(0, 1)
            self._queue_bar.setValue(0)
            return
        word = "image" if rem == 1 else "images"
        self._status_line.setText(f"{rem} {word} remaining to process")
        cur = (status.current_path or "").strip()
        if cur:
            self._current_line.setText(f"Now: {Path(cur).name}")
        else:
            self._current_line.setText("")
        self._queue_bar.setRange(0, 0)

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
        self._compare_plot.set_x_label("q (nm$^{-1}$)")
        self._subtracted_plot.set_x_label("q (nm$^{-1}$)")
        if self._compare_sample_path and self._compare_buffer_path:
            self._compare_plot.plot_sample_and_scaled_buffer(
                self._compare_sample_path,
                self._compare_buffer_path,
                subtract_options=self._sub_subtract_opts,
            )
        else:
            self._compare_plot.clear()
        if self._current_subtracted_path:
            self._subtracted_plot.plot_dat(self._current_subtracted_path, label="subtracted")
        else:
            self._subtracted_plot.clear()

    def show_image(self, path: str) -> None:
        self._current_image_path = path or ""
        if not path:
            self._img.clear()
            return
        self._img.show_tiff(path)

    def _open_2d_viewer(self) -> None:
        if not self._current_image_path:
            return
        open_image_viewer(self, self._current_image_path)

    def _open_1d_viewer(self) -> None:
        if not self._current_curve_path:
            return
        if self._viewer_1d is None:
            dlg = QDialog(self)
            dlg.setWindowTitle("1D viewer")
            dlg.resize(1100, 800)
            lay = QVBoxLayout(dlg)
            panel = LogCurvePlot()
            lay.addWidget(mpl_navigation_toolbar(panel, dlg))
            lay.addWidget(panel, 1)
            self._viewer_1d = dlg
            self._viewer_panel_1d = panel
        assert self._viewer_panel_1d is not None and self._viewer_1d is not None
        plot = self._viewer_panel_1d
        try:
            plot.set_x_label(self._curve_x_label)  # type: ignore[attr-defined]
            plot.plot_dat(self._current_curve_path)  # type: ignore[attr-defined]
        except Exception:
            pass
        self._viewer_1d.show()
        self._viewer_1d.raise_()
        self._viewer_1d.activateWindow()

    def _open_compare_viewer(self) -> None:
        if not self._compare_sample_path or not self._compare_buffer_path:
            return
        if self._viewer_compare is None:
            dlg = QDialog(self)
            dlg.setWindowTitle("Sample + scaled buffer")
            dlg.resize(1100, 800)
            lay = QVBoxLayout(dlg)
            panel = LogCurvePlot()
            lay.addWidget(mpl_navigation_toolbar(panel, dlg))
            lay.addWidget(panel, 1)
            self._viewer_compare = dlg
            self._viewer_panel_compare = panel
        assert self._viewer_panel_compare is not None and self._viewer_compare is not None
        p = self._viewer_panel_compare
        try:
            p.set_x_label("q (nm$^{-1}$)")  # type: ignore[attr-defined]
            p.plot_sample_and_scaled_buffer(  # type: ignore[attr-defined]
                self._compare_sample_path,
                self._compare_buffer_path,
                subtract_options=self._sub_subtract_opts,
            )
        except Exception:
            pass
        self._viewer_compare.show()
        self._viewer_compare.raise_()
        self._viewer_compare.activateWindow()

    def _open_subtracted_viewer(self) -> None:
        if not self._current_subtracted_path:
            return
        if self._viewer_subtracted is None:
            dlg = QDialog(self)
            dlg.setWindowTitle("Subtracted curve")
            dlg.resize(1100, 800)
            lay = QVBoxLayout(dlg)
            panel = LogCurvePlot()
            lay.addWidget(mpl_navigation_toolbar(panel, dlg))
            lay.addWidget(panel, 1)
            self._viewer_subtracted = dlg
            self._viewer_panel_subtracted = panel
        assert self._viewer_panel_subtracted is not None and self._viewer_subtracted is not None
        plot = self._viewer_panel_subtracted
        try:
            plot.set_x_label("q (nm$^{-1}$)")  # type: ignore[attr-defined]
            plot.plot_dat(self._current_subtracted_path)  # type: ignore[attr-defined]
        except Exception:
            pass
        self._viewer_subtracted.show()
        self._viewer_subtracted.raise_()
        self._viewer_subtracted.activateWindow()
