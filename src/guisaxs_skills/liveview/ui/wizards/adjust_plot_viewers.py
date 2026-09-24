"""Click-to-open matplotlib viewers for GNOM adjust wizards (P(r)/D(R), I(q), residuals)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtWidgets import QDialog, QVBoxLayout, QWidget

from ..widgets.plots import mpl_navigation_toolbar
from ..panels.right.gnom_residuals_plot import GnomResidualsPlot


class _MplViewerDialog(QDialog):
    def __init__(self, *, title: str, plot: QWidget, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1000, 600)
        self._plot = plot
        lay = QVBoxLayout(self)
        lay.addWidget(mpl_navigation_toolbar(self._plot, self))
        lay.addWidget(self._plot, 1)

    @property
    def plot(self):
        return self._plot


class AdjustPlotClickRouter:
    """
    Open enlarged viewers for distribution / I(q) fit / residuals plots.

    ``make_dist_plot`` / ``make_fit_plot`` are factories returning a fresh canvas
    (PrPlot or DrPlot / GnomFitPlot) for the enlarged dialog.
    """

    def __init__(
        self,
        parent: QWidget,
        *,
        make_dist_plot: Callable[[], QWidget],
        make_fit_plot: Callable[[], QWidget],
        dist_title: str,
        fit_title: str = "I(q) fit",
        residuals_title: str = "Residuals",
    ) -> None:
        self._parent = parent
        self._make_dist = make_dist_plot
        self._make_fit = make_fit_plot
        self._dist_title = dist_title
        self._fit_title = fit_title
        self._resid_title = residuals_title
        self._dist_dlg: Optional[_MplViewerDialog] = None
        self._fit_dlg: Optional[_MplViewerDialog] = None
        self._resid_dlg: Optional[_MplViewerDialog] = None

    def wire(self, plot) -> None:
        plot.mpl_connect("button_press_event", lambda ev, p=plot: self._on_click(ev, p))

    def _on_click(self, ev: object, plot) -> None:
        if getattr(ev, "inaxes", None) is None:
            return
        if int(getattr(ev, "button", 0)) != 1:
            return
        path = getattr(plot, "click_path", None)
        if not isinstance(path, str) or not path.strip() or not os.path.isfile(path.strip()):
            return
        viewer = getattr(plot, "click_viewer", None)
        self.open_path(path.strip(), viewer=viewer if isinstance(viewer, str) else None)

    def open_path(self, path: str, *, viewer: Optional[str] = None) -> None:
        short = Path(path).name
        if viewer == "gnom_residuals":
            self._open_residuals(path, short)
            return
        if viewer in ("gnom_pr", "gnom_dr"):
            if self._dist_dlg is None:
                self._dist_dlg = _MplViewerDialog(
                    title=self._dist_title,
                    plot=self._make_dist(),
                    parent=self._parent,
                )
            self._dist_dlg.setWindowTitle(f"{self._dist_title} — {short}")
            plot = self._dist_dlg.plot
            if hasattr(plot, "plot_from_gnom_out"):
                plot.plot_from_gnom_out(path)
            self._dist_dlg.show()
            self._dist_dlg.raise_()
            self._dist_dlg.activateWindow()
            return
        if viewer == "gnom_iq":
            if self._fit_dlg is None:
                self._fit_dlg = _MplViewerDialog(
                    title=self._fit_title,
                    plot=self._make_fit(),
                    parent=self._parent,
                )
            self._fit_dlg.setWindowTitle(f"{self._fit_title} — {short}")
            plot = self._fit_dlg.plot
            if hasattr(plot, "plot_from_gnom_out"):
                plot.plot_from_gnom_out(path)
            self._fit_dlg.show()
            self._fit_dlg.raise_()
            self._fit_dlg.activateWindow()
            return

    def _open_residuals(self, path: str, short: str) -> None:
        if self._resid_dlg is None:
            self._resid_dlg = _MplViewerDialog(
                title=self._resid_title,
                plot=GnomResidualsPlot(figsize=(5.0, 3.5)),
                parent=self._parent,
            )
        self._resid_dlg.setWindowTitle(f"{self._resid_title} — {short}")
        self._resid_dlg.plot.plot_from_gnom_out(path)
        self._resid_dlg.show()
        self._resid_dlg.raise_()
        self._resid_dlg.activateWindow()
