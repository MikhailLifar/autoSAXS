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
from .plots import GnomFitPlot, PrPlot


class GnomPane(QWidget):
    params_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        plots_row = QHBoxLayout()
        plots_row.setSpacing(8)
        self._fit_plot = GnomFitPlot()
        self._pr_plot = PrPlot()
        for p in (self._fit_plot, self._pr_plot):
            p.setMinimumHeight(120)
            p.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        iq_box = QGroupBox("I(q)")
        iq_lay = QVBoxLayout(iq_box)
        iq_lay.setContentsMargins(6, 8, 6, 6)
        iq_lay.addWidget(self._fit_plot, 1)
        pr_box = QGroupBox("P(r)")
        pr_lay = QVBoxLayout(pr_box)
        pr_lay.setContentsMargins(6, 8, 6, 6)
        pr_lay.addWidget(self._pr_plot, 1)
        plots_row.addWidget(iq_box, 1)
        plots_row.addWidget(pr_box, 1)

        self._rg = QDoubleSpinBox()
        self._rg.setDecimals(4)
        self._rg.setRange(0.0, 1e6)
        self._rg.setSpecialValueText("(auto)")
        self._rg.setValue(0.0)
        self._first = QSpinBox()
        self._first.setMinimum(1)
        self._first.setMaximum(99999)
        self._last = QSpinBox()
        self._last.setMinimum(0)
        self._last.setMaximum(99999)
        self._last.setSpecialValueText("(none)")
        self._smooth = QDoubleSpinBox()
        self._smooth.setDecimals(2)
        self._smooth.setRange(0.0, 100.0)
        self._smooth.setValue(2.0)

        self._lbl_diagnostics = QLabel("—")
        self._lbl_diagnostics.setWordWrap(True)

        form = QFormLayout()
        form.addRow("Rg (nm)", self._rg)
        form.addRow("first", self._first)
        form.addRow("last", self._last)
        form.addRow("smooth", self._smooth)

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
        for w in (self._rg, self._first, self._last, self._smooth):
            w.valueChanged.connect(self._schedule_emit)

    def set_running(self, running: bool) -> None:
        if running:
            self._debounce.stop()
        for w in (self._rg, self._first, self._last, self._smooth):
            w.setEnabled(not running)

    def _schedule_emit(self, *_args) -> None:
        self._debounce.start()

    def gnom_params(self) -> dict:
        rg = float(self._rg.value())
        last = int(self._last.value())
        out = {
            "first": int(self._first.value()),
            "smooth": float(self._smooth.value()),
        }
        if rg > 0.0:
            out["rg_nm"] = rg
        if last > 0:
            out["last"] = last
        return out

    def set_params(self, params: dict) -> None:
        if not isinstance(params, dict):
            return
        # Programmatic updates must not emit params_changed (that pauses/cancels the
        # live pipeline via intervention_requested — kills model_dam mid-auto-job).
        self._debounce.stop()
        widgets = (self._rg, self._first, self._last, self._smooth)
        for w in widgets:
            w.blockSignals(True)
        try:
            if params.get("rg_nm") is not None:
                self._rg.setValue(float(params["rg_nm"]))
            elif params.get("rg") is not None:
                self._rg.setValue(float(params["rg"]))
            if params.get("first") is not None:
                self._first.setValue(int(params["first"]))
            if params.get("last") is not None:
                self._last.setValue(int(params["last"]))
            if params.get("smooth") is not None:
                self._smooth.setValue(float(params["smooth"]))
        finally:
            for w in widgets:
                w.blockSignals(False)

    def set_diagnostics(self, *, text: str = "", poor: bool = False) -> None:
        self._lbl_diagnostics.setText(text or "—")
        apply_quality_hint_style(self._lbl_diagnostics, poor=bool(poor) and bool(text))

    def show_gnom(self, profile_path: str, gnom_out_path: str) -> None:
        self._fit_plot.plot_from_dat_and_gnom_out(profile_path, gnom_out_path)
        self._pr_plot.plot_from_gnom_out(gnom_out_path)

    def clear_view(self) -> None:
        self._fit_plot.clear_plot()
        self._pr_plot.clear_plot()
        self.set_diagnostics()

    @property
    def fit_plot(self) -> GnomFitPlot:
        return self._fit_plot

    @property
    def pr_plot(self) -> PrPlot:
        return self._pr_plot
