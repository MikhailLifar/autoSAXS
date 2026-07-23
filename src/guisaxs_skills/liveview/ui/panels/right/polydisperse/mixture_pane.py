from __future__ import annotations

from typing import Any, Optional

from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..monodisperse.format_display import format_display_number
from ......ui.style import apply_quality_hint_style
from .plots import MixtureDistPlot, MixtureFitPlot

# Skill defaults when radius bounds are omitted (nm → Å for distribution plot).
_DEFAULT_R_MIN_NM = 0.1


class MixturePane(QWidget):
    mode_changed = pyqtSignal(str)
    params_changed = pyqtSignal()
    fit_selection_changed = pyqtSignal(str)
    rerun_mixture_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        mode_row = QHBoxLayout()
        self._grp = QButtonGroup(self)
        self._rb_none = QRadioButton("None")
        self._rb_mixture = QRadioButton("MIXTURE")
        self._rb_none.setChecked(True)
        for rb in (self._rb_none, self._rb_mixture):
            self._grp.addButton(rb)
            mode_row.addWidget(rb)
        mode_row.addStretch(1)
        self._rerun = QPushButton("Re-run D(R)")
        self._rerun.clicked.connect(self.rerun_mixture_requested.emit)
        mode_row.addWidget(self._rerun)

        self._hint = QLabel(
            "Select MIXTURE for automatic D(R) parametric fit; Re-run D(R) for a manual re-run"
        )
        self._hint.setWordWrap(True)
        self._hint.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        plots_row = QHBoxLayout()
        plots_row.setSpacing(8)
        self._fit_plot = MixtureFitPlot()
        self._dist_plot = MixtureDistPlot()
        for p in (self._fit_plot, self._dist_plot):
            p.setMinimumHeight(100)
            p.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        iq_box = QGroupBox("I(q)")
        iq_lay = QVBoxLayout(iq_box)
        iq_lay.setContentsMargins(6, 8, 6, 6)
        iq_lay.addWidget(self._fit_plot, 1)
        dist_box = QGroupBox("Distribution")
        dist_lay = QVBoxLayout(dist_box)
        dist_lay.setContentsMargins(6, 8, 6, 6)
        dist_lay.addWidget(self._dist_plot, 1)
        plots_row.addWidget(iq_box, 1)
        plots_row.addWidget(dist_box, 1)

        self._fit_combo = QComboBox()
        self._fit_combo.currentIndexChanged.connect(self._on_fit_combo)
        self._lbl_status = QLabel("—")
        self._lbl_status.setWordWrap(True)

        self._sp_max_nph = QSpinBox()
        self._sp_max_nph.setRange(1, 3)
        self._sp_max_nph.setValue(3)
        # Like fit_sizes last / rmax: 0 = omit (skill derives); filled after a run.
        self._sp_r_max = QDoubleSpinBox()
        self._sp_r_max.setRange(0.0, 1e6)
        self._sp_r_max.setDecimals(4)
        self._sp_r_max.setSpecialValueText("(auto)")
        self._sp_r_max.setValue(0.0)
        self._sp_poly_max = QDoubleSpinBox()
        self._sp_poly_max.setRange(0.0, 1e6)
        self._sp_poly_max.setDecimals(4)
        self._sp_poly_max.setSpecialValueText("(auto)")
        self._sp_poly_max.setValue(0.0)
        self._q_min = QDoubleSpinBox()
        self._q_min.setRange(0.0, 1e6)
        self._q_min.setDecimals(5)
        self._q_min.setSpecialValueText("(full)")
        self._q_min.setValue(0.0)
        self._q_max = QDoubleSpinBox()
        self._q_max.setRange(0.0, 1e6)
        self._q_max.setDecimals(5)
        self._q_max.setSpecialValueText("(full)")
        self._q_max.setValue(0.0)

        form = QFormLayout()
        form.addRow("Model", self._fit_combo)
        form.addRow("max_nph", self._sp_max_nph)
        form.addRow("r_max (nm)", self._sp_r_max)
        form.addRow("poly_max (nm)", self._sp_poly_max)
        form.addRow("q_min (nm⁻¹)", self._q_min)
        form.addRow("q_max (nm⁻¹)", self._q_max)

        ctrl = QVBoxLayout()
        ctrl.addLayout(form)
        ctrl.addWidget(self._lbl_status, 1)

        self._body = QWidget()
        body = QHBoxLayout(self._body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)
        body.addLayout(plots_row, 3)
        body.addLayout(ctrl, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addLayout(mode_row)
        lay.addWidget(self._hint, 0)
        lay.addWidget(self._body, 1)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self.params_changed.emit)
        for w in (
            self._sp_max_nph,
            self._sp_r_max,
            self._sp_poly_max,
            self._q_min,
            self._q_max,
        ):
            w.valueChanged.connect(self._schedule_emit)

        self._rb_none.toggled.connect(self._on_mode_toggled)
        self._rb_mixture.toggled.connect(self._on_mode_toggled)
        self._update_mode_ui()
        self._rows_by_label: dict[str, dict[str, Any]] = {}
        self._fit_paths: dict[str, str] = {}
        self._best_label = ""

    def _param_widgets(self) -> tuple:
        return (
            self._fit_combo,
            self._sp_max_nph,
            self._sp_r_max,
            self._sp_poly_max,
            self._q_min,
            self._q_max,
        )

    def _schedule_emit(self, *_args) -> None:
        if self.mixture_mode() == "none":
            return
        self._debounce.start()

    def _on_mode_toggled(self, *_args) -> None:
        self._update_mode_ui()
        self.mode_changed.emit(self.mixture_mode())

    def _update_mode_ui(self) -> None:
        enabled = self.mixture_mode() == "mixture"
        self._hint.setVisible(not enabled)
        self._body.setVisible(enabled)
        self._rerun.setEnabled(enabled)
        for w in self._param_widgets():
            w.setEnabled(enabled)
        if not enabled:
            self.clear_view()

    def mixture_mode(self) -> str:
        return "mixture" if self._rb_mixture.isChecked() else "none"

    def set_mixture_mode(self, mode: str) -> None:
        m = str(mode or "none").lower()
        self._rb_none.blockSignals(True)
        self._rb_mixture.blockSignals(True)
        try:
            if m == "mixture":
                self._rb_mixture.setChecked(True)
            else:
                self._rb_none.setChecked(True)
        finally:
            self._rb_none.blockSignals(False)
            self._rb_mixture.blockSignals(False)
        self._update_mode_ui()

    def mixture_params(self) -> dict[str, Any]:
        """Params for the skill. Omit r_max / poly_max when (auto) so the skill derives them."""
        out: dict[str, Any] = {
            "max_nph": int(self._sp_max_nph.value()),
        }
        r_max = float(self._sp_r_max.value())
        if r_max > 0.0:
            out["r_max"] = r_max
        poly_max = float(self._sp_poly_max.value())
        if poly_max > 0.0:
            out["poly_max"] = poly_max
        qmin = float(self._q_min.value())
        qmax = float(self._q_max.value())
        if qmin > 0.0:
            out["q_min_nm"] = qmin
        if qmax > 0.0:
            out["q_max_nm"] = qmax
        return out

    def set_mixture_params(self, params: dict) -> None:
        if not isinstance(params, dict):
            return
        self._debounce.stop()
        widgets = (
            self._sp_max_nph,
            self._sp_r_max,
            self._sp_poly_max,
            self._q_min,
            self._q_max,
        )
        for w in widgets:
            w.blockSignals(True)
        try:
            if params.get("max_nph") is not None:
                self._sp_max_nph.setValue(max(1, min(3, int(params["max_nph"]))))
            for key, spin in (
                ("r_max", self._sp_r_max),
                ("poly_max", self._sp_poly_max),
                ("q_min_nm", self._q_min),
                ("q_max_nm", self._q_max),
            ):
                if params.get(key) is not None:
                    spin.setValue(float(params[key]))
        finally:
            for w in widgets:
                w.blockSignals(False)

    def set_running(self, running: bool) -> None:
        if running:
            self._debounce.stop()
        enabled = (not running) and self.mixture_mode() == "mixture"
        self._rerun.setEnabled(enabled)
        for w in self._param_widgets():
            w.setEnabled(enabled)
        self._rb_none.setEnabled(not running)
        self._rb_mixture.setEnabled(not running)

    def set_rerun_enabled(self, enabled: bool) -> None:
        self._rerun.setEnabled(bool(enabled) and self.mixture_mode() == "mixture")

    def set_status(self, text: str, *, poor: bool = False) -> None:
        self._lbl_status.setText(text or "—")
        apply_quality_hint_style(self._lbl_status, poor=bool(poor) and bool(text) and text != "—")

    def set_fit_models(
        self,
        *,
        labels: list[str],
        rows_by_label: dict[str, dict[str, Any]],
        fit_paths: dict[str, str],
        best_label: str = "",
    ) -> None:
        self._rows_by_label = dict(rows_by_label or {})
        self._fit_paths = dict(fit_paths or {})
        self._best_label = (best_label or "").strip()
        self._fit_combo.blockSignals(True)
        try:
            self._fit_combo.clear()
            for lab in labels:
                self._fit_combo.addItem(lab)
            if self._best_label and self._best_label in labels:
                self._fit_combo.setCurrentText(self._best_label)
            elif labels:
                self._fit_combo.setCurrentIndex(0)
        finally:
            self._fit_combo.blockSignals(False)
        self._refresh_selected_views()

    def selected_label(self) -> str:
        return self._fit_combo.currentText().strip()

    def _on_fit_combo(self, *_args) -> None:
        lab = self.selected_label()
        self._refresh_selected_views()
        if lab:
            self.fit_selection_changed.emit(lab)

    def _refresh_selected_views(self) -> None:
        lab = self.selected_label()
        if not lab:
            self.clear_view()
            return
        fit_path = self._fit_paths.get(lab, "")
        if fit_path:
            self._fit_plot.plot_from_fit(fit_path, label=lab)
        else:
            self._fit_plot.clear_plot()
        row = self._rows_by_label.get(lab) or {}
        params = self.mixture_params()
        r_max_nm = float(params.get("r_max") or 0.0)
        if r_max_nm <= 0.0:
            r_max_nm = 12.0
        self._dist_plot.plot_from_model_row(
            row,
            r_min_ang=_DEFAULT_R_MIN_NM * 10.0,
            r_max_ang=r_max_nm * 10.0,
            label=lab,
        )
        best = self._best_label
        lines = [f"Selected: {lab}" + (" (best)" if best and lab == best else (f" (best={best})" if best else ""))]
        chi2 = row.get("chi2")
        bic_chi2 = row.get("BIC_chi2")
        if chi2 is not None and str(chi2).strip() != "":
            lines.append(f"χ² = {format_display_number(chi2)}")
        if bic_chi2 is not None and str(bic_chi2).strip() != "":
            lines.append(f"BIC_χ² = {format_display_number(bic_chi2)}")
        self.set_status("\n".join(lines))

    def clear_view(self) -> None:
        self._fit_plot.clear_plot()
        self._dist_plot.clear_plot()
        self._fit_combo.blockSignals(True)
        try:
            self._fit_combo.clear()
        finally:
            self._fit_combo.blockSignals(False)
        self._rows_by_label = {}
        self._fit_paths = {}
        self._best_label = ""
        self.set_status("—")

    @property
    def fit_plot(self) -> MixtureFitPlot:
        return self._fit_plot

    @property
    def dist_plot(self) -> MixtureDistPlot:
        return self._dist_plot
