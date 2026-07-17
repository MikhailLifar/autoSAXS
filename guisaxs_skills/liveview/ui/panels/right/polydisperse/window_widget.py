from __future__ import annotations

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QGridLayout, QGroupBox, QHBoxLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ..monodisperse.guinier_pane import GuinierPane
from .mixture_pane import MixturePane
from .plot_clicks import PolydispersePlotClickRouter
from .sizes_pane import SizesPane


def _pane_group(title: str, inner: QWidget) -> QGroupBox:
    box = QGroupBox(title)
    lay = QVBoxLayout(box)
    lay.setContentsMargins(8, 10, 8, 8)
    lay.setSpacing(6)
    inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
    lay.addWidget(inner, 1)
    return box


class PolydisperseWindowWidget(QWidget):
    auto_toggle_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.guinier_pane = GuinierPane(self)
        self.sizes_pane = SizesPane(self)
        self.mixture_pane = MixturePane(self)
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
        grid.addWidget(_pane_group("fit_sizes (D(R))", self.sizes_pane), 0, 1, 1, 1)
        grid.addWidget(_pane_group("fit_mixture", self.mixture_pane), 1, 1, 1, 1)
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

        self._plot_clicks = PolydispersePlotClickRouter(self)
        for plot in (
            self.guinier_pane.plot_widget,
            self.sizes_pane.fit_plot,
            self.sizes_pane.dr_plot,
            self.mixture_pane.fit_plot,
            self.mixture_pane.dist_plot,
        ):
            self._plot_clicks.wire(plot)

    def bind_state(self, state) -> None:
        mode = getattr(state, "polydisperse_mixture_mode", None)
        if mode is not None:
            self.mixture_pane.set_mixture_mode(str(getattr(mode, "value", mode)))
        wp = getattr(state, "polydisperse_window_params", None) or {}
        g_first = wp.get("guinier_first")
        g_last = wp.get("guinier_last")
        if g_first and g_last:
            self.guinier_pane.set_range(int(g_first), int(g_last))
        self.sizes_pane.set_params(wp)
        mix = wp.get("mixture") if isinstance(wp.get("mixture"), dict) else None
        if mix:
            self.mixture_pane.set_mixture_params(mix)
        elif getattr(state, "fit_mixture_options", None):
            self.mixture_pane.set_mixture_params(dict(state.fit_mixture_options))

    def set_running(self, running: bool) -> None:
        self.guinier_pane.set_running(running)
        self.sizes_pane.set_running(running)
        self.mixture_pane.set_running(running)
        self._refresh_auto_button(processing_idle=not running)

    def set_queue_paused(self, paused: bool, *, processing_idle: bool = True) -> None:
        self._paused = bool(paused)
        self._refresh_auto_button(processing_idle=processing_idle)

    def auto_processing_paused(self) -> bool:
        return bool(self._paused)

    def _refresh_auto_button(self, *, processing_idle: bool) -> None:
        if self._paused:
            self._auto_btn.setText("Resume auto-processing")
            self._auto_btn.setEnabled(processing_idle)
        else:
            self._auto_btn.setText("Stop auto-processing")
            self._auto_btn.setEnabled(True)

    def set_auto_processing_paused(self, paused: bool) -> None:
        self.set_queue_paused(paused)
