"""Dedicated Guinier adjust wizard (live in-process fixed-interval preview + Confirm re-run)."""

from __future__ import annotations

import os
from typing import Any, Dict, Mapping, Optional

import numpy as np
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from autosaxs.core.utils import ensure_q_nm, load_saxs_1d_any
from autosaxs.skill.fit_guinier.guinier import run_fixed_interval_guinier

from ....ui.widgets.spin_sliders import PointIndexSpinSlider
from ..panels.right.guinier_residuals_plot import GuinierResidualsPlot
from ..panels.right.monodisperse.format_display import format_guinier_passport_rows
from ..panels.right.monodisperse.plots import GuinierCurvePlot
from ..panels.right.passport_table import PassportTableWidget
from ..widgets.plots import mpl_navigation_toolbar
from .adjust_confirm import AdjustConfirmController

_PREVIEW_DEBOUNCE_MS = 100
_Q_SLIDER_CAP_NM = 2.0


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


class GuinierAdjustWizardDialog(QDialog):
    """
    Interactive Guinier refine wizard.

    Left: Guinier fit (top, ~2× taller); residuals (bottom).
    Right: first/last spin+sliders, Restore auto, Confirm, passport table.
    """

    params_changed = pyqtSignal()
    editing_started = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Guinier adjust")
        self.setWindowFlags(
            Qt.Window
            | Qt.CustomizeWindowHint
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowCloseButtonHint
            | Qt.WindowMinMaxButtonsHint
        )
        self.setSizeGripEnabled(True)
        try:
            scr = QGuiApplication.primaryScreen()
            geo = scr.availableGeometry() if scr is not None else None
            if geo is not None:
                w = max(900, int(0.68 * int(geo.width())))
                h = max(640, int(0.78 * int(geo.height())))
                self.resize(w, h)
                self.setMinimumSize(800, 560)
        except Exception:
            self.setMinimumWidth(860)
            self.resize(1000, 700)

        self._profile_path: str = ""
        self._q_nm: Optional[np.ndarray] = None
        self._I: Optional[np.ndarray] = None
        self._sigma: Optional[np.ndarray] = None
        self._auto_snapshot: Dict[str, Any] = {}
        self._results_path: str = ""
        self._pause_emitted = False
        self._block_params = False
        self._fit_dlg: Optional[_MplViewerDialog] = None
        self._resid_dlg: Optional[_MplViewerDialog] = None

        self._fit_plot = GuinierCurvePlot(figsize=(4.5, 3.2))
        self._resid_plot = GuinierResidualsPlot(figsize=(4.0, 2.2))
        for p in (self._fit_plot, self._resid_plot):
            p.setMinimumHeight(120)
            p.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        left = QVBoxLayout()
        left.setSpacing(8)
        fit_box = QGroupBox("Guinier fit")
        fit_lay = QVBoxLayout(fit_box)
        fit_lay.setContentsMargins(6, 8, 6, 6)
        fit_lay.addWidget(self._fit_plot, 1)
        resid_box = QGroupBox("ΔI")
        resid_lay = QVBoxLayout(resid_box)
        resid_lay.setContentsMargins(6, 8, 6, 6)
        resid_lay.addWidget(self._resid_plot, 1)
        left.addWidget(fit_box, 2)
        left.addWidget(resid_box, 1)

        self._first = PointIndexSpinSlider()
        self._last = PointIndexSpinSlider()
        self._lbl_q_first = QLabel("q = —")
        self._lbl_q_last = QLabel("q = —")
        self._btn_restore = QPushButton("Restore auto")
        self._btn_restore.clicked.connect(self._on_restore_auto)
        self._confirm = AdjustConfirmController(
            self,
            get_current_params=self.guinier_params,
            on_confirm=self._on_confirm,
            warning_title="Unconfirmed Guinier changes",
            warning_text=(
                "You have unconfirmed Guinier interval changes. "
                "Close without writing them to disk / re-running fit_guinier?"
            ),
        )
        self._passport_table = PassportTableWidget()

        first_row = QHBoxLayout()
        first_row.addWidget(self._first, 1)
        first_row.addWidget(self._lbl_q_first, 0)
        last_row = QHBoxLayout()
        last_row.addWidget(self._last, 1)
        last_row.addWidget(self._lbl_q_last, 0)

        form = QFormLayout()
        form.addRow("first", first_row)
        form.addRow("last", last_row)

        passport_col = QVBoxLayout()
        passport_col.setContentsMargins(0, 0, 0, 0)
        passport_col.setSpacing(4)
        passport_col.addWidget(QLabel("Passport"), 0, Qt.AlignTop)
        passport_col.addWidget(self._passport_table, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addLayout(form)
        right.addWidget(self._btn_restore)
        right.addWidget(self._confirm.button)
        right.addLayout(passport_col, 1)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addLayout(left, 3)
        body.addLayout(right, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(
            QLabel(
                "Adjust Guinier first/last for the current curve. "
                "Plots and passport update live; press Confirm to write parameters and re-run. "
                "Click a plot to open an enlarged viewer."
            )
        )
        lay.addLayout(body, 1)

        self._preview_debounce = QTimer(self)
        self._preview_debounce.setSingleShot(True)
        self._preview_debounce.setInterval(_PREVIEW_DEBOUNCE_MS)
        self._preview_debounce.timeout.connect(self._run_preview)
        self._first.valueChanged.connect(self._schedule_preview)
        self._last.valueChanged.connect(self._schedule_preview)

        self._fit_plot.mpl_connect("button_press_event", lambda ev: self._on_plot_click(ev, "fit"))
        self._resid_plot.mpl_connect("button_press_event", lambda ev: self._on_plot_click(ev, "resid"))

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._confirm.confirm_close_if_dirty():
            event.accept()
        else:
            event.ignore()

    def reject(self) -> None:
        if self._confirm.confirm_close_if_dirty():
            super().reject()

    def set_running(self, running: bool) -> None:
        if running:
            self._preview_debounce.stop()
        enabled = not running
        self._first.setEnabled(enabled)
        self._last.setEnabled(enabled)
        self._btn_restore.setEnabled(enabled)
        self._confirm.set_controls_enabled(enabled)

    def set_context(
        self,
        *,
        profile_path: str,
        working_params: Optional[Mapping[str, Any]] = None,
        auto_snapshot: Optional[Mapping[str, Any]] = None,
        results_path: str = "",
    ) -> None:
        self._profile_path = str(profile_path or "").strip()
        self._results_path = str(results_path or "").strip()
        self._auto_snapshot = dict(auto_snapshot or {})
        self._q_nm = None
        self._I = None
        self._sigma = None
        self._pause_emitted = False
        self._ensure_profile()
        params = dict(working_params or {})
        if not params and self._auto_snapshot:
            params = dict(self._auto_snapshot)
        self._apply_slider_spans()
        self.set_params(params, emit=False)
        self._confirm.set_committed(self.guinier_params())
        self._run_preview()

    def guinier_params(self) -> Dict[str, Any]:
        first = int(self._first.value())
        last = int(self._last.value())
        if last < first:
            last = first
        return {"first": first, "last": last}

    def set_params(self, params: Mapping[str, Any], *, emit: bool = False) -> None:
        self._block_params = True
        try:
            first = params.get("first")
            last = params.get("last")
            if first is None:
                first = params.get("first_point_1based")
            if last is None:
                last = params.get("last_point_1based")
            if first is not None:
                self._first.setValue(max(1, int(first)))
            if last is not None:
                self._last.setValue(max(1, int(last)))
            if self._first.value() > 0 and self._last.value() < self._first.value():
                self._last.setValue(self._first.value())
            self._update_q_labels()
        finally:
            self._block_params = False
        if emit:
            self._schedule_preview()

    def _ensure_profile(self) -> bool:
        if self._q_nm is not None and self._I is not None:
            return True
        prof = self._profile_path
        if not prof or not os.path.isfile(prof):
            return False
        try:
            q, I, sigma = load_saxs_1d_any(prof)
            q, I, sigma = ensure_q_nm(q, I, sigma)
            self._q_nm = np.asarray(q, dtype=float)
            self._I = np.asarray(I, dtype=float)
            self._sigma = None if sigma is None else np.asarray(sigma, dtype=float)
            n = int(self._q_nm.size)
            self._first.set_spin_maximum(max(1, n))
            self._last.set_spin_maximum(max(1, n))
            return True
        except Exception:
            return False

    def _last_index_q_le(self, q_cap: float = _Q_SLIDER_CAP_NM) -> int:
        if self._q_nm is None or self._q_nm.size == 0:
            return 100
        q = np.asarray(self._q_nm, dtype=float)
        m = np.isfinite(q) & (q <= float(q_cap))
        if not m.any():
            return int(q.size)
        return int(np.where(m)[0][-1]) + 1  # 1-based

    def _apply_slider_spans(self) -> None:
        if not self._ensure_profile():
            self._first.set_slider_span(min_i=1, max_i=100)
            self._last.set_slider_span(min_i=1, max_i=100)
            return
        hi = self._last_index_q_le(_Q_SLIDER_CAP_NM)
        n = int(self._q_nm.size) if self._q_nm is not None else hi
        hi = max(1, min(hi, n))
        self._first.set_slider_span(min_i=1, max_i=hi)
        self._last.set_slider_span(min_i=1, max_i=hi)

    def _point_q(self, point_1based: int) -> Optional[float]:
        if self._q_nm is None:
            return None
        i = int(point_1based) - 1
        if i < 0 or i >= self._q_nm.size:
            return None
        v = float(self._q_nm[i])
        return v if np.isfinite(v) else None

    def _update_q_labels(self) -> None:
        qf = self._point_q(self._first.value())
        ql = self._point_q(self._last.value())
        self._lbl_q_first.setText(f"q = {qf:.4g}" if qf is not None else "q = —")
        self._lbl_q_last.setText(f"q = {ql:.4g}" if ql is not None else "q = —")

    def _schedule_preview(self, *_a) -> None:
        if self._block_params:
            return
        if not self._pause_emitted:
            self._pause_emitted = True
            self.editing_started.emit()
        # Keep last >= first.
        if self._last.value() < self._first.value():
            self._block_params = True
            try:
                self._last.setValue(self._first.value())
            finally:
                self._block_params = False
        self._update_q_labels()
        self._confirm.refresh()
        self._preview_debounce.start()

    def _on_restore_auto(self) -> None:
        snap = dict(self._auto_snapshot or {})
        if not snap:
            self._passport_table.set_message("No auto Guinier interval to restore.", poor=True)
            return
        self.set_params(snap, emit=True)

    def _on_confirm(self) -> None:
        self.params_changed.emit()

    def _passport_from_fixed(self, out: Mapping[str, Any], *, first: int, last: int) -> Dict[str, Any]:
        interval = out.get("interval") if isinstance(out.get("interval"), dict) else {}
        ch = out.get("chosen_interval")
        q_min = ch[0] if isinstance(ch, (tuple, list)) and len(ch) >= 2 else interval.get("guinier_interval", (None, None))[0]
        q_max = ch[1] if isinstance(ch, (tuple, list)) and len(ch) >= 2 else None
        if isinstance(interval.get("guinier_interval"), (tuple, list)) and len(interval["guinier_interval"]) >= 2:
            q_min = interval["guinier_interval"][0]
            q_max = interval["guinier_interval"][1]
        rg = out.get("chosen_Rg")
        if rg is None:
            rg = interval.get("Rg")
        i0 = out.get("chosen_I0")
        if i0 is None:
            i0 = interval.get("I0")
        qrg = interval.get("qrg")
        if qrg is None and q_max is not None and rg is not None:
            try:
                qrg = float(q_max) * float(rg)
            except (TypeError, ValueError):
                qrg = None
        return {
            "rg": rg,
            "i0": i0,
            "q_min": q_min,
            "q_max": q_max,
            "qrg": qrg,
            "n_points": out.get("chosen_n_points") or interval.get("n_points"),
            "interval_r2": interval.get("interval_r2"),
            "validation_r2": out.get("chosen_validation_r2") or interval.get("validation_r2"),
            "quality_class": out.get("quality_class"),
            "classification": out.get("classification"),
            "selection_mode": out.get("selection_mode") or "fixed_interval",
            "first_point_1based": first,
            "last_point_1based": last,
            "source": "interval",
            "_results_path": self._results_path,
            "_click_viewer": "guinier_fit",
        }

    def _run_preview(self) -> None:
        if not self._ensure_profile() or self._q_nm is None or self._I is None:
            self._passport_table.set_message("No profile available for preview.", poor=True)
            return
        params = self.guinier_params()
        first = int(params["first"])
        last = int(params["last"])
        try:
            out = run_fixed_interval_guinier(
                self._q_nm,
                self._I,
                self._sigma,
                first_point_1based=first,
                last_point_1based=last,
            )
        except Exception as exc:
            self._passport_table.set_message(f"Guinier preview failed: {exc}", poor=True)
            return
        if out.get("chosen") is None or out.get("chosen_Rg") is None:
            self._passport_table.set_message("Guinier fit failed for this interval.", poor=True)
            return
        data = self._passport_from_fixed(out, first=first, last=last)
        try:
            self._fit_plot.plot_from_profile_and_data(
                self._profile_path,
                data,
                q=self._q_nm,
                I=self._I,
            )
        except Exception:
            pass
        try:
            self._resid_plot.plot_from_arrays(
                self._q_nm,
                self._I,
                self._sigma,
                rg=float(data["rg"]),
                i0=float(data["i0"]),
                first_point_1based=first,
                last_point_1based=last,
                click_path=self._profile_path,
            )
        except Exception:
            pass
        self._passport_table.set_rows(format_guinier_passport_rows(data))
        if self._fit_dlg is not None and self._fit_dlg.isVisible():
            self._fit_plot.replay_last(self._fit_dlg.plot)
        if self._resid_dlg is not None and self._resid_dlg.isVisible() and self._resid_plot.last_payload:
            self._resid_dlg.plot.plot_from_payload(self._resid_plot.last_payload, click_path=self._profile_path)

    def _on_plot_click(self, ev: object, which: str) -> None:
        if getattr(ev, "inaxes", None) is None:
            return
        if int(getattr(ev, "button", 0)) != 1:
            return
        if which == "fit":
            if self._fit_dlg is None:
                self._fit_dlg = _MplViewerDialog(
                    title="Guinier fit",
                    plot=GuinierCurvePlot(figsize=(5.0, 3.5)),
                    parent=self,
                )
            if self._fit_plot.replay_last(self._fit_dlg.plot):
                self._fit_dlg.show()
                self._fit_dlg.raise_()
                self._fit_dlg.activateWindow()
            return
        if which == "resid":
            payload = self._resid_plot.last_payload
            if not payload:
                return
            if self._resid_dlg is None:
                self._resid_dlg = _MplViewerDialog(
                    title="ΔI",
                    plot=GuinierResidualsPlot(figsize=(5.0, 3.5)),
                    parent=self,
                )
            self._resid_dlg.plot.plot_from_payload(payload, click_path=self._profile_path)
            self._resid_dlg.show()
            self._resid_dlg.raise_()
            self._resid_dlg.activateWindow()
