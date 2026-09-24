"""Distribution modeling main window (MIXTURE)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt5.QtWidgets import (
    QButtonGroup,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...liveview.ui.panels.right.polydisperse.plots import MixtureDistPlot, MixtureFitPlot
from ...ui.passport_table import PassportTableWidget
from ...ui.path_field import PathField
from ..context import ModelingContext
from ..freeze_ui import (
    enter_context_freeze,
    install_context_freeze,
    notify_ready_for_context_if_deferred,
)
from ..ipc import ModelingIpcChild
from ..plots import FitResidualsPlot, ModelingPlotClickRouter
from ..run_params import (
    analysis_root_from_modeling_path,
    conventional_sample_modeling_dir,
    read_run_params,
    resolve_sample_modeling_dir,
    skill_batch_output_dir,
)
from ..runtime import ModelingRuntime


class DrModelingWindow(QMainWindow):
    def __init__(
        self,
        ctx: ModelingContext,
        *,
        ipc: Optional[ModelingIpcChild] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("GUISAXS Distribution Modeling")
        self.resize(1100, 720)
        self._ipc = ipc
        self._ctx = ModelingContext()
        self._runtime = ModelingRuntime(workdir=Path.cwd(), parent=self)
        self._runtime.started.connect(self._on_started)
        self._runtime.finished.connect(self._on_finished)
        self._rows: List[Dict[str, Any]] = []
        self._analysis_root: Optional[Path] = None
        self._plot_clicks = ModelingPlotClickRouter(self)
        self._build_ui()
        install_context_freeze(self)
        self.apply_context(ctx)

    def apply_context(self, ctx: ModelingContext) -> None:
        if self._runtime.is_running():
            enter_context_freeze(self)
            return
        self._ctx = ctx if isinstance(ctx, ModelingContext) else ModelingContext.from_dict({})
        self._pf_profile.set_text(str(self._ctx.profile_path or ""))
        if self._ctx.output_dir:
            resolved = resolve_sample_modeling_dir(
                self._ctx.output_dir, profile_path=self._ctx.profile_path or ""
            )
            self._pf_outdir.set_text(str(resolved))
            root = analysis_root_from_modeling_path(resolved)
            if root is not None:
                self._analysis_root = root
        else:
            self._pf_outdir.set_text("")
        mode = (self._ctx.mode or "none").lower()
        if mode == "mixture":
            self._rb_mixture.setChecked(True)
        else:
            self._rb_none.setChecked(True)
        self._sync_outdir_to_mode(mode)
        opts = self._ctx.options or {}
        for key, spin in (
            ("max_nph", self._sp_max_nph),
            ("r_max_nm", self._sp_r_max),
            ("poly_max_nm", self._sp_poly_max),
            ("q_min", self._q_min),
            ("q_max", self._q_max),
        ):
            if key in opts and opts[key] is not None:
                try:
                    spin.setValue(float(opts[key]) if spin is not self._sp_max_nph else int(opts[key]))
                except (TypeError, ValueError):
                    pass
        self._apply_run_params_from_disk()
        self._update_controls_visibility()
        self._try_load_existing_artifacts()
        self._update_confirm_enabled()

    def _apply_run_params_from_disk(self) -> None:
        raw = (self._pf_outdir.text() or "").strip()
        if not raw:
            return
        prof = (self._pf_profile.text() or "").strip()
        out = str(resolve_sample_modeling_dir(raw, profile_path=prof))
        if out != raw:
            self._pf_outdir.set_text(out)
        if not os.path.isdir(out):
            return
        params = read_run_params(out, profile_path=prof)
        if not params and not (Path(out) / "mixture_results.csv").is_file():
            return
        if self._mode() == "none" and (
            str(params.get("skill") or "") == "model_mixture"
            or (Path(out) / "mixture_results.csv").is_file()
        ):
            self._rb_mixture.setChecked(True)
        if "max_nph" in params:
            try:
                self._sp_max_nph.setValue(int(params["max_nph"]))
            except (TypeError, ValueError):
                pass
        if params.get("r_max_nm") is not None:
            try:
                self._sp_r_max.setValue(float(params["r_max_nm"]))
            except (TypeError, ValueError):
                pass
        if params.get("poly_max_nm") is not None:
            try:
                self._sp_poly_max.setValue(float(params["poly_max_nm"]))
            except (TypeError, ValueError):
                pass
        if params.get("q_min") is not None:
            try:
                self._q_min.setValue(float(params["q_min"]))
            except (TypeError, ValueError):
                pass
        if params.get("q_max") is not None:
            try:
                self._q_max.setValue(float(params["q_max"]))
            except (TypeError, ValueError):
                pass

    def _clear_result_views(self) -> None:
        """Wipe fit/Δ/D(R)/passport so a sample without MIXTURE results is not misleading."""
        self._rows = []
        try:
            self._fit.clear_plot()
        except Exception:
            pass
        try:
            self._delta.clear_plot()
        except Exception:
            pass
        try:
            self._dist.clear_plot()
        except Exception:
            pass
        try:
            self._passport.set_message("—")
        except Exception:
            pass

    def _try_load_existing_artifacts(self) -> None:
        self._clear_result_views()
        raw = (self._pf_outdir.text() or "").strip()
        if not raw:
            return
        prof = (self._pf_profile.text() or "").strip()
        out = str(resolve_sample_modeling_dir(raw, profile_path=prof))
        if out != raw:
            self._pf_outdir.set_text(out)
        if not os.path.isdir(out):
            return
        csv = Path(out) / "mixture_results.csv"
        if not csv.is_file():
            return
        if self._mode() == "none":
            self._rb_mixture.setChecked(True)
        # Prefer BIC_log / best_label from run params / CSV via ingest.
        params = read_run_params(out, profile_path=prof)
        self._ingest_result(
            {
                "output_subdir": out,
                "results_csv_path": str(csv),
                "best_fit_path": self._discover_best_fit(out),
                "best_label": str(params.get("best_label") or ""),
                "BIC_log": params.get("BIC_log"),
            }
        )

    def _discover_best_fit(self, outdir: str) -> str:
        sd = Path(outdir)
        for pat in ("*_best.fit", "*.fit", "*_fit.dat"):
            cands = sorted(sd.glob(pat), key=lambda p: p.stat().st_mtime, reverse=True)
            if cands:
                return str(cands[0])
        return ""

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)

        left = QVBoxLayout()
        dist_box = QGroupBox("D(R)")
        dist_lay = QVBoxLayout(dist_box)
        self._dist = MixtureDistPlot()
        dist_lay.addWidget(self._dist, 1)
        left.addWidget(dist_box, 3)

        bottom = QHBoxLayout()
        fit_box = QGroupBox("MIXTURE I(q) fit")
        fit_lay = QVBoxLayout(fit_box)
        self._fit = MixtureFitPlot()
        fit_lay.addWidget(self._fit, 1)
        delta_box = QGroupBox("MIXTURE Δ (I−fit)/σ")
        delta_lay = QVBoxLayout(delta_box)
        self._delta = FitResidualsPlot()
        delta_lay.addWidget(self._delta, 1)
        bottom.addWidget(fit_box, 2)
        bottom.addWidget(delta_box, 1)
        left.addLayout(bottom, 2)

        self._fit.mpl_connect("button_press_event", lambda ev: self._enlarge_fit(ev))
        self._delta.mpl_connect("button_press_event", lambda ev: self._enlarge_delta(ev))
        self._dist.mpl_connect("button_press_event", lambda ev: self._enlarge_dist(ev))

        right = QVBoxLayout()
        ctrl_box = QGroupBox("Controls")
        ctrl = QVBoxLayout(ctrl_box)
        mode_row = QHBoxLayout()
        self._grp = QButtonGroup(self)
        self._rb_none = QRadioButton("None")
        self._rb_mixture = QRadioButton("MIXTURE")
        self._rb_none.setChecked(True)
        for rb in (self._rb_none, self._rb_mixture):
            self._grp.addButton(rb)
            mode_row.addWidget(rb)
        mode_row.addStretch(1)
        ctrl.addLayout(mode_row)

        ctrl.addWidget(QLabel("I(q) profile"))
        self._pf_profile = PathField(mode="file", expected_exts=(".dat",))
        ctrl.addWidget(self._pf_profile)
        ctrl.addWidget(QLabel("Output directory"))
        self._pf_outdir = PathField(mode="dir")
        ctrl.addWidget(self._pf_outdir)

        self._sp_max_nph = QSpinBox()
        self._sp_max_nph.setRange(1, 3)
        self._sp_max_nph.setValue(3)
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
        self._mixture_params = QWidget()
        form = QFormLayout(self._mixture_params)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("max_nph", self._sp_max_nph)
        form.addRow("r_max (nm)", self._sp_r_max)
        form.addRow("poly_max (nm)", self._sp_poly_max)
        form.addRow("q_min (nm⁻¹)", self._q_min)
        form.addRow("q_max (nm⁻¹)", self._q_max)
        ctrl.addWidget(self._mixture_params)
        self._status = QLabel("—")
        self._status.setWordWrap(True)
        ctrl.addWidget(self._status)
        self._confirm = QPushButton("Confirm")
        self._confirm.clicked.connect(self._on_confirm)
        ctrl.addWidget(self._confirm)

        pass_box = QGroupBox("Quality passport")
        pass_lay = QVBoxLayout(pass_box)
        self._passport = PassportTableWidget()
        pass_lay.addWidget(self._passport, 1)

        right.addWidget(ctrl_box, 2)
        right.addWidget(pass_box, 1)

        root.addLayout(left, 3)
        root.addLayout(right, 1)

        self._rb_none.toggled.connect(lambda *_: self._on_mode_changed())
        self._rb_mixture.toggled.connect(lambda *_: self._on_mode_changed())
        self._pf_profile.path_changed.connect(self._on_profile_path_changed)
        self._pf_outdir.path_changed.connect(self._on_outdir_path_changed)
        self._update_controls_visibility()

    def _mode(self) -> str:
        return "mixture" if self._rb_mixture.isChecked() else "none"

    def _update_controls_visibility(self) -> None:
        """Hide MIXTURE-only params when mode is None (same idea as shape app)."""
        self._mixture_params.setVisible(self._mode() == "mixture")

    def _on_mode_changed(self) -> None:
        self._sync_outdir_to_mode(self._mode())
        self._update_controls_visibility()
        self._update_confirm_enabled()
        self._try_load_existing_artifacts()

    def _on_profile_path_changed(self, *_a) -> None:
        self._sync_outdir_to_mode(self._mode())
        self._update_confirm_enabled()

    def _on_outdir_path_changed(self, *_a) -> None:
        raw = (self._pf_outdir.text() or "").strip()
        if raw:
            root = analysis_root_from_modeling_path(raw)
            if root is not None:
                self._analysis_root = root
        self._update_confirm_enabled()

    def _sync_outdir_to_mode(self, mode: str) -> None:
        if (mode or "").lower() != "mixture":
            return
        suggested = conventional_sample_modeling_dir(
            mode="mixture",
            current_outdir=(self._pf_outdir.text() or "").strip(),
            profile_path=(self._pf_profile.text() or "").strip(),
            analysis_root=self._analysis_root,
        )
        if suggested is None:
            return
        text = str(suggested)
        if (self._pf_outdir.text() or "").strip() != text:
            self._pf_outdir.set_text(text)

    def _update_confirm_enabled(self) -> None:
        if self._mode() != "mixture" or self._runtime.is_running():
            self._confirm.setEnabled(False)
            return
        prof = (self._pf_profile.text() or "").strip()
        out = (self._pf_outdir.text() or "").strip()
        self._confirm.setEnabled(bool(prof) and os.path.isfile(prof) and bool(out))

    def _on_confirm(self) -> None:
        if self._mode() != "mixture":
            return
        prof = (self._pf_profile.text() or "").strip()
        outdir_raw = (self._pf_outdir.text() or "").strip()
        if not prof or not os.path.isfile(prof) or not outdir_raw:
            QMessageBox.warning(self, "Confirm", "Set profile and output directory.")
            return
        sample_out = resolve_sample_modeling_dir(outdir_raw, profile_path=prof)
        batch_outdir = skill_batch_output_dir(sample_out, profile_path=prof)
        Path(sample_out).mkdir(parents=True, exist_ok=True)
        Path(batch_outdir).mkdir(parents=True, exist_ok=True)
        self._pf_outdir.set_text(str(sample_out))
        try:
            self._runtime.set_workdir(Path(batch_outdir))
        except RuntimeError as exc:
            QMessageBox.warning(self, "Confirm", str(exc))
            return
        opts: Dict[str, Any] = {
            "output_dir": str(Path(batch_outdir).resolve()),
            "use_cache": False,
            "max_nph": int(self._sp_max_nph.value()),
        }
        if self._sp_r_max.value() > 0:
            opts["r_max_nm"] = float(self._sp_r_max.value())
        if self._sp_poly_max.value() > 0:
            opts["poly_max_nm"] = float(self._sp_poly_max.value())
        if self._q_min.value() > 0:
            opts["q_min"] = float(self._q_min.value())
        if self._q_max.value() > 0:
            opts["q_max"] = float(self._q_max.value())

        if self._ipc is not None:
            self._ipc.send_confirmed(
                mode="mixture",
                options=dict(opts),
                paths={"profile_path": prof, "output_dir": str(sample_out)},
            )
            self._ipc.send_busy(True)

        self._confirm.setEnabled(False)
        self._status.setText("Running model_mixture…")
        self._runtime.start("model_mixture", [str(Path(prof).resolve())], opts)

    def _on_started(self, skill: str) -> None:
        self._status.setText(f"Running {skill}…")
        self._passport.set_message(f"Running {skill}…")

    def _on_finished(self, outcome: object) -> None:
        if self._ipc is not None:
            self._ipc.send_busy(False)
        success = bool(getattr(outcome, "success", False))
        result = getattr(outcome, "result", {}) or {}
        if not isinstance(result, dict):
            result = {}
        if self._ipc is not None:
            self._ipc.send_finished(success=success, result=result)
        if not success:
            self._status.setText(f"Failed (exit {getattr(outcome, 'exit_code', '?')})")
            self._passport.set_message(self._status.text(), poor=True)
            self._update_confirm_enabled()
            notify_ready_for_context_if_deferred(self, self._ipc)
            return
        self._status.setText("Done.")
        self._ingest_result(result)
        self._update_confirm_enabled()
        notify_ready_for_context_if_deferred(self, self._ipc)

    def _ingest_result(self, result: Dict[str, Any]) -> None:
        fit = result.get("best_fit_path") or result.get("fit_path")
        if isinstance(fit, str) and os.path.isfile(fit):
            self._fit.plot_from_fit(fit)
            # Mixture .fit may not match fir residual layout; try anyway.
            self._delta.plot_from_fir(fit)
        csv_path = result.get("results_csv_path") or result.get("distributions_path")
        row = None
        if isinstance(csv_path, str) and os.path.isfile(csv_path) and csv_path.endswith(".csv"):
            try:
                import csv

                with open(csv_path, newline="", encoding="utf-8", errors="replace") as fh:
                    reader = csv.DictReader(fh)
                    rows = list(reader)
                best_label = str(result.get("best_label") or "").strip()
                if best_label and rows:
                    for r in rows:
                        if str(r.get("label") or "").strip() == best_label:
                            row = r
                            break
                if row is None and rows and "BIC_log" in (rows[0] or {}):
                    def _bic(r: dict) -> float:
                        try:
                            return float(r.get("BIC_log"))
                        except (TypeError, ValueError):
                            return float("inf")

                    row = min(rows, key=_bic)
                elif row is None and rows:
                    row = rows[0]
            except Exception:
                row = None
        if isinstance(row, dict):
            self._dist.plot_from_model_row(row, label=str(row.get("label") or "best"))
        self._passport.set_rows(self._passport_rows_for_result(result, row=row if isinstance(row, dict) else None))

    def _passport_rows_for_result(
        self,
        result: Dict[str, Any],
        *,
        row: Optional[Dict[str, Any]] = None,
    ) -> list[tuple[str, str, str]]:
        """Quality passport from MIXTURE metrics (not paths / mode labels)."""
        rows: list[tuple[str, str, str]] = []
        label = str(result.get("best_label") or (row or {}).get("label") or "").strip()
        if label:
            rows.append(("best model", label, "ok"))

        def _add(metric: str, raw: object) -> None:
            if raw is None or raw == "":
                return
            try:
                v = float(raw)
            except (TypeError, ValueError):
                rows.append((metric, str(raw), "ok"))
                return
            if v != v:  # NaN
                return
            rows.append((metric, f"{v:.4g}", "ok"))

        src = row or {}
        _add("BIC_log", result.get("BIC_log", src.get("BIC_log")))
        _add("χ²", src.get("chi2") if src.get("chi2") is not None else result.get("chi2"))
        _add("R²_log", src.get("R2_log"))
        return rows

    def _enlarge_fit(self, ev: object) -> None:
        if getattr(ev, "inaxes", None) is None or int(getattr(ev, "button", 0)) != 1:
            return
        path = self._fit.click_path
        if not path:
            return

        def _populate(plot: MixtureFitPlot) -> None:
            plot.plot_from_fit(path)

        self._plot_clicks.open(
            key="fit",
            title=f"MIXTURE I(q) fit — {Path(path).name}",
            make_plot=lambda: MixtureFitPlot(figsize=(8, 6)),
            populate=_populate,
        )

    def _enlarge_delta(self, ev: object) -> None:
        if getattr(ev, "inaxes", None) is None or int(getattr(ev, "button", 0)) != 1:
            return
        path = self._delta.click_path
        if not path:
            return

        def _populate(plot: FitResidualsPlot) -> None:
            plot.plot_from_fir(path)

        self._plot_clicks.open(
            key="delta",
            title=f"MIXTURE Δ (I−fit)/σ — {Path(path).name}",
            make_plot=lambda: FitResidualsPlot(figsize=(8, 4)),
            populate=_populate,
        )

    def _enlarge_dist(self, ev: object) -> None:
        if getattr(ev, "inaxes", None) is None or int(getattr(ev, "button", 0)) != 1:
            return
        payload = getattr(self._dist, "_click_payload", None)
        if not isinstance(payload, dict):
            return
        row = payload.get("row") or {}
        r_min = float(payload.get("r_min_ang") or 5.0)
        r_max = float(payload.get("r_max_ang") or 120.0)
        label = str(payload.get("label") or "")

        def _populate(plot: MixtureDistPlot) -> None:
            plot.plot_from_model_row(
                row,
                r_min_ang=r_min,
                r_max_ang=r_max,
                label=label,
            )

        self._plot_clicks.open(
            key="dist",
            title=f"D(R) — {label or 'MIXTURE'}",
            make_plot=lambda: MixtureDistPlot(figsize=(8, 6)),
            populate=_populate,
        )
