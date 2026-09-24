"""Fit residual canvas + rich enlarge viewers shared by modeling mini-apps."""

from __future__ import annotations

import os
from typing import Callable, Optional

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT
from matplotlib.figure import Figure
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QWidget


def mpl_navigation_toolbar(canvas: FigureCanvas, parent: QWidget) -> NavigationToolbar2QT:
    """Same zoom / pan / home / save toolbar as GNOM adjust wizards."""
    return NavigationToolbar2QT(canvas, parent)


class FitResidualsPlot(FigureCanvas):
    def __init__(self, *, figsize=(3.2, 1.6)) -> None:
        self._fig = Figure(figsize=figsize, dpi=100)
        super().__init__(self._fig)
        self._ax = self._fig.add_subplot(111)
        self._click_path: Optional[str] = None
        self.clear_plot()

    @property
    def click_path(self) -> Optional[str]:
        return self._click_path

    def clear_plot(self) -> None:
        self._click_path = None
        self._ax.clear()
        self._ax.text(0.5, 0.5, "—", ha="center", va="center", transform=self._ax.transAxes)
        self._ax.set_axis_off()
        self.draw_idle()
        self.setCursor(Qt.ArrowCursor)

    def plot_from_fir(self, fir_path: str) -> None:
        if not fir_path or not os.path.isfile(fir_path):
            self.clear_plot()
            return
        try:
            from autosaxs.core.utils import ensure_q_nm

            try:
                data = np.loadtxt(fir_path, comments="#")
            except Exception:
                data = np.loadtxt(fir_path, skiprows=1)
            if data.ndim == 1:
                data = data.reshape(1, -1)
            if data.shape[1] < 4:
                self.clear_plot()
                return
            q = data[:, 0]
            i_exp = data[:, 1]
            err = data[:, 2]
            i_fit = data[:, 3]
            q, i_exp, err = ensure_q_nm(q, i_exp, err)
        except Exception:
            self.clear_plot()
            return
        m = np.isfinite(q) & np.isfinite(i_exp) & np.isfinite(i_fit) & np.isfinite(err) & (err > 0)
        if not m.any():
            self.clear_plot()
            return
        resid = (i_exp[m] - i_fit[m]) / err[m]
        self._click_path = fir_path
        self._ax.clear()
        self._ax.axhline(0.0, color="0.5", lw=0.8)
        self._ax.scatter(q[m], resid, s=8, alpha=0.7)
        self._ax.set_xlabel("q (nm⁻¹)")
        self._ax.set_ylabel("(I−fit)/σ")
        self._ax.grid(True, alpha=0.2)
        self._fig.tight_layout()
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)


class MplViewerDialog(QDialog):
    """Non-modal enlarged plot with matplotlib navigation toolbar (GNOM-wizard style)."""

    def __init__(self, *, title: str, plot: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1000, 600)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self._plot = plot
        lay = QVBoxLayout(self)
        if isinstance(plot, FigureCanvas):
            lay.addWidget(mpl_navigation_toolbar(plot, self))
        lay.addWidget(plot, 1)

    @property
    def plot(self) -> QWidget:
        return self._plot

    def present(self, *, title: Optional[str] = None) -> None:
        if title:
            self.setWindowTitle(title)
        self.show()
        self.raise_()
        self.activateWindow()


class ModelingPlotClickRouter:
    """
    Click-to-open rich viewers for modeling comparison plots.

    ``populate`` redraws the persistent enlarged canvas for the current data.
    """

    def __init__(self, parent: QWidget) -> None:
        self._parent = parent
        self._dialogs: dict[str, MplViewerDialog] = {}

    def open(
        self,
        *,
        key: str,
        title: str,
        make_plot: Callable[[], QWidget],
        populate: Callable[[QWidget], None],
    ) -> None:
        dlg = self._dialogs.get(key)
        if dlg is None:
            dlg = MplViewerDialog(title=title, plot=make_plot(), parent=self._parent)
            self._dialogs[key] = dlg
        populate(dlg.plot)
        dlg.present(title=title)


def enlarge_canvas(plot: QWidget, *, title: str, parent: Optional[QWidget] = None) -> None:
    """Backward-compatible entry: open a rich toolbar viewer for a freshly built plot."""
    dlg = MplViewerDialog(title=title, plot=plot, parent=parent)
    dlg.present()
