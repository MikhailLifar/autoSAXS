from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QGridLayout, QGroupBox, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .gnom_pane import GnomPane
from .guinier_pane import GuinierPane
from .plot_clicks import MonodispersePlotClickRouter
from .shape_pane import ShapePane


def _pane_group(title: str, inner: QWidget) -> QGroupBox:
    """Framed pane matching main-window QGroupBox style (2D / 1D panels)."""
    box = QGroupBox(title)
    lay = QVBoxLayout(box)
    lay.setContentsMargins(8, 10, 8, 8)
    lay.setSpacing(6)
    inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    lay.addWidget(inner, 1)
    return box


class MonodisperseWizardWidget(QWidget):
    auto_toggle_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.guinier_pane = GuinierPane(self)
        self.gnom_pane = GnomPane(self)
        self.shape_pane = ShapePane(self)
        self._auto_btn = QPushButton("Stop auto-processing")
        self._auto_btn.clicked.connect(self.auto_toggle_clicked.emit)
        self._paused = False

        grid = QGridLayout()
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        guinier_box = _pane_group("Guinier", self.guinier_pane)
        guinier_box.setMinimumWidth(268)
        grid.addWidget(guinier_box, 0, 0, 2, 1)
        grid.addWidget(_pane_group("GNOM", self.gnom_pane), 0, 1, 1, 1)
        grid.addWidget(_pane_group("Shape", self.shape_pane), 1, 1, 1, 1)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(1, 4)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addLayout(grid, 1)
        self._auto_btn.setMaximumWidth(240)
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.addStretch(1)
        btn_row.addWidget(self._auto_btn, 0)
        lay.addLayout(btn_row, 0)

        self._plot_clicks = MonodispersePlotClickRouter(self)
        for plot in (
            self.guinier_pane.plot_widget,
            self.gnom_pane.fit_plot,
            self.gnom_pane.pr_plot,
            self.shape_pane.fit_plot,
        ):
            self._plot_clicks.wire(plot)

    def bind_state(self, state) -> None:
        mode = getattr(state, "monodisperse_shape_mode", None)
        if mode is not None:
            self.shape_pane.set_shape_mode(str(getattr(mode, "value", mode)))
        wp = getattr(state, "monodisperse_wizard_params", None) or {}
        g_first = wp.get("guinier_first", wp.get("first"))
        g_last = wp.get("guinier_last", wp.get("last"))
        if g_first and g_last:
            self.guinier_pane.set_range(int(g_first), int(g_last))
        self.gnom_pane.set_params(wp)
        shapes = getattr(state, "fit_bodies_shapes", None)
        if shapes:
            self.shape_pane.set_selected_shapes(list(shapes))

    def set_running(self, running: bool) -> None:
        self.guinier_pane.set_running(running)
        self.gnom_pane.set_running(running)
        self.shape_pane.set_running(running)
        self._refresh_auto_button(processing_idle=not running)

    def set_queue_paused(self, paused: bool, *, processing_idle: bool = True) -> None:
        self._paused = bool(paused)
        self._refresh_auto_button(processing_idle=processing_idle)

    def auto_processing_paused(self) -> bool:
        return self._paused

    def _refresh_auto_button(self, *, processing_idle: bool) -> None:
        if self._paused:
            self._auto_btn.setText("Resume auto-processing")
            self._auto_btn.setEnabled(processing_idle)
        else:
            self._auto_btn.setText("Stop auto-processing")
            self._auto_btn.setEnabled(True)

    def summary_lines(self) -> tuple[str, str]:
        """Short status for the right-panel summary (like calibration preview area)."""
        rg = self.guinier_pane._lbl_rg.text()  # noqa: SLF001
        gnom = self.gnom_pane._lbl_diagnostics.text()  # noqa: SLF001
        shape = self.shape_pane.shape_mode()
        status = f"Rg (Guinier): {rg}\nGNOM: {gnom}\nShape: {shape}"
        return "Latest monodisperse results (open wizard for controls).", status
