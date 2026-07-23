from __future__ import annotations

from PyQt5.QtCore import QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ......ui.style import apply_quality_hint_style
from .plots import DrPlot, GnomFitPlot


class SizesPane(QWidget):
    params_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        plots_row = QHBoxLayout()
        plots_row.setSpacing(8)
        self._fit_plot = GnomFitPlot()
        self._dr_plot = DrPlot()
        for p in (self._fit_plot, self._dr_plot):
            p.setMinimumHeight(120)
            p.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        iq_box = QGroupBox("I(q)")
        iq_lay = QVBoxLayout(iq_box)
        iq_lay.setContentsMargins(6, 8, 6, 6)
        iq_lay.addWidget(self._fit_plot, 1)
        dr_box = QGroupBox("D(R)")
        dr_lay = QVBoxLayout(dr_box)
        dr_lay.setContentsMargins(6, 8, 6, 6)
        dr_lay.addWidget(self._dr_plot, 1)
        plots_row.addWidget(iq_box, 1)
        plots_row.addWidget(dr_box, 1)

        self._first = QSpinBox()
        self._first.setMinimum(1)
        self._first.setMaximum(99999)
        self._first.setValue(1)
        self._last = QSpinBox()
        self._last.setMinimum(0)
        self._last.setMaximum(99999)
        self._last.setSpecialValueText("(none)")
        self._rmin = QDoubleSpinBox()
        self._rmin.setDecimals(4)
        self._rmin.setRange(0.0, 1e6)
        self._rmin.setSpecialValueText("(auto)")
        self._rmin.setValue(0.0)
        self._rmax = QDoubleSpinBox()
        self._rmax.setDecimals(4)
        self._rmax.setRange(0.0, 1e6)
        self._rmax.setSpecialValueText("(auto)")
        self._rmax.setValue(0.0)
        self._alpha = QDoubleSpinBox()
        self._alpha.setDecimals(4)
        self._alpha.setRange(0.0, 1e6)
        self._alpha.setSpecialValueText("(auto)")
        self._alpha.setValue(0.0)

        self._lbl_diagnostics = QLabel("—")
        self._lbl_diagnostics.setWordWrap(True)

        form = QFormLayout()
        form.addRow("first", self._first)
        form.addRow("last", self._last)
        form.addRow("rmin", self._rmin)
        form.addRow("rmax", self._rmax)
        form.addRow("alpha", self._alpha)

        right = QVBoxLayout()
        right.addLayout(form)
        right.addWidget(self._lbl_diagnostics, 1)
        right.addStretch(1)

        body = QHBoxLayout()
        body.setSpacing(10)
        body.addLayout(plots_row, 3)
        body.addLayout(right, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addLayout(body, 1)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self.params_changed.emit)
        for w in (self._first, self._last, self._rmin, self._rmax, self._alpha):
            w.valueChanged.connect(self._schedule_emit)

    def set_running(self, running: bool) -> None:
        if running:
            self._debounce.stop()
        for w in (self._first, self._last, self._rmin, self._rmax, self._alpha):
            w.setEnabled(not running)

    def _schedule_emit(self, *_args) -> None:
        self._debounce.start()

    def sizes_params(self) -> dict:
        out: dict = {"first": int(self._first.value())}
        last = int(self._last.value())
        if last > 0:
            out["last"] = last
        rmin = float(self._rmin.value())
        if rmin > 0.0:
            out["rmin_nm"] = rmin
        rmax = float(self._rmax.value())
        if rmax > 0.0:
            out["rmax_nm"] = rmax
        alpha = float(self._alpha.value())
        if alpha > 0.0:
            out["alpha"] = alpha
        return out

    def set_params(self, params: dict) -> None:
        if not isinstance(params, dict):
            return
        self._debounce.stop()
        widgets = (self._first, self._last, self._rmin, self._rmax, self._alpha)
        for w in widgets:
            w.blockSignals(True)
        try:
            if params.get("first") is not None:
                self._first.setValue(max(1, int(params["first"])))
            if params.get("last") is not None:
                self._last.setValue(max(0, int(params["last"])))
            if params.get("rmin_nm") is not None:
                self._rmin.setValue(float(params["rmin_nm"]))
            if params.get("rmax_nm") is not None:
                self._rmax.setValue(float(params["rmax_nm"]))
            if params.get("alpha") is not None:
                self._alpha.setValue(float(params["alpha"]))
        finally:
            for w in widgets:
                w.blockSignals(False)

    def set_diagnostics(self, *, text: str = "", poor: bool = False) -> None:
        self._lbl_diagnostics.setText(text or "—")
        apply_quality_hint_style(self._lbl_diagnostics, poor=bool(poor) and bool(text))

    def show_sizes(self, profile_path: str, gnom_out: str) -> None:
        self._fit_plot.plot_from_gnom_out(gnom_out)
        self._dr_plot.plot_from_gnom_out(gnom_out)

    def clear_view(self) -> None:
        self._fit_plot.clear_plot()
        self._dr_plot.clear_plot()
        self.set_diagnostics()

    @property
    def fit_plot(self) -> GnomFitPlot:
        return self._fit_plot

    @property
    def dr_plot(self) -> DrPlot:
        return self._dr_plot
