"""Dedicated D(R) / GNOM adjust wizard (live in-process GNOM preview + manual refine)."""

from __future__ import annotations

import html
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import numpy as np
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from autosaxs.core.atsas_gnom import normalize_force_zero, run_gnom
from autosaxs.core.gnom import parse_gnom_out
from autosaxs.core.gnom_quality import analyze_dr_quality
from autosaxs.core.utils import ensure_q_nm, load_saxs_1d_any, write_saxs_atsas_format

from ....ui.style import COLOR_QUALITY_POOR
from ....ui.widgets.spin_sliders import AlphaSpinSlider, LengthNmSpinSlider
from ..panels.right.polydisperse.format_display import format_sizes_passport_html
from ..panels.right.polydisperse.plots import DrPlot, GnomFitPlot
from .adjust_confirm import AdjustConfirmController
from .q_fit_bounds import first_last_from_q_values, make_q_max_spin, make_q_min_spin, q_bounds_from_params

_PREVIEW_DEBOUNCE_MS = 100


class SizesAdjustWizardDialog(QDialog):
    """
    Interactive polydisperse GNOM (D(R)) refine wizard.

    Left: D(R) (top) and I(q) fit (bottom).
    Right: q-min/q-max/rmin/rmax/alpha/boundary checkboxes, Restore auto, passport.
    """

    params_changed = pyqtSignal()
    editing_started = pyqtSignal()
    restore_auto_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("D(R) / GNOM adjust")
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
                w = max(960, int(0.72 * int(geo.width())))
                h = max(700, int(0.82 * int(geo.height())))
                self.resize(w, h)
                self.setMinimumSize(860, 620)
        except Exception:
            self.setMinimumWidth(900)
            self.resize(1100, 760)

        self._profile_path: str = ""
        self._atsas_dat_path: str = ""
        self._q_nm: Optional[np.ndarray] = None
        self._auto_snapshot: Dict[str, Any] = {}
        self._auto_gnom_out: str = ""
        self._disk_best_gnom_out: str = ""
        self._preview_tmp: Optional[str] = None
        self._pause_emitted = False
        self._block_params = False

        self._dr_plot = DrPlot(figsize=(4.2, 2.8))
        self._fit_plot = GnomFitPlot(figsize=(4.2, 2.8))
        for p in (self._dr_plot, self._fit_plot):
            p.setMinimumHeight(160)
            p.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        left = QVBoxLayout()
        left.setSpacing(8)
        dr_box = QGroupBox("D(R)")
        dr_lay = QVBoxLayout(dr_box)
        dr_lay.setContentsMargins(6, 8, 6, 6)
        dr_lay.addWidget(self._dr_plot, 1)
        iq_box = QGroupBox("I(q) fit")
        iq_lay = QVBoxLayout(iq_box)
        iq_lay.setContentsMargins(6, 8, 6, 6)
        iq_lay.addWidget(self._fit_plot, 1)
        left.addWidget(dr_box, 1)
        left.addWidget(iq_box, 1)

        self._q_min = make_q_min_spin()
        self._q_max = make_q_max_spin()
        self._rmin = QDoubleSpinBox()
        self._rmin.setDecimals(4)
        self._rmin.setRange(0.0, 1e6)
        self._rmin.setSpecialValueText("(auto)")
        self._rmin.setValue(0.0)
        self._rmax = LengthNmSpinSlider()
        self._alpha = AlphaSpinSlider()
        self._d0 = QCheckBox("D(0) = 0")
        self._d0.setChecked(True)
        self._drmax = QCheckBox("D(Rmax) = 0")
        self._drmax.setChecked(True)
        self._btn_restore = QPushButton("Restore auto")
        self._btn_restore.clicked.connect(self._on_restore_auto)
        self._confirm = AdjustConfirmController(
            self,
            get_current_params=self.sizes_params,
            on_confirm=self._on_confirm,
            warning_title="Unconfirmed D(R) changes",
            warning_text=(
                "You have unconfirmed GNOM parameter changes. "
                "Close without writing them to disk / re-running fit_sizes?"
            ),
        )
        self._lbl_passport_title = QLabel("Passport")
        self._lbl_passport_title.setContentsMargins(0, 0, 0, 0)
        self._lbl_passport = QLabel("—")
        self._lbl_passport.setWordWrap(True)
        self._lbl_passport.setTextFormat(Qt.RichText)
        self._lbl_passport.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._lbl_passport.setContentsMargins(0, 0, 0, 0)

        form = QFormLayout()
        form.addRow("q-min (nm⁻¹)", self._q_min)
        form.addRow("q-max (nm⁻¹)", self._q_max)
        form.addRow("rmin (nm)", self._rmin)
        form.addRow("rmax (nm)", self._rmax)
        form.addRow("alpha", self._alpha)

        passport_col = QVBoxLayout()
        passport_col.setContentsMargins(0, 0, 0, 0)
        passport_col.setSpacing(0)
        passport_col.addWidget(self._lbl_passport_title, 0, Qt.AlignTop)
        passport_col.addWidget(self._lbl_passport, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addLayout(form)
        right.addWidget(self._d0)
        right.addWidget(self._drmax)
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
                "Adjust GNOM parameters for the current curve. "
                "Plots and passport update live; press Confirm to write parameters and re-run fit_sizes."
            )
        )
        lay.addLayout(body, 1)

        self._preview_debounce = QTimer(self)
        self._preview_debounce.setSingleShot(True)
        self._preview_debounce.setInterval(_PREVIEW_DEBOUNCE_MS)
        self._preview_debounce.timeout.connect(self._run_preview)
        self._q_min.valueChanged.connect(self._schedule_preview)
        self._q_max.valueChanged.connect(self._schedule_preview)
        self._rmin.valueChanged.connect(self._schedule_preview)
        self._rmax.valueChanged.connect(self._schedule_preview)
        self._alpha.valueChanged.connect(self._schedule_preview)
        self._d0.toggled.connect(self._schedule_preview)
        self._drmax.toggled.connect(self._schedule_preview)

    def set_running(self, running: bool) -> None:
        if running:
            self._preview_debounce.stop()
        enabled = not running
        for w in (
            self._q_min,
            self._q_max,
            self._rmin,
            self._rmax,
            self._alpha,
            self._d0,
            self._drmax,
            self._btn_restore,
        ):
            w.setEnabled(enabled)
        self._confirm.set_controls_enabled(enabled)

    def _plot_dr_adjust(self, path: str) -> None:
        disk_best = self._disk_best_gnom_out if self._disk_best_gnom_out and os.path.isfile(self._disk_best_gnom_out) else None
        if disk_best and os.path.abspath(disk_best) == os.path.abspath(path):
            disk_best = None
        self._dr_plot.plot_from_gnom_out(
            path,
            close_fits=True,
            force_zero_off=True,
            overlay_gnom_out=disk_best,
        )

    def set_context(
        self,
        *,
        profile_path: str,
        auto_snapshot: Optional[Mapping[str, Any]] = None,
        working_params: Optional[Mapping[str, Any]] = None,
        gnom_out_path: str = "",
        passport_html: str = "",
        passport_text: str = "",
    ) -> None:
        self._profile_path = str(profile_path or "").strip()
        self._auto_snapshot = dict(auto_snapshot or {})
        self._auto_gnom_out = str(gnom_out_path or "").strip()
        self._disk_best_gnom_out = self._auto_gnom_out
        self._atsas_dat_path = ""
        self._q_nm = None
        self._pause_emitted = False
        self._ensure_atsas_dat()
        params = dict(working_params or {})
        if not params and self._auto_snapshot:
            params = dict(self._auto_snapshot)
        self._apply_rmax_slider_span()
        self.set_params(params, emit=False)
        if (not params.get("rmax_nm")) and gnom_out_path and os.path.isfile(gnom_out_path):
            try:
                parsed = parse_gnom_out(Path(gnom_out_path).read_text(errors="replace"))
                fill: Dict[str, Any] = {}
                if parsed.get("real_space_rmax") is not None:
                    fill["rmax_nm"] = float(parsed["real_space_rmax"])
                if parsed.get("current_alpha") is not None:
                    fill["alpha"] = float(parsed["current_alpha"])
                if fill:
                    merged = dict(params)
                    for k, v in fill.items():
                        if merged.get(k) is None:
                            merged[k] = v
                    self.set_params(merged, emit=False)
            except Exception:
                pass
        self._apply_rmax_slider_span()
        if gnom_out_path and os.path.isfile(gnom_out_path):
            self._fit_plot.plot_from_dat_and_gnom_out(self._profile_path, gnom_out_path)
            self._plot_dr_adjust(gnom_out_path)
        if passport_html:
            self.set_passport(html_text=passport_html)
        elif passport_text:
            self._lbl_passport.setTextFormat(Qt.PlainText)
            self._lbl_passport.setText(passport_text)
            self._lbl_passport.setStyleSheet("")
        self._confirm.set_committed(self.sizes_params())
        # Refresh I(q)/D(R) for the loaded q-min/q-max (disk .out may differ).
        self._run_preview()

    def _apply_rmax_slider_span(self) -> None:
        """Slider domain 0 … 4× best-auto Rmax (fallback: current Rmax); spin can exceed."""
        auto_rmax = None
        for src in (
            (self._auto_snapshot or {}).get("rmax_nm"),
            float(self._rmax.value()) if float(self._rmax.value()) > 0 else None,
        ):
            try:
                v = float(src) if src is not None else None
            except (TypeError, ValueError):
                v = None
            if v is not None and v > 0:
                auto_rmax = v
                break
        if auto_rmax is None:
            self._rmax.set_slider_span(min_nm=0.0, max_nm=1000.0)
            return
        self._rmax.set_slider_span(min_nm=0.0, max_nm=4.0 * auto_rmax)

    def set_params(self, params: Mapping[str, Any], *, emit: bool = False) -> None:
        if not isinstance(params, dict) and not isinstance(params, Mapping):
            return
        self._preview_debounce.stop()
        self._block_params = True
        widgets = (self._q_min, self._q_max, self._rmin, self._rmax, self._alpha, self._d0, self._drmax)
        for w in widgets:
            w.blockSignals(True)
        try:
            q_lo, q_hi = q_bounds_from_params(params, self._q_nm)
            if q_lo is not None:
                self._q_min.setValue(float(q_lo))
            if q_hi is not None:
                self._q_max.setValue(float(q_hi))
            elif "q_max" in params and params.get("q_max") is None:
                self._q_max.setValue(0.0)
            elif "last" in params and params.get("last") is None and params.get("q_max") is None:
                self._q_max.setValue(0.0)
            if params.get("rmin_nm") is not None:
                try:
                    self._rmin.setValue(max(0.0, float(params["rmin_nm"])))
                except (TypeError, ValueError):
                    self._rmin.setValue(0.0)
            elif "rmin_nm" in params and params.get("rmin_nm") is None:
                self._rmin.setValue(0.0)
            if params.get("rmax_nm") is not None:
                self._rmax.setValue(float(params["rmax_nm"]))
            if params.get("alpha") is not None:
                try:
                    a = float(params["alpha"])
                    self._alpha.setValue(a if a > 0 else 0.0)
                except (TypeError, ValueError):
                    self._alpha.setValue(0.0)
            elif "alpha" in params and params.get("alpha") is None:
                self._alpha.setValue(0.0)
            if "force_zero_rmin" in params:
                self._d0.setChecked(normalize_force_zero(params.get("force_zero_rmin")) == "Y")
            if "force_zero_rmax" in params:
                self._drmax.setChecked(normalize_force_zero(params.get("force_zero_rmax")) == "Y")
        finally:
            for w in widgets:
                w.blockSignals(False)
            self._block_params = False
        if emit:
            self._schedule_preview()

    def sizes_params(self) -> dict:
        out: dict = {
            "q_min": float(self._q_min.value()),
            "rmax_nm": float(self._rmax.value()),
            "force_zero_rmin": "Y" if self._d0.isChecked() else "N",
            "force_zero_rmax": "Y" if self._drmax.isChecked() else "N",
        }
        q_max = float(self._q_max.value())
        if q_max > 0.0:
            out["q_max"] = q_max
        rmin = float(self._rmin.value())
        if rmin > 0.0:
            out["rmin_nm"] = rmin
        alpha = float(self._alpha.value())
        if alpha > 0.0:
            out["alpha"] = alpha
        return out

    def set_passport(self, *, text: str = "", poor: bool = False, html_text: str = "") -> None:
        _ = poor
        if html_text:
            self._lbl_passport.setTextFormat(Qt.RichText)
            self._lbl_passport.setText(html_text)
            self._lbl_passport.setStyleSheet("")
            return
        self._lbl_passport.setTextFormat(Qt.PlainText)
        self._lbl_passport.setText(text or "—")
        self._lbl_passport.setStyleSheet("")

    def set_passport_from_quality(self, quality: Mapping[str, Any]) -> None:
        html_body = format_sizes_passport_html(quality, poor_color=COLOR_QUALITY_POOR)
        self.set_passport(html_text=html_body)

    def show_from_gnom_out(self, gnom_out_path: str) -> None:
        if gnom_out_path and os.path.isfile(gnom_out_path):
            self._disk_best_gnom_out = gnom_out_path
            self._auto_gnom_out = gnom_out_path
            self._fit_plot.plot_from_dat_and_gnom_out(self._profile_path, gnom_out_path)
            self._plot_dr_adjust(gnom_out_path)

    def _on_restore_auto(self) -> None:
        if not self._auto_snapshot:
            return
        self.set_params(self._auto_snapshot, emit=True)
        self.restore_auto_requested.emit()

    def _schedule_preview(self, *_args) -> None:
        if self._block_params:
            return
        if not self._pause_emitted:
            self._pause_emitted = True
            self.editing_started.emit()
        self._confirm.refresh()
        self._preview_debounce.start()

    def _on_confirm(self) -> None:
        self.params_changed.emit()

    def _ensure_atsas_dat(self) -> bool:
        if self._atsas_dat_path and os.path.isfile(self._atsas_dat_path) and self._q_nm is not None:
            return True
        prof = self._profile_path
        if not prof or not os.path.isfile(prof):
            return False
        try:
            q_nm, I, sigma = load_saxs_1d_any(prof)
            q_nm, I, sigma = ensure_q_nm(q_nm, I, sigma)
            q_nm = np.asarray(q_nm, dtype=float)
            self._q_nm = q_nm
            fd, tmp = tempfile.mkstemp(suffix="_atsas.dat", prefix="sizes_wiz_")
            os.close(fd)
            write_saxs_atsas_format(tmp, q_nm, I, sigma)
            if self._preview_tmp and os.path.isfile(self._preview_tmp):
                try:
                    os.remove(self._preview_tmp)
                except OSError:
                    pass
            self._preview_tmp = tmp
            self._atsas_dat_path = tmp
            return True
        except Exception:
            return False

    def _run_preview(self) -> None:
        if not self._ensure_atsas_dat() or self._q_nm is None:
            self.set_passport(text="No profile available for preview.", poor=True)
            return
        params = self.sizes_params()
        rmax = float(params["rmax_nm"])
        try:
            first, last = first_last_from_q_values(
                self._q_nm,
                q_min=params.get("q_min"),
                q_max=params.get("q_max"),
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            msg = html.escape(f"Invalid q-min/q-max: {exc}")
            self.set_passport(html_text=f'<span style="color:{COLOR_QUALITY_POOR}">{msg}</span>')
            return
        alpha = params.get("alpha")
        rmin = params.get("rmin_nm")
        out_path = (self._preview_tmp or "") + ".out"
        ok, _rc, stderr, out_text = run_gnom(
            atsas_dat_path=self._atsas_dat_path,
            output_dir=str(Path(out_path).parent),
            rmax_nm=rmax,
            out_path=out_path,
            system=1,
            rmin_nm=float(rmin) if rmin is not None else None,
            first=first,
            last=int(last) if last is not None else None,
            alpha=float(alpha) if alpha is not None else None,
            force_zero_rmin=params.get("force_zero_rmin", "Y"),
            force_zero_rmax=params.get("force_zero_rmax", "Y"),
        )
        if not ok or not out_text:
            msg = html.escape(f"GNOM preview failed: {stderr or 'unknown error'}")
            self.set_passport(html_text=f'<span style="color:{COLOR_QUALITY_POOR}">{msg}</span>')
            return
        try:
            self._fit_plot.plot_from_dat_and_gnom_out(self._profile_path, out_path)
            self._plot_dr_adjust(out_path)
        except Exception:
            pass
        try:
            parsed = parse_gnom_out(out_text)
            quality = analyze_dr_quality(
                parsed,
                atsas_fit_ok=True,
                rg_guinier_nm=None,
                shape="spheres",
                q_nm=self._q_nm,
                first_pt_1based=first,
            )
            self.set_passport_from_quality(quality)
        except Exception as exc:
            self.set_passport(
                html_text=(
                    f'<span style="color:{COLOR_QUALITY_POOR}">'
                    f"{html.escape(f'Passport update failed: {exc}')}</span>"
                )
            )

    def closeEvent(self, event) -> None:  # noqa: N802
        if not self._confirm.confirm_close_if_dirty():
            event.ignore()
            return
        self._cleanup_preview_tmp()
        super().closeEvent(event)

    def reject(self) -> None:  # type: ignore[override]
        if not self._confirm.confirm_close_if_dirty():
            return
        self._cleanup_preview_tmp()
        super().reject()

    def _cleanup_preview_tmp(self) -> None:
        if self._preview_tmp:
            for p in (self._preview_tmp, self._preview_tmp + ".out"):
                try:
                    if os.path.isfile(p):
                        os.remove(p)
                except OSError:
                    pass
            self._preview_tmp = None
