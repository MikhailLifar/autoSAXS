"""Distribution modeling main window (MIXTURE)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt5.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...liveview.ui.panels.right.polydisperse.plots import MixtureDistPlot, MixtureFitPlot
from ...ui.os_open import open_path_in_file_manager
from ...ui.passport_format import BIC_CHI2, R2_LOG
from ...ui.passport_table import PassportTableWidget
from ...ui.path_field import PathField
from ...ui.in_progress_overlay import InProgressOverlay
from ...ui.run_status_bar import RunStatusBar
from ..auto_mode import ModelingAutoMode
from ..catalogs.mixture_models import MixtureModelCatalog, build_mixture_model_catalog
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
    normalize_model_mixture_options,
    read_run_params,
    resolve_sample_modeling_dir,
    skill_batch_output_dir,
)
from ..progress_parse import ProgressStderrBuffer
from ..runtime import ModelingRuntime
from .mixture_viewer import MixtureAdvancedViewerDialog


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
        # Wider default so I(q) + ΔI share the bottom row without crushing the residual pane.
        self.resize(1480, 780)
        self._ipc = ipc
        self._ctx = ModelingContext()
        self._runtime = ModelingRuntime(workdir=Path.cwd(), parent=self)
        self._runtime.started.connect(self._on_started)
        self._runtime.finished.connect(self._on_finished)
        self._runtime.stderr.connect(self._on_stderr)
        self._progress_buf = ProgressStderrBuffer()
        self._rows: List[Dict[str, Any]] = []
        self._catalog = MixtureModelCatalog()
        self._analysis_root: Optional[Path] = None
        self._plot_clicks = ModelingPlotClickRouter(self)
        self._mixture_viewer: Optional[MixtureAdvancedViewerDialog] = None
        self._last_mixture_subdir = ""
        self._last_best_label = ""
        self._last_r_min_ang = 5.0
        self._last_r_max_ang = 120.0
        self._build_ui()
        install_context_freeze(self)
        # First bind may seed session-owned run knobs; later IPC pushes must not.
        self.apply_context(ctx, seed_controls=True)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._auto_mode.shutdown()
        super().closeEvent(event)

    def apply_context(self, ctx: ModelingContext, *, seed_controls: bool = False) -> None:
        """Bind sample paths/artifacts from liveview.

        User run controls (max_nph, r_max, q-range, Auto/Manual) are owned by this
        modeling-app session. Sample switch / ModelingContext push updates paths
        and catalogs only — never clobber knobs or rewrite run-params to
        liveview/disk defaults. Confirm always reads the current UI values.
        """
        if self._runtime.is_running():
            enter_context_freeze(self)
            return
        self._ctx = ctx if isinstance(ctx, ModelingContext) else ModelingContext.from_dict({})
        with self._auto_mode.suppress_control_changes():
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
            self._sync_outdir_to_mode("mixture")
            if seed_controls:
                self._seed_run_controls_from_context()
        self._try_load_existing_artifacts()
        self._update_confirm_enabled()

    def _seed_run_controls_from_context(self) -> None:
        """One-shot launch seed for mixture knobs (ctx options + disk run-params)."""
        opts = normalize_model_mixture_options(self._ctx.options or {})
        for key, spin in (
            ("max_nph", self._sp_max_nph),
            ("r_max", self._sp_r_max),
            ("poly_max", self._sp_poly_max),
            ("q_min_nm", self._q_min),
            ("q_max_nm", self._q_max),
        ):
            if key in opts and opts[key] is not None:
                try:
                    spin.setValue(float(opts[key]) if spin is not self._sp_max_nph else int(opts[key]))
                except (TypeError, ValueError):
                    pass
        self._apply_run_params_from_disk()

    def _apply_run_params_from_disk(self) -> Optional[Dict[str, Any]]:
        raw = (self._pf_outdir.text() or "").strip()
        if not raw:
            return None
        prof = (self._pf_profile.text() or "").strip()
        out = str(resolve_sample_modeling_dir(raw, profile_path=prof))
        if out != raw:
            self._pf_outdir.set_text(out)
        if not os.path.isdir(out):
            return None
        params = normalize_model_mixture_options(read_run_params(out, profile_path=prof))
        if not params and not (Path(out) / "mixture_results.csv").is_file():
            return None
        if "max_nph" in params:
            try:
                self._sp_max_nph.setValue(int(params["max_nph"]))
            except (TypeError, ValueError):
                pass
        if params.get("r_max") is not None:
            try:
                self._sp_r_max.setValue(float(params["r_max"]))
            except (TypeError, ValueError):
                pass
        if params.get("poly_max") is not None:
            try:
                self._sp_poly_max.setValue(float(params["poly_max"]))
            except (TypeError, ValueError):
                pass
        if params.get("q_min_nm") is not None:
            try:
                self._q_min.setValue(float(params["q_min_nm"]))
            except (TypeError, ValueError):
                pass
        if params.get("q_max_nm") is not None:
            try:
                self._q_max.setValue(float(params["q_max_nm"]))
            except (TypeError, ValueError):
                pass
        return params

    def _clear_result_views(self) -> None:
        """Wipe fit/Δ/D(R)/passport so a sample without MIXTURE results is not misleading."""
        self._rows = []
        self._catalog = MixtureModelCatalog()
        self._last_mixture_subdir = ""
        self._last_best_label = ""
        self._fit_combo.blockSignals(True)
        try:
            self._fit_combo.clear()
        finally:
            self._fit_combo.blockSignals(False)
        self._open_folder_btn.setEnabled(False)
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
            self._passport.set_rows([])
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
        params = read_run_params(out, profile_path=prof)
        self._ingest_result(
            {
                "output_subdir": out,
                "results_csv_path": str(csv),
                "best_label": str(params.get("best_label") or ""),
                "BIC_chi2": params.get("BIC_chi2"),
                "r_min_nm": params.get("r_min_nm"),
                "r_max_nm": params.get("r_max_nm"),
            }
        )

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setSpacing(8)
        outer.setContentsMargins(8, 8, 8, 8)

        content = QHBoxLayout()

        left = QVBoxLayout()
        dist_box = QGroupBox("D(R)")
        dist_lay = QVBoxLayout(dist_box)
        self._dist = MixtureDistPlot()
        dist_lay.addWidget(self._dist, 1)
        self._busy_overlay = InProgressOverlay(self._dist)
        left.addWidget(dist_box, 3)

        bottom = QHBoxLayout()
        fit_box = QGroupBox("MIXTURE I(q) fit")
        fit_lay = QVBoxLayout(fit_box)
        self._fit = MixtureFitPlot()
        fit_lay.addWidget(self._fit, 1)
        delta_box = QGroupBox("MIXTURE ΔI")
        delta_lay = QVBoxLayout(delta_box)
        self._delta = FitResidualsPlot()
        delta_lay.addWidget(self._delta, 1)
        bottom.addWidget(fit_box, 2)
        bottom.addWidget(delta_box, 1)
        left.addLayout(bottom, 2)

        self._status = RunStatusBar()
        left.addWidget(self._status)

        self._fit.mpl_connect("button_press_event", lambda ev: self._enlarge_fit(ev))
        self._delta.mpl_connect("button_press_event", lambda ev: self._enlarge_delta(ev))
        self._dist.mpl_connect("button_press_event", lambda ev: self._enlarge_dist(ev))

        right = QVBoxLayout()
        ctrl_box = QGroupBox("Controls")
        ctrl = QVBoxLayout(ctrl_box)
        ctrl.addWidget(QLabel("MIXTURE"))

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

        # Results navigation (shape-app parity: browse ensemble fits + open artifacts).
        ctrl.addWidget(QLabel("Results model"))
        self._fit_combo = QComboBox()
        self._fit_combo.setEnabled(False)
        self._fit_combo.currentIndexChanged.connect(self._on_fit_combo_changed)
        ctrl.addWidget(self._fit_combo)
        self._open_folder_btn = QPushButton("Open artifacts folder…")
        self._open_folder_btn.setEnabled(False)
        self._open_folder_btn.clicked.connect(self._on_open_artifacts_folder)
        ctrl.addWidget(self._open_folder_btn)

        self._confirm = QPushButton("Confirm")
        self._confirm.clicked.connect(lambda: self._on_confirm(quiet=False))
        ctrl.addWidget(self._confirm)

        self._auto_btn = QPushButton("Start auto-processing")
        self._auto_btn.setMaximumWidth(240)
        self._auto_mode = ModelingAutoMode(
            self, auto_btn=self._auto_btn, confirm_btn=self._confirm
        )
        self._auto_mode.auto_changed.connect(self._on_auto_changed_ipc)

        pass_box = QGroupBox("Quality passport")
        pass_lay = QVBoxLayout(pass_box)
        self._passport = PassportTableWidget()
        pass_lay.addWidget(self._passport, 1)

        right.addWidget(ctrl_box, 2)
        right.addWidget(pass_box, 1)

        content.addLayout(left, 3)
        content.addLayout(right, 1)
        outer.addLayout(content, 1)

        auto_row = QHBoxLayout()
        auto_row.setContentsMargins(0, 0, 0, 0)
        auto_row.addStretch(1)
        auto_row.addWidget(self._auto_btn, 0)
        outer.addLayout(auto_row, 0)

        self._pf_profile.path_changed.connect(self._on_profile_path_changed)
        self._pf_profile.path_changed.connect(self._auto_mode.on_control_changed)
        self._pf_outdir.path_changed.connect(self._on_outdir_path_changed)
        self._pf_outdir.path_changed.connect(self._auto_mode.on_control_changed)
        for spin in (
            self._sp_max_nph,
            self._sp_r_max,
            self._sp_poly_max,
            self._q_min,
            self._q_max,
        ):
            spin.valueChanged.connect(self._auto_mode.on_control_changed)

    def _mode(self) -> str:
        return "mixture"

    def _on_profile_path_changed(self, *_a) -> None:
        self._sync_outdir_to_mode("mixture")
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
        if self._runtime.is_running():
            self._confirm.setEnabled(False)
            return
        prof = (self._pf_profile.text() or "").strip()
        out = (self._pf_outdir.text() or "").strip()
        self._confirm.setEnabled(bool(prof) and os.path.isfile(prof) and bool(out))

    def _on_auto_changed_ipc(self, auto: bool) -> None:
        if self._ipc is not None:
            self._ipc.send_auto_changed(bool(auto))

    def _on_confirm_ipc(self) -> None:
        if not self._auto_mode.should_accept_ipc_confirm():
            return
        self._on_confirm(quiet=True)

    def _on_confirm(self, *, quiet: bool = False) -> None:
        if self._runtime.is_running():
            # Busy: ignore Confirm (freeze only on deferred context pushes).
            return
        if not quiet:
            self._auto_mode.enter_auto()
        prof = (self._pf_profile.text() or "").strip()
        outdir_raw = (self._pf_outdir.text() or "").strip()
        if not prof or not os.path.isfile(prof) or not outdir_raw:
            if not quiet:
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
            if not quiet:
                QMessageBox.warning(self, "Confirm", str(exc))
            return
        # Skill CLI owns --r-max / --poly-max / --q-*-nm (values in nm). Never pass
        # disk handoff names (r_max_nm) here — RunRequest maps keys to CLI flags 1:1.
        skill_opts = normalize_model_mixture_options(
            {
                "max_nph": int(self._sp_max_nph.value()),
                "r_max": float(self._sp_r_max.value()) if self._sp_r_max.value() > 0 else None,
                "poly_max": float(self._sp_poly_max.value()) if self._sp_poly_max.value() > 0 else None,
                "q_min_nm": float(self._q_min.value()) if self._q_min.value() > 0 else None,
                "q_max_nm": float(self._q_max.value()) if self._q_max.value() > 0 else None,
            }
        )
        opts: Dict[str, Any] = {
            "output_dir": str(Path(batch_outdir).resolve()),
            "use_cache": False,
            **skill_opts,
        }

        if self._ipc is not None:
            self._ipc.send_confirmed(
                mode="mixture",
                options=dict(opts),
                paths={"profile_path": prof, "output_dir": str(sample_out)},
            )
            self._ipc.send_busy(True)

        self._confirm.setEnabled(False)
        self._progress_buf = ProgressStderrBuffer()
        self._status.set_running(True, text="Running model_mixture…")
        self._busy_overlay.set_active(True)
        self._runtime.start("model_mixture", [str(Path(prof).resolve())], opts)

    def _on_started(self, skill: str) -> None:
        self._status.set_running(True, text=f"Running {skill}…")
        self._busy_overlay.set_active(True)
        self._passport.set_message(f"Running {skill}…")

    def _on_stderr(self, chunk: str) -> None:
        for status in self._progress_buf.feed(chunk):
            self._status.set_running(True, text=status)
            self._passport.set_message(status)

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
            self._status.set_running(
                False, text=f"Failed (exit {getattr(outcome, 'exit_code', '?')})"
            )
            self._busy_overlay.set_active(False)
            self._passport.set_message(self._status.text(), poor=True)
            self._update_confirm_enabled()
            notify_ready_for_context_if_deferred(self, self._ipc)
            return
        self._status.set_running(False, text="Done.")
        self._busy_overlay.set_active(False)
        self._ingest_result(result)
        self._update_confirm_enabled()
        notify_ready_for_context_if_deferred(self, self._ipc)

    def _ingest_result(self, result: Dict[str, Any]) -> None:
        out = str(result.get("output_subdir") or "").strip()
        if not out:
            csv_path = str(result.get("results_csv_path") or "").strip()
            if csv_path:
                out = str(Path(csv_path).parent)
        if not out:
            out = (self._pf_outdir.text() or "").strip()
        if out:
            resolved = resolve_sample_modeling_dir(out, profile_path=self._pf_profile.text() or "")
            if resolved:
                self._pf_outdir.set_text(str(resolved))
                out = str(resolved)
            self._last_mixture_subdir = out
        try:
            if result.get("r_min_nm") is not None:
                self._last_r_min_ang = float(result["r_min_nm"]) * 10.0
            if result.get("r_max_nm") is not None:
                self._last_r_max_ang = float(result["r_max_nm"]) * 10.0
            elif self._sp_r_max.value() > 0:
                self._last_r_max_ang = float(self._sp_r_max.value()) * 10.0
        except (TypeError, ValueError):
            pass
        best_label = str(result.get("best_label") or "").strip()
        self._last_best_label = best_label
        self._catalog = build_mixture_model_catalog(
            out,
            best_label=best_label,
            r_min_ang=self._last_r_min_ang,
            r_max_ang=self._last_r_max_ang,
        )
        if self._catalog.best_key:
            self._last_best_label = self._catalog.best_key
        self._rows = [dict(e.row) for e in self._catalog.entries]
        self._sync_fit_combo(select_key=self._catalog.best_key)
        self._open_folder_btn.setEnabled(
            bool(self._catalog.output_subdir) and Path(self._catalog.output_subdir).is_dir()
        )
        self._show_catalog_entry(self._catalog.best())

    def _sync_fit_combo(self, *, select_key: str = "") -> None:
        self._fit_combo.blockSignals(True)
        try:
            self._fit_combo.clear()
            for entry in self._catalog.entries:
                self._fit_combo.addItem(entry.label, entry.key)
            self._fit_combo.setEnabled(bool(self._catalog.entries))
            if select_key:
                for i in range(self._fit_combo.count()):
                    if self._fit_combo.itemData(i) == select_key:
                        self._fit_combo.setCurrentIndex(i)
                        break
            elif self._fit_combo.count():
                self._fit_combo.setCurrentIndex(0)
        finally:
            self._fit_combo.blockSignals(False)

    def _on_fit_combo_changed(self, *_args) -> None:
        key = self._fit_combo.currentData()
        entry = self._catalog.by_key(str(key or ""))
        self._show_catalog_entry(entry)

    def _on_open_artifacts_folder(self) -> None:
        folder = self._catalog.output_subdir or (self._pf_outdir.text() or "").strip()
        if folder:
            open_path_in_file_manager(folder)

    def _show_catalog_entry(self, entry: Optional[Any]) -> None:
        if entry is None:
            return
        fit = str(getattr(entry, "fit_path", "") or "")
        if fit and os.path.isfile(fit):
            self._fit.plot_from_fit(fit)
            try:
                self._delta.plot_from_mixture_fit(fit)
            except Exception:
                pass
        row = dict(getattr(entry, "row", {}) or {})
        if row:
            self._dist.plot_from_model_row(
                row,
                r_min_ang=float(getattr(self._catalog, "r_min_ang", self._last_r_min_ang) or self._last_r_min_ang),
                r_max_ang=float(getattr(self._catalog, "r_max_ang", self._last_r_max_ang) or self._last_r_max_ang),
                label=str(getattr(entry, "csv_label", None) or getattr(entry, "label", "") or "fit"),
            )
        selected = str(getattr(entry, "csv_label", None) or getattr(entry, "key", "") or "")
        self._passport.set_rows(
            self._passport_rows_for_result(
                {
                    "best_label": self._catalog.best_key,
                    "BIC_chi2": getattr(entry, "bic_chi2", None),
                    "chi2": row.get("chi2"),
                },
                row=row,
                selected_label=selected,
            )
        )

    def _passport_rows_for_result(
        self,
        result: Dict[str, Any],
        *,
        row: Optional[Dict[str, Any]] = None,
        selected_label: str = "",
    ) -> list[tuple[str, str, str]]:
        """Quality passport from MIXTURE metrics (not paths / mode labels)."""
        rows: list[tuple[str, str, str]] = []
        best = str(result.get("best_label") or "").strip()
        selected = (selected_label or str((row or {}).get("label") or "")).strip()
        if selected:
            mark = " (best)" if best and selected == best else ""
            rows.append(("selected model", f"{selected}{mark}", "ok"))
        elif best:
            rows.append(("best model", best, "ok"))

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
        bic = result.get("BIC_chi2", src.get("BIC_chi2"))
        if bic is None:
            bic = result.get("BIC_log", src.get("BIC_log"))
        _add(BIC_CHI2, bic)
        _add("χ²", src.get("chi2") if src.get("chi2") is not None else result.get("chi2"))
        _add(R2_LOG, src.get("R2_log"))
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
            plot.plot_from_mixture_fit(path)

        self._plot_clicks.open(
            key="delta",
            title=f"MIXTURE ΔI — {Path(path).name}",
            make_plot=lambda: FitResidualsPlot(figsize=(8, 4)),
            populate=_populate,
        )

    def _enlarge_dist(self, ev: object) -> None:
        """Open the advanced MIXTURE results viewer (model combo + components)."""
        if getattr(ev, "inaxes", None) is None or int(getattr(ev, "button", 0)) != 1:
            return
        subdir = self._last_mixture_subdir or (self._pf_outdir.text() or "").strip()
        if not subdir or not os.path.isdir(subdir):
            return
        if not (Path(subdir) / "mixture_results.csv").is_file():
            return
        payload = getattr(self._dist, "_click_payload", None)
        r_min = self._last_r_min_ang
        r_max = self._last_r_max_ang
        if isinstance(payload, dict):
            try:
                r_min = float(payload.get("r_min_ang") or r_min)
                r_max = float(payload.get("r_max_ang") or r_max)
            except (TypeError, ValueError):
                pass
        if self._mixture_viewer is None:
            self._mixture_viewer = MixtureAdvancedViewerDialog(self)
        self._mixture_viewer.present(
            subdir=subdir,
            best_label=self._last_best_label,
            r_min_ang=r_min,
            r_max_ang=r_max,
            select_key=self._last_best_label,
        )
