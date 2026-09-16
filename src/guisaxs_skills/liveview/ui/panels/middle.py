from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PyQt5.QtCore import Qt, pyqtSignal
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

from ...pipeline import LiveviewQueueStatus
from ...session.state import LiveviewIntakeMode
from ....logic.path_display import contracted_path_label
from ..widgets.plots import (
    DatCurveViewerDialog,
    DropTiffImageCanvas,
    Image2DViewerDialog,
    LogCurvePlot,
    open_compare_curves_dialog,
    open_dat_curve_dialog,
    open_image_2d_dialog,
)


class LiveviewMiddlePanel(QWidget):
    tiff_files_dropped = pyqtSignal(object)  # list[str] — TIFF and/or .dat paths
    history_step = pyqtSignal(int)  # -1 = older, +1 = newer
    process_history_file_requested = pyqtSignal()
    subtraction_wizard_requested = pyqtSignal()
    image_presence_changed = pyqtSignal(bool)  # True when a 2D TIFF is shown

    def __init__(self) -> None:
        super().__init__()
        self._intake_mode = LiveviewIntakeMode.FRAME_2D
        self._current_image_path = ""
        self._current_curve_path = ""
        self._current_subtracted_path = ""
        self._compare_sample_path = ""
        self._compare_buffer_path = ""
        self._sub_subtract_opts: Dict[str, Any] = {}
        self._manual_preview_scale: Optional[float] = None
        self.setAcceptDrops(True)

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
        self._img_host = QWidget()
        self._img_host.setAttribute(Qt.WA_StyledBackground, True)
        img_host_lay = QVBoxLayout(self._img_host)
        img_host_lay.setContentsMargins(6, 6, 6, 6)
        img_host_lay.addWidget(self._img, 1)
        il = QVBoxLayout(self._group_img)
        il.addWidget(self._img_host)

        # States A / B / BD / curve intake: primary 1D (or Sub) curve.
        self._group_main = QGroupBox("1D")
        self._main_plot = LogCurvePlot()
        self._main_plot.files_dropped.connect(self.tiff_files_dropped.emit)
        gl = QVBoxLayout(self._group_main)
        gl.addWidget(self._main_plot)

        # Dual compare / subtracted (2D state C/CD; 1D when buffer is set).
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

        # Interactive 2D TIFF / 1D .dat (matplotlib NavigationToolbar).
        self._image_2d_preview_dialog: Image2DViewerDialog | None = None
        self._curve_preview_dialog: DatCurveViewerDialog | None = None
        self._curve_x_label = "q (nm$^{-1}$)"
        self._buffer_ready = False
        self._middle_sync_sig: tuple | None = None

    def apply_intake_layout(
        self,
        mode: LiveviewIntakeMode,
        *,
        buffer_ready: bool = False,
    ) -> bool:
        """Sole owner of middle widget visibility. Returns True if layout changed."""
        buffer_ready = bool(buffer_ready)
        if mode == LiveviewIntakeMode.FRAME_2D:
            want_img, want_main, want_sub, title = (
                True,
                not buffer_ready,
                buffer_ready,
                "1D",
            )
            drop_hint = False
        elif mode == LiveviewIntakeMode.CURVE_SUB:
            want_img, want_main, want_sub, title = False, True, False, "Sub"
            drop_hint = True
        else:
            # CURVE_1D
            want_img, want_main, want_sub, title = False, True, buffer_ready, "1D"
            drop_hint = True

        changed = (
            self._intake_mode != mode
            or self._buffer_ready != buffer_ready
            or self._group_img.isVisible() != want_img
            or self._group_main.isVisible() != want_main
            or self._group_sub.isVisible() != want_sub
            or self._group_main.title() != title
        )
        self._intake_mode = mode
        self._buffer_ready = buffer_ready
        if not changed:
            return False

        self._group_img.setVisible(want_img)
        self._main_plot.set_drop_hint_enabled(drop_hint)
        self._group_main.setTitle(title)
        self._group_main.setVisible(want_main)
        self._group_sub.setVisible(want_sub)
        self._middle_sync_sig = None  # layout change invalidates paint skip
        return True
    def curve_drop_host(self) -> QWidget:
        return self._group_main

    def drop_canvas_host(self) -> QWidget:
        return self._img_host

    def drop_hint_canvas(self):
        if self._intake_mode == LiveviewIntakeMode.FRAME_2D:
            return self._img
        return self._main_plot

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        # Panel-level drops for curve modes (2D canvas also accepts drops).
        from ..widgets.plots import collect_saxs_drop_paths

        if collect_saxs_drop_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        from ..widgets.plots import collect_saxs_drop_paths

        paths = collect_saxs_drop_paths(event.mimeData())
        if paths:
            self.tiff_files_dropped.emit(paths)
        event.setDropAction(Qt.CopyAction)
        event.accept()

    def has_image(self) -> bool:
        return bool((self._current_image_path or "").strip())

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

    def _image_2d_viewer_dialog(self) -> Image2DViewerDialog:
        if self._image_2d_preview_dialog is None:
            self._image_2d_preview_dialog = Image2DViewerDialog(self)
        return self._image_2d_preview_dialog

    def _curve_viewer_dialog(self) -> DatCurveViewerDialog:
        if self._curve_preview_dialog is None:
            self._curve_preview_dialog = DatCurveViewerDialog(self)
        return self._curve_preview_dialog

    def _store_image_2d_viewer(self, dlg: Image2DViewerDialog | None) -> None:
        if dlg is not None:
            self._image_2d_preview_dialog = dlg

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

    def show_curve(self, path: str, *, x_label: str = "q (nm$^{-1}$)") -> None:
        """Paint main curve canvas only (visibility owned by ``apply_intake_layout``)."""
        path = path or ""
        if path == self._current_curve_path and x_label == self._curve_x_label:
            return
        self._current_curve_path = path
        if self._intake_mode == LiveviewIntakeMode.CURVE_SUB:
            self._current_subtracted_path = path
        self._curve_x_label = x_label
        self._main_plot.set_x_label(x_label)
        if not path:
            self._main_plot.clear()
            return
        self._main_plot.plot_dat(path)

    def clear_dual_plots(self) -> None:
        self._compare_sample_path = ""
        self._compare_buffer_path = ""
        self._current_subtracted_path = ""
        self._sub_subtract_opts = {}
        self._manual_preview_scale = None
        self._compare_plot.clear()
        self._subtracted_plot.clear()

    def show_subtraction_placeholder(self) -> None:
        """Empty dual plots; layout unchanged."""
        self.clear_dual_plots()
        if self._intake_mode == LiveviewIntakeMode.CURVE_SUB and not self._current_curve_path:
            self._main_plot.clear()

    def show_subtraction_views(
        self,
        *,
        sample_dat: str,
        buffer_dat: str,
        subtracted_dat: str,
        subtract_options: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Paint dual (and Sub-intake main) only — no visibility changes."""
        sample_dat = sample_dat.strip()
        buffer_dat = buffer_dat.strip()
        subtracted_dat = subtracted_dat.strip()
        opts = dict(subtract_options or {})
        same = (
            sample_dat == self._compare_sample_path
            and buffer_dat == self._compare_buffer_path
            and subtracted_dat == self._current_subtracted_path
            and opts == self._sub_subtract_opts
            and self._manual_preview_scale is None
        )
        if same:
            return

        self._compare_sample_path = sample_dat
        self._compare_buffer_path = buffer_dat
        self._current_subtracted_path = subtracted_dat
        self._sub_subtract_opts = opts
        self._manual_preview_scale = None

        if self._intake_mode == LiveviewIntakeMode.CURVE_SUB:
            self._current_curve_path = subtracted_dat
            self._curve_x_label = "q (nm$^{-1}$)"
            self._main_plot.set_x_label("q (nm$^{-1}$)")
            if subtracted_dat:
                self._main_plot.plot_dat(subtracted_dat, label="subtracted")
            else:
                self._main_plot.clear()
            return

        if self._intake_mode == LiveviewIntakeMode.CURVE_1D and sample_dat:
            self._current_curve_path = sample_dat
            self._curve_x_label = "q (nm$^{-1}$)"
            self._main_plot.set_x_label("q (nm$^{-1}$)")
            self._main_plot.plot_dat(sample_dat)

        self._compare_plot.set_x_label("q (nm$^{-1}$)")
        self._subtracted_plot.set_x_label("q (nm$^{-1}$)")
        if sample_dat and buffer_dat:
            self._compare_plot.plot_sample_and_scaled_buffer(
                sample_dat,
                buffer_dat,
                subtracted_path=subtracted_dat,
                subtract_options=self._sub_subtract_opts,
            )
        else:
            self._compare_plot.clear()
        if subtracted_dat:
            self._subtracted_plot.plot_dat(subtracted_dat, label="subtracted")
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
        path = path or ""
        if path == self._current_image_path:
            return
        had = self.has_image()
        self._current_image_path = path
        if not path:
            self._img.clear()
        else:
            self._img.show_tiff(path)
        now = self.has_image()
        if had != now:
            self.image_presence_changed.emit(now)

    def _open_2d_viewer(self) -> None:
        if not self._current_image_path:
            return
        self._store_image_2d_viewer(
            open_image_2d_dialog(
                self,
                self._current_image_path,
                reuse=self._image_2d_viewer_dialog(),
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
