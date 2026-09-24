"""Embedded GNOM residual plot canvas (shared by mono / poly adjust wizards)."""

from __future__ import annotations

import os
from typing import Optional

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5.QtCore import Qt

from .gnom_iq_plot import draw_gnom_residuals_on_ax


class GnomResidualsPlot(FigureCanvas):
    def __init__(self, *, figsize=(3.2, 2.4)) -> None:
        self._fig = Figure(figsize=figsize, dpi=100)
        super().__init__(self._fig)
        self._ax = self._fig.add_subplot(111)
        self._click_path: Optional[str] = None
        self._click_viewer: Optional[str] = None

    @property
    def click_path(self) -> Optional[str]:
        return self._click_path

    @property
    def click_viewer(self) -> Optional[str]:
        return self._click_viewer

    def _show_status(self, text: str) -> None:
        self._click_path = None
        self._click_viewer = None
        self._ax.clear()
        self._ax.text(0.5, 0.5, text, ha="center", va="center", transform=self._ax.transAxes, fontsize=9)
        self._ax.set_axis_off()
        self.draw_idle()
        self.setCursor(Qt.ArrowCursor)

    def clear_plot(self) -> None:
        self._show_status("—")

    def plot_from_gnom_out(self, gnom_out_path: str) -> None:
        err = draw_gnom_residuals_on_ax(self._ax, gnom_out_path)
        if err:
            self._show_status(err)
            return
        self._click_path = gnom_out_path if gnom_out_path and os.path.isfile(gnom_out_path) else None
        self._click_viewer = "gnom_residuals"
        self._fig.tight_layout()
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)
