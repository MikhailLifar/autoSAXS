from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtWidgets import QDialog, QVBoxLayout, QWidget

from ....widgets.plots import DatCurveViewerDialog, mpl_navigation_toolbar, open_dat_curve_dialog
from .plots import GnomFitPlot, PrPlot, ShapeFitPlot


class _MonodisperseFirViewerDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Shape fit")
        self.resize(1000, 600)
        self._plot = ShapeFitPlot(figsize=(5.0, 3.5))
        lay = QVBoxLayout(self)
        lay.addWidget(mpl_navigation_toolbar(self._plot, self))
        lay.addWidget(self._plot, 1)

    def show_fir(self, path: str, *, label: str = "fit") -> None:
        short = Path(path).name
        self.setWindowTitle(f"I(q) fit — {short}")
        self._plot.plot_from_fir(path, label=label)


class MonodispersePlotClickRouter:
    """Click-to-open viewers for monodisperse wizard plots (structured files only)."""

    def __init__(self, parent: QWidget) -> None:
        self._parent = parent
        self._dat_dlg: Optional[DatCurveViewerDialog] = None
        self._fir_dlg: Optional[_MonodisperseFirViewerDialog] = None
        self._gnom_open: Optional[Callable[[], None]] = None
        self._guinier_open: Optional[Callable[[], None]] = None

    def set_gnom_open_handler(self, handler: Optional[Callable[[], None]]) -> None:
        """When set, clicks on P(r) / I(q) GNOM plots open the adjust wizard."""
        self._gnom_open = handler

    def set_guinier_open_handler(self, handler: Optional[Callable[[], None]]) -> None:
        """When set, clicks on the Guinier preview open the adjust wizard."""
        self._guinier_open = handler

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
        suf = Path(path).suffix.lower()
        if viewer == "guinier":
            if self._guinier_open is not None:
                self._guinier_open()
                return
        if viewer in ("gnom_iq", "gnom_pr") or (viewer is None and suf == ".out"):
            if self._gnom_open is not None:
                self._gnom_open()
                return
        if suf == ".dat":
            self._dat_dlg = open_dat_curve_dialog(self._parent, path, reuse=self._dat_dlg)
            return
        if suf == ".fir":
            if self._fir_dlg is None:
                self._fir_dlg = _MonodisperseFirViewerDialog(self._parent)
            self._fir_dlg.show_fir(path)
            self._fir_dlg.show()
            self._fir_dlg.raise_()
            self._fir_dlg.activateWindow()
