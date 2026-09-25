"""Dedicated P(r) / GNOM adjust wizard (live in-process GNOM preview + manual refine)."""

from __future__ import annotations

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

from autosaxs.core.atsas_gnom import normalize_force_zero, run_gnom_pr
from autosaxs.core.gnom import parse_gnom_out
from autosaxs.core.gnom_quality import analyze_pr_quality
from autosaxs.core.utils import ensure_q_nm, load_saxs_1d_any, write_saxs_atsas_format

from ....ui.style import COLOR_QUALITY_POOR
from ....ui.widgets.spin_sliders import AlphaSpinSlider, LengthNmSpinSlider
from ..panels.right.gnom_residuals_plot import GnomResidualsPlot
from ..panels.right.monodisperse.format_display import format_gnom_passport_rows
from ..panels.right.monodisperse.plots import GnomFitPlot, PrPlot
from ..panels.right.passport_table import PassportTableWidget
from .adjust_confirm import AdjustConfirmController
from .adjust_plot_viewers import AdjustPlotClickRouter
from .q_fit_bounds import first_last_from_q_values, make_q_max_spin, make_q_min_spin, q_bounds_from_params

_PREVIEW_DEBOUNCE_MS = 100


class GnomAdjustWizardDialog(QDialog):
    """
    Interactive GNOM refine wizard.

    Left: P(r) (top); I(q) fit + residuals (bottom split).
    Right: q-min/q-max/Dmax/alpha/boundary checkboxes, Restore auto, passport table.
    """

    params_changed = pyqtSignal()
    editing_started = pyqtSignal()
    restore_auto_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("P(r) / GNOM adjust")
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
        self._guinier_handoff: Dict[str, Any] = {}
        self._auto_snapshot: Dict[str, Any] = {}
        self._auto_gnom_out: str = ""
        self._disk_best_gnom_out: str = ""
        self._preview_tmp: Optional[str] = None
        self._pause_emitted = False
        self._block_params = False

        self._pr_plot = PrPlot(figsize=(4.2, 2.8))
        self._fit_plot = GnomFitPlot(figsize=(3.2, 2.4))
        self._resid_plot = GnomResidualsPlot(figsize=(3.2, 2.4))
        for p in (self._pr_plot, self._fit_plot, self._resid_plot):
            p.setMinimumHeight(140)
            p.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        left = QVBoxLayout()
        left.setSpacing(8)
        pr_box = QGroupBox("P(r)")
        pr_lay = QVBoxLayout(pr_box)
        pr_lay.setContentsMargins(6, 8, 6, 6)
        pr_lay.addWidget(self._pr_plot, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        iq_box = QGroupBox("I(q) fit")
        iq_lay = QVBoxLayout(iq_box)
        iq_lay.setContentsMargins(6, 8, 6, 6)
        iq_lay.addWidget(self._fit_plot, 1)
        resid_box = QGroupBox("ΔI")
        resid_lay = QVBoxLayout(resid_box)
        resid_lay.setContentsMargins(6, 8, 6, 6)
        resid_lay.addWidget(self._resid_plot, 1)
        bottom.addWidget(iq_box, 1)
        bottom.addWidget(resid_box, 1)

        left.addWidget(pr_box, 3)
        left.addLayout(bottom, 2)

        self._plot_clicks = AdjustPlotClickRouter(
            self,
            make_dist_plot=lambda: PrPlot(figsize=(5.0, 3.5)),
            make_fit_plot=lambda: GnomFitPlot(figsize=(5.0, 3.5)),
            dist_title="P(r)",
        )
        for p in (self._pr_plot, self._fit_plot, self._resid_plot):
            self._plot_clicks.wire(p)

        self._q_min = make_q_min_spin()
        self._q_max = make_q_max_spin()
        self._dmax = LengthNmSpinSlider()
        self._alpha = AlphaSpinSlider()
        self._p0 = QCheckBox("P(0) = 0")
        self._p0.setChecked(True)
        self._pdmax = QCheckBox("P(Dmax) = 0")
        self._pdmax.setChecked(True)
        self._btn_restore = QPushButton("Restore auto")
        self._btn_restore.clicked.connect(self._on_restore_auto)
        self._confirm = AdjustConfirmController(
            self,
            get_current_params=self.gnom_params,
            on_confirm=self._on_confirm,
            warning_title="Unconfirmed P(r) changes",
            warning_text=(
                "You have unconfirmed GNOM parameter changes. "
                "Close without writing them to disk / re-running fit_distances?"
            ),
        )
        self._lbl_passport_title = QLabel("Passport")
        self._lbl_passport_title.setContentsMargins(0, 0, 0, 0)
        self._passport_table = PassportTableWidget()

        form = QFormLayout()
        form.addRow("q-min (nm⁻¹)", self._q_min)
        form.addRow("q-max (nm⁻¹)", self._q_max)
        form.addRow("Dmax (nm)", self._dmax)
        form.addRow("alpha", self._alpha)

        passport_col = QVBoxLayout()
        passport_col.setContentsMargins(0, 0, 0, 0)
        passport_col.setSpacing(4)
        passport_col.addWidget(self._lbl_passport_title, 0, Qt.AlignTop)
        passport_col.addWidget(self._passport_table, 1)

        right = QVBoxLayout()
        right.setSpacing(8)
        right.addLayout(form)
        right.addWidget(self._p0)
        right.addWidget(self._pdmax)
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
                "Plots and passport update live; press Confirm to write parameters and re-run fit_distances. "
                "Click a plot to open an enlarged viewer."
            )
        )
        lay.addLayout(body, 1)

        # Live GNOM preview while dragging; disk commit only on Confirm.
        self._preview_debounce = QTimer(self)
        self._preview_debounce.setSingleShot(True)
        self._preview_debounce.setInterval(_PREVIEW_DEBOUNCE_MS)
        self._preview_debounce.timeout.connect(self._run_preview)
        self._q_min.valueChanged.connect(self._schedule_preview)
        self._q_max.valueChanged.connect(self._schedule_preview)
        self._dmax.valueChanged.connect(self._schedule_preview)
        self._alpha.valueChanged.connect(self._schedule_preview)
        self._p0.toggled.connect(self._schedule_preview)
        self._pdmax.toggled.connect(self._schedule_preview)

    def set_running(self, running: bool) -> None:
        if running:
            self._preview_debounce.stop()
        enabled = not running
        for w in (self._q_min, self._q_max, self._dmax, self._alpha, self._p0, self._pdmax, self._btn_restore):
            w.setEnabled(enabled)
        self._confirm.set_controls_enabled(enabled)

    def _plot_pr_adjust(self, path: str) -> None:
        disk_best = self._disk_best_gnom_out if self._disk_best_gnom_out and os.path.isfile(self._disk_best_gnom_out) else None
        if disk_best and os.path.abspath(disk_best) == os.path.abspath(path):
            disk_best = None
        self._pr_plot.plot_from_gnom_out(
            path,
            overlay_gnom_out=disk_best,
        )
        self._resid_plot.plot_from_gnom_out(path)

    def set_context(
        self,
        *,
        profile_path: str,
        guinier_handoff: Optional[Mapping[str, Any]] = None,
        auto_snapshot: Optional[Mapping[str, Any]] = None,
        working_params: Optional[Mapping[str, Any]] = None,
        gnom_out_path: str = "",
        passport_text: str = "",
        passport_html: str = "",
        passport_poor: bool = False,
    ) -> None:
        _ = passport_poor
        self._profile_path = str(profile_path or "").strip()
        self._guinier_handoff = dict(guinier_handoff or {})
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
        self._apply_dmax_slider_span()
        self.set_params(params, emit=False)
        if (not params.get("dmax_nm")) and gnom_out_path and os.path.isfile(gnom_out_path):
            try:
                parsed = parse_gnom_out(Path(gnom_out_path).read_text(errors="replace"))
                fill: Dict[str, Any] = {}
                if parsed.get("real_space_rmax") is not None:
                    fill["dmax_nm"] = float(parsed["real_space_rmax"])
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
        self._apply_dmax_slider_span()
        if gnom_out_path and os.path.isfile(gnom_out_path):
            self._fit_plot.plot_from_dat_and_gnom_out(self._profile_path, gnom_out_path)
            self._plot_pr_adjust(gnom_out_path)
        if passport_html:
            # Legacy HTML from coordinator — convert via quality when possible.
            self._passport_table.set_message("—")
            self.set_passport(html_text=passport_html)
        elif passport_text:
            self.set_passport(text=passport_text)
        self._confirm.set_committed(self.gnom_params())
        # Refresh I(q)/P(r) for the loaded q-min/q-max (disk .out may differ).
        self._run_preview()

    def _apply_dmax_slider_span(self) -> None:
        """Slider domain 0 … 4× best-auto Dmax (fallback: current Dmax); spin can exceed."""
        auto_dmax = None
        for src in (
            (self._auto_snapshot or {}).get("dmax_nm"),
            float(self._dmax.value()) if float(self._dmax.value()) > 0 else None,
        ):
            try:
                v = float(src) if src is not None else None
            except (TypeError, ValueError):
                v = None
            if v is not None and v > 0:
                auto_dmax = v
                break
        if auto_dmax is None:
            self._dmax.set_slider_span(min_nm=0.0, max_nm=1000.0)
            return
        self._dmax.set_slider_span(min_nm=0.0, max_nm=4.0 * auto_dmax)

    def set_params(self, params: Mapping[str, Any], *, emit: bool = False) -> None:
        if not isinstance(params, dict) and not isinstance(params, Mapping):
            return
        self._preview_debounce.stop()
        self._block_params = True
        widgets = (self._q_min, self._q_max, self._dmax, self._alpha, self._p0, self._pdmax)
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
            if params.get("dmax_nm") is not None:
                self._dmax.setValue(float(params["dmax_nm"]))
            if params.get("alpha") is not None:
                try:
                    a = float(params["alpha"])
                    self._alpha.setValue(a if a > 0 else 0.0)
                except (TypeError, ValueError):
                    self._alpha.setValue(0.0)
            elif "alpha" in params and params.get("alpha") is None:
                self._alpha.setValue(0.0)
            if "force_zero_rmin" in params:
                self._p0.setChecked(normalize_force_zero(params.get("force_zero_rmin")) == "Y")
            if "force_zero_rmax" in params:
                self._pdmax.setChecked(normalize_force_zero(params.get("force_zero_rmax")) == "Y")
        finally:
            for w in widgets:
                w.blockSignals(False)
            self._block_params = False
        if emit:
            self._schedule_preview()

    def gnom_params(self) -> dict:
        out: dict = {
            "q_min": float(self._q_min.value()),
            "dmax_nm": float(self._dmax.value()),
            "force_zero_rmin": "Y" if self._p0.isChecked() else "N",
            "force_zero_rmax": "Y" if self._pdmax.isChecked() else "N",
        }
        q_max = float(self._q_max.value())
        if q_max > 0.0:
            out["q_max"] = q_max
        alpha = float(self._alpha.value())
        if alpha > 0.0:
            out["alpha"] = alpha
        rg = self._guinier_handoff.get("rg")
        if rg is not None:
            try:
                out["rg_nm"] = float(rg)
            except (TypeError, ValueError):
                pass
        return out

    def set_passport(self, *, text: str = "", poor: bool = False, html_text: str = "") -> None:
        _ = html_text  # legacy callers; prefer set_passport_from_quality
        if text:
            self._passport_table.set_message(text, poor=poor, poor_color=COLOR_QUALITY_POOR)
        elif html_text:
            # Fall back: strip tags roughly for a single message cell.
            plain = html_text
            for tag in ("<br/>", "<br>", "</span>", "<span"):
                plain = plain.replace(tag, " " if tag.startswith("<br") else "")
            # Keep a readable error when only html_text is supplied.
            import re

            plain = re.sub(r"<[^>]+>", "", plain).strip() or "—"
            self._passport_table.set_message(plain, poor=poor or COLOR_QUALITY_POOR in html_text)
        else:
            self._passport_table.set_rows([])

    def set_passport_from_quality(self, quality: Mapping[str, Any]) -> None:
        rows = format_gnom_passport_rows(quality, guinier_handoff=self._guinier_handoff)
        self._passport_table.set_rows(rows, poor_color=COLOR_QUALITY_POOR)

    def show_from_gnom_out(self, gnom_out_path: str) -> None:
        if gnom_out_path and os.path.isfile(gnom_out_path):
            self._disk_best_gnom_out = gnom_out_path
            # Persist path may become the new auto reference after DATGNOM auto.
            if "datgnom" in os.path.basename(gnom_out_path).lower():
                self._auto_gnom_out = gnom_out_path
            self._fit_plot.plot_from_dat_and_gnom_out(self._profile_path, gnom_out_path)
            self._plot_pr_adjust(gnom_out_path)

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
            fd, tmp = tempfile.mkstemp(suffix="_atsas.dat", prefix="gnom_wiz_")
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
        params = self.gnom_params()
        dmax = float(params["dmax_nm"])
        try:
            first, last = first_last_from_q_values(
                self._q_nm,
                q_min=params.get("q_min"),
                q_max=params.get("q_max"),
            )
        except (TypeError, ValueError, RuntimeError) as exc:
            self.set_passport(text=f"Invalid q-min/q-max: {exc}", poor=True)
            return
        alpha = params.get("alpha")
        out_path = (self._preview_tmp or "") + ".out"
        ok, _rc, stderr, out_text = run_gnom_pr(
            atsas_dat_path=self._atsas_dat_path,
            output_dir=str(Path(out_path).parent),
            rmax_nm=dmax,
            out_path=out_path,
            first=first,
            last=int(last) if last is not None else None,
            alpha=float(alpha) if alpha is not None else None,
            force_zero_rmin=params.get("force_zero_rmin", "Y"),
            force_zero_rmax=params.get("force_zero_rmax", "Y"),
        )
        if not ok or not out_text:
            msg = f"GNOM preview failed: {stderr or 'unknown error'}"
            self.set_passport(text=msg, poor=True)
            return
        try:
            self._fit_plot.plot_from_dat_and_gnom_out(self._profile_path, out_path)
            self._plot_pr_adjust(out_path)
        except Exception:
            pass
        try:
            parsed = parse_gnom_out(out_text)
            rg_g = None
            if self._guinier_handoff.get("rg") is not None:
                rg_g = float(self._guinier_handoff["rg"])
            quality = analyze_pr_quality(
                parsed,
                atsas_fit_ok=True,
                rg_guinier_nm=rg_g,
                q_nm=self._q_nm,
                first_pt_1based=first,
                last_pt_1based=last,
                suspicious=bool(parsed.get("suspicious")),
                dmax_validation=None,
            )
            self.set_passport_from_quality(quality)
        except Exception as exc:
            self.set_passport(text=f"Passport update failed: {exc}", poor=True)

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
