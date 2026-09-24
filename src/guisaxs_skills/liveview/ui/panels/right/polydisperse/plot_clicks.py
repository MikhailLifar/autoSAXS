from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Optional

from PyQt5.QtWidgets import QDialog, QVBoxLayout, QWidget

from ....widgets.plots import DatCurveViewerDialog, mpl_navigation_toolbar, open_dat_curve_dialog
from .plots import DrPlot, GnomFitPlot, MixtureDistPlot, MixtureFitPlot


class _PolyIqViewerDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("I(q)")
        self.resize(1000, 500)
        self._plot = GnomFitPlot(figsize=(5.0, 3.5))
        lay = QVBoxLayout(self)
        lay.addWidget(mpl_navigation_toolbar(self._plot, self))
        lay.addWidget(self._plot, 1)

    def show_gnom_out(self, path: str) -> None:
        short = Path(path).name
        self.setWindowTitle(f"I(q) fit — {short}")
        self._plot.plot_from_gnom_out(path)


class _PolyDrViewerDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("D(R)")
        self.resize(1000, 500)
        self._plot = DrPlot(figsize=(4.5, 3.2))
        lay = QVBoxLayout(self)
        lay.addWidget(mpl_navigation_toolbar(self._plot, self))
        lay.addWidget(self._plot, 1)

    def show_gnom_out(self, path: str) -> None:
        short = Path(path).name
        self.setWindowTitle(f"D(R) — {short}")
        self._plot.plot_from_gnom_out(path)


class _PolyMixtureIqViewerDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MIXTURE I(q)")
        self.resize(1000, 500)
        self._plot = MixtureFitPlot(figsize=(5.0, 3.5))
        lay = QVBoxLayout(self)
        lay.addWidget(mpl_navigation_toolbar(self._plot, self))
        lay.addWidget(self._plot, 1)

    def show_fit(self, path: str, *, label: str = "fit") -> None:
        short = Path(path).name
        self.setWindowTitle(f"I(q) fit — {short}")
        self._plot.plot_from_fit(path, label=label)


class _PolyMixtureDistViewerDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("MIXTURE distribution")
        self.resize(1000, 500)
        self._plot = MixtureDistPlot(figsize=(4.5, 3.2))
        lay = QVBoxLayout(self)
        lay.addWidget(mpl_navigation_toolbar(self._plot, self))
        lay.addWidget(self._plot, 1)

    def show_model(
        self,
        row: dict[str, Any],
        *,
        r_min_ang: float,
        r_max_ang: float,
        label: str = "",
    ) -> None:
        title = f"Distribution — {label}" if label else "MIXTURE distribution"
        self.setWindowTitle(title)
        self._plot.plot_from_model_row(
            row,
            r_min_ang=r_min_ang,
            r_max_ang=r_max_ang,
            label=label,
        )


class PolydispersePlotClickRouter:
    """Click-to-open viewers for polydisperse window plots (structured files only)."""

    def __init__(self, parent: QWidget) -> None:
        self._parent = parent
        self._dat_dlg: Optional[DatCurveViewerDialog] = None
        self._iq_dlg: Optional[_PolyIqViewerDialog] = None
        self._dr_dlg: Optional[_PolyDrViewerDialog] = None
        self._mix_iq_dlg: Optional[_PolyMixtureIqViewerDialog] = None
        self._mix_dist_dlg: Optional[_PolyMixtureDistViewerDialog] = None
        self._sizes_open: Optional[Callable[[], None]] = None
        self._guinier_open: Optional[Callable[[], None]] = None

    def set_sizes_open_handler(self, handler: Optional[Callable[[], None]]) -> None:
        """When set, clicks on sizes I(q) / D(R) GNOM plots open the adjust wizard."""
        self._sizes_open = handler

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
        viewer = getattr(plot, "click_viewer", None)
        if viewer == "mixture_dist":
            payload = getattr(plot, "click_payload", None)
            if isinstance(payload, dict) and payload.get("row") is not None:
                self._open_mixture_dist(payload)
            return
        path = getattr(plot, "click_path", None)
        if not isinstance(path, str) or not path.strip() or not os.path.isfile(path.strip()):
            return
        self.open_path(path.strip(), viewer=viewer if isinstance(viewer, str) else None)

    def _open_mixture_dist(self, payload: dict[str, Any]) -> None:
        if self._mix_dist_dlg is None:
            self._mix_dist_dlg = _PolyMixtureDistViewerDialog(self._parent)
        self._mix_dist_dlg.show_model(
            dict(payload.get("row") or {}),
            r_min_ang=float(payload.get("r_min_ang") or 1.0),
            r_max_ang=float(payload.get("r_max_ang") or 120.0),
            label=str(payload.get("label") or ""),
        )
        self._mix_dist_dlg.show()
        self._mix_dist_dlg.raise_()
        self._mix_dist_dlg.activateWindow()

    def open_path(self, path: str, *, viewer: Optional[str] = None) -> None:
        suf = Path(path).suffix.lower()
        if viewer == "guinier":
            if self._guinier_open is not None:
                self._guinier_open()
                return
        if viewer in ("gnom_iq", "gnom_dr") or (viewer is None and suf == ".out"):
            if self._sizes_open is not None:
                self._sizes_open()
                return
        if viewer == "gnom_iq" or (viewer is None and suf == ".out"):
            if self._iq_dlg is None:
                self._iq_dlg = _PolyIqViewerDialog(self._parent)
            self._iq_dlg.show_gnom_out(path)
            self._iq_dlg.show()
            self._iq_dlg.raise_()
            self._iq_dlg.activateWindow()
            return
        if viewer == "gnom_dr":
            if self._dr_dlg is None:
                self._dr_dlg = _PolyDrViewerDialog(self._parent)
            self._dr_dlg.show_gnom_out(path)
            self._dr_dlg.show()
            self._dr_dlg.raise_()
            self._dr_dlg.activateWindow()
            return
        if viewer == "mixture_iq" or suf == ".fit":
            if self._mix_iq_dlg is None:
                self._mix_iq_dlg = _PolyMixtureIqViewerDialog(self._parent)
            self._mix_iq_dlg.show_fit(path)
            self._mix_iq_dlg.show()
            self._mix_iq_dlg.raise_()
            self._mix_iq_dlg.activateWindow()
            return
        if suf == ".dat":
            self._dat_dlg = open_dat_curve_dialog(self._parent, path, reuse=self._dat_dlg)
