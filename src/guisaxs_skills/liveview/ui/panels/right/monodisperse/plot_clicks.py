from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt5.QtWidgets import QDialog, QVBoxLayout, QWidget

from ....widgets.plots import DatCurveViewerDialog, mpl_navigation_toolbar, open_dat_curve_dialog
from .plots import GnomFitPlot, PrPlot, ShapeFitPlot


class _MonodisperseIqViewerDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("GNOM I(q)")
        self.resize(1000, 500)
        self._plot = GnomFitPlot(figsize=(5.0, 3.5))
        lay = QVBoxLayout(self)
        lay.addWidget(mpl_navigation_toolbar(self._plot, self))
        lay.addWidget(self._plot, 1)

    def show_gnom_out(self, path: str) -> None:
        short = Path(path).name
        self.setWindowTitle(f"I(q) fit — {short}")
        self._plot.plot_from_gnom_out(path)


class _MonodisperseOutViewerDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("GNOM")
        self.resize(1000, 500)
        self._pr = PrPlot(figsize=(4.5, 3.2))
        lay = QVBoxLayout(self)
        lay.addWidget(mpl_navigation_toolbar(self._pr, self))
        lay.addWidget(self._pr, 1)

    def show_gnom_out(self, path: str) -> None:
        short = Path(path).name
        self.setWindowTitle(f"P(r) — {short}")
        self._pr.plot_from_gnom_out(path)


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
        self._iq_dlg: Optional[_MonodisperseIqViewerDialog] = None
        self._out_dlg: Optional[_MonodisperseOutViewerDialog] = None
        self._fir_dlg: Optional[_MonodisperseFirViewerDialog] = None

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
        if viewer == "gnom_iq":
            if self._iq_dlg is None:
                self._iq_dlg = _MonodisperseIqViewerDialog(self._parent)
            self._iq_dlg.show_gnom_out(path)
            self._iq_dlg.show()
            self._iq_dlg.raise_()
            self._iq_dlg.activateWindow()
            return
        if viewer == "gnom_pr" or (viewer is None and suf == ".out"):
            if self._out_dlg is None:
                self._out_dlg = _MonodisperseOutViewerDialog(self._parent)
            self._out_dlg.show_gnom_out(path)
            self._out_dlg.show()
            self._out_dlg.raise_()
            self._out_dlg.activateWindow()
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
