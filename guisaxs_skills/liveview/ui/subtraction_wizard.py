from __future__ import annotations

import os
from typing import Any, Dict, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from .plots import LogCurvePlot


class SubtractionWizardDialog(QDialog):
    """
    One-off subtraction scaling wizard for the currently displayed file (State C/CD).

    - Changing the spinbox updates only previews (no writes).
    - Pressing Apply triggers a real autosaxs subtract run (overwrites sub_<stem>.dat) and may rerun analysis.
    """

    preview_scale_changed = pyqtSignal(float)
    apply_requested = pyqtSignal(float)

    def __init__(
        self,
        *,
        sample_dat: str,
        buffer_dat: str,
        subtracted_dat: str,
        subtract_options: Optional[Dict[str, Any]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Subtraction wizard")
        self.setMinimumWidth(820)
        self.resize(1100, 700)

        self._sample_dat = (sample_dat or "").strip()
        self._buffer_dat = (buffer_dat or "").strip()
        self._subtracted_dat = (subtracted_dat or "").strip()
        self._subtract_options = dict(subtract_options or {})

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)

        # Main view: two plots (like state-C middle bottom row).
        plots_row = QHBoxLayout()
        left_col = QVBoxLayout()
        left_col.addWidget(QLabel("S + buffer"), 0)
        self._compare_plot = LogCurvePlot()
        left_col.addWidget(self._compare_plot, 1)
        right_col = QVBoxLayout()
        right_col.addWidget(QLabel("Sub"), 0)
        self._sub_plot = LogCurvePlot()
        right_col.addWidget(self._sub_plot, 1)
        plots_row.addLayout(left_col, 1)
        plots_row.addLayout(right_col, 1)
        lay.addLayout(plots_row, 1)

        # Controls at the bottom.
        row = QHBoxLayout()
        row.addWidget(QLabel("Scaling factor"), 0, Qt.AlignLeft)
        self._scale = QDoubleSpinBox()
        self._scale.setDecimals(8)
        self._scale.setMinimum(1e-300)  # positive only, no practical cap
        self._scale.setMaximum(1e300)
        self._scale.setKeyboardTracking(False)  # avoid spam while typing
        self._scale.setSingleStep(0.01)
        self._scale.setValue(1.0)
        self._scale.valueChanged.connect(self._on_scale_changed)
        row.addWidget(self._scale, 1)
        self._apply = QPushButton("Apply")
        self._apply.clicked.connect(self._on_apply_clicked)
        self._close = QPushButton("Close")
        self._close.clicked.connect(self.close)
        row.addWidget(self._apply, 0)
        row.addWidget(self._close, 0)
        lay.addLayout(row, 0)

        self._seed_initial_scale()
        self._validate_paths_and_update_ui()
        self._refresh_plots()

    def set_running(self, running: bool) -> None:
        r = bool(running)
        self._apply.setEnabled(not r)
        self._close.setEnabled(not r)
        self._scale.setEnabled(not r)
        if r:
            self.setWindowTitle("Subtraction wizard — running…")
        else:
            self.setWindowTitle("Subtraction wizard")

    def current_paths(self) -> tuple[str, str, str]:
        return self._sample_dat, self._buffer_dat, self._subtracted_dat

    def set_scale_value(self, value: float) -> None:
        self._scale.setValue(float(value))

    def scale_value(self) -> float:
        return float(self._scale.value())

    def _validate_paths_and_update_ui(self) -> None:
        ok = True
        if not self._sample_dat or not os.path.isfile(self._sample_dat):
            ok = False
        if not self._buffer_dat or not os.path.isfile(self._buffer_dat):
            ok = False
        self._apply.setEnabled(ok)
        if not ok:
            self._compare_plot.clear()
            self._sub_plot.clear()
            if self._compare_plot.figure.axes:
                self._compare_plot.figure.axes[0].set_title("Missing sample/buffer curves")
            if self._sub_plot.figure.axes:
                self._sub_plot.figure.axes[0].set_title("Missing sample/buffer curves")
            self._compare_plot.draw_idle()
            self._sub_plot.draw_idle()
            return

    def _seed_initial_scale(self) -> None:
        """
        Start at the automatically computed scale for the displayed file, if available.
        Prefer reading it from the existing subtracted .dat metadata; otherwise, keep 1.0.
        """
        p = self._subtracted_dat
        if not p or not os.path.isfile(p):
            return
        try:
            from autosaxs.core.utils import read_saxs

            _q, _I, _sig, meta = read_saxs(p)
            if isinstance(meta, dict):
                subm = meta.get("subtract")
                if isinstance(subm, dict):
                    sf = subm.get("scaling_factor")
                    if sf is not None:
                        v = float(sf)
                        if v > 0.0:
                            self._scale.blockSignals(True)
                            self._scale.setValue(v)
                            self._scale.blockSignals(False)
                            self._set_step_relative()
        except Exception:
            return

    def _set_step_relative(self) -> None:
        v = float(self._scale.value())
        step = abs(v) * 0.01
        if step <= 0.0 or not (step == step):  # NaN-safe
            step = 0.01
        self._scale.setSingleStep(step)

    def _on_scale_changed(self, _val: float) -> None:
        self._set_step_relative()
        self._refresh_plots()
        self.preview_scale_changed.emit(float(self._scale.value()))

    def _on_apply_clicked(self) -> None:
        if float(self._scale.value()) <= 0.0:
            QMessageBox.warning(self, "Scaling factor", "Scaling factor must be positive.")
            return
        self.apply_requested.emit(float(self._scale.value()))

    def _refresh_plots(self) -> None:
        sp = self._sample_dat
        bp = self._buffer_dat
        if not sp or not bp or not os.path.isfile(sp) or not os.path.isfile(bp):
            return
        scale = float(self._scale.value())
        self._compare_plot.plot_sample_and_scaled_buffer_manual(sp, bp, scaling_factor=scale)
        self._sub_plot.plot_subtracted_preview_manual(sp, bp, scaling_factor=scale)

