"""Distribution modeling main window (MIXTURE)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt5.QtWidgets import (
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
from ...ui.passport_format import BIC_CHI2, R2_LOG
from ...ui.passport_table import PassportTableWidget
from ...ui.path_field import PathField
from ...ui.in_progress_overlay import InProgressOverlay
from ...ui.run_status_bar import RunStatusBar
from ..auto_mode import ModelingAutoMode
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
        self._analysis_root: Optional[Path] = None
        self._plot_clicks = ModelingPlotClickRouter(self)
        self._mixture_viewer: Optional[MixtureAdvancedViewerDialog] = None
        self._last_mixture_subdir = ""
        self._last_best_label = ""
        self._last_r_min_ang = 5.0
        self._last_r_max_ang = 120.0
        self._build_ui()
        install_context_freeze(self)
        self.apply_context(ctx)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._auto_mode.shutdown()
        super().closeEvent(event)

    def apply_context(self, ctx: ModelingContext) -> None:
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
        self._last_mixture_subdir = ""
        self._last_best_label = ""
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
        # Prefer BIC_chi2 / best_label from run params / CSV via ingest.
        params = read_run_params(out, profile_path=prof)
        self._ingest_result(
            {
                "output_subdir": out,
                "results_csv_path": str(csv),
                "best_fit_path": self._discover_best_fit(out, best_label=str(params.get("best_label") or "")),
                "best_label": str(params.get("best_label") or ""),
                "BIC_chi2": params.get("BIC_chi2"),
                "r_min_nm": params.get("r_min_nm"),
                "r_max_nm": params.get("r_max_nm"),
            }
        )

    def _discover_best_fit(self, outdir: str, *, best_label: str = "") -> str:
        sd = Path(outdir)
        lab = (best_label or "").strip()
        if lab:
            nested = sd / lab
            if nested.is_dir():
                cands = sorted(nested.glob("*.fit"), key=lambda p: p.stat().st_mtime, reverse=True)
                if cands:
                    return str(cands[0])
        for pat in ("*_best.fit", "*.fit", "*_fit.dat"):
            cands = sorted(sd.rglob(pat) if pat == "*.fit" else sd.glob(pat), key=lambda p: p.stat().st_mtime, reverse=True)
            if cands:
                return str(cands[0])
        return ""

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
        self._confirm = QPushButton("Confirm")
        self._confirm.clicked.connect(lambda: self._on_confirm(quiet=False))
        ctrl.addWidget(self._confirm)

        self._auto_btn = QPushButton("Start auto-processing")
        self._auto_btn.setMaximumWidth(240)
        self._auto_mode = ModelingAutoMode(
            self, auto_btn=self._auto_btn, confirm_btn=self._confirm
        )

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
        subdir = result.get("output_subdir") or (self._pf_outdir.text() or "").strip()
        if isinstance(subdir, str) and subdir.strip():
            resolved = resolve_sample_modeling_dir(subdir, profile_path=self._pf_profile.text() or "")
            self._pf_outdir.set_text(str(resolved))
            subdir = str(resolved)
            self._last_mixture_subdir = subdir
        try:
            if result.get("r_min_nm") is not None:
                self._last_r_min_ang = float(result["r_min_nm"]) * 10.0
            if result.get("r_max_nm") is not None:
                self._last_r_max_ang = float(result["r_max_nm"]) * 10.0
            elif self._sp_r_max.value() > 0:
                self._last_r_max_ang = float(self._sp_r_max.value()) * 10.0
        except (TypeError, ValueError):
            pass

        csv_path = result.get("results_csv_path")
        if not (isinstance(csv_path, str) and os.path.isfile(csv_path)) and isinstance(subdir, str):
            cand = Path(subdir) / "mixture_results.csv"
            if cand.is_file():
                csv_path = str(cand)
        row = None
        rows: List[Dict[str, Any]] = []
        if isinstance(csv_path, str) and os.path.isfile(csv_path) and csv_path.endswith(".csv"):
            try:
                import csv

                with open(csv_path, newline="", encoding="utf-8", errors="replace") as fh:
                    rows = list(csv.DictReader(fh))
                best_label = str(result.get("best_label") or "").strip()
                if best_label and rows:
                    for r in rows:
                        if str(r.get("label") or "").strip() == best_label:
                            row = r
                            break
                if row is None and rows:
                    from autosaxs.skill.model_mixture.mixture import select_best_mixture_row

                    row = select_best_mixture_row(rows)
            except Exception:
                row = None
        if isinstance(row, dict):
            self._last_best_label = str(row.get("label") or result.get("best_label") or "")
            self._dist.plot_from_model_row(
                row,
                r_min_ang=self._last_r_min_ang,
                r_max_ang=self._last_r_max_ang,
                label=str(row.get("label") or "best"),
            )
        else:
            self._last_best_label = str(result.get("best_label") or "")

        fit = result.get("best_fit_path") or result.get("fit_path")
        if not (isinstance(fit, str) and os.path.isfile(fit)) and isinstance(subdir, str):
            fit = self._discover_best_fit(subdir, best_label=self._last_best_label)
        if isinstance(fit, str) and os.path.isfile(fit):
            self._fit.plot_from_fit(fit)
            self._delta.plot_from_mixture_fit(fit)

        self._rows = rows
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
