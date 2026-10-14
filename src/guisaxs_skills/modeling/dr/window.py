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

_MAX_INIT_COMPONENTS = 3


def _format_init_csv(values: List[float]) -> str:
    return ",".join(f"{float(v):.6g}" for v in values)


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
        self._runtime.stderr.connect(self._on_stderr)
        self._progress_buf = ProgressStderrBuffer()
        self._rows: List[Dict[str, Any]] = []
        self._catalog = MixtureModelCatalog()
        self._analysis_root: Optional[Path] = None
        self._plot_clicks = ModelingPlotClickRouter(self)
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
            opts = normalize_model_mixture_options(self._ctx.options or {})
            # Keep init_* from context even if normalize already included them.
            for key in ("init_radii_nm", "init_fractions", "init_poly_nm"):
                raw = (self._ctx.options or {}).get(key)
                if raw is not None and raw != "":
                    opts[key] = raw
            had_max_nph = "max_nph" in opts
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
            self._apply_init_options(opts)
            disk = self._apply_run_params_from_disk()
            if isinstance(disk, dict) and "max_nph" in disk:
                had_max_nph = True
            self._apply_fit_sizes_component_guesses(opts_had_max_nph=had_max_nph)
        self._try_load_existing_artifacts()
        self._update_confirm_enabled()

    def _fit_sizes_handoff_path(self) -> Optional[Path]:
        """Locate fit_sizes YAML that owns D(R) peak / n_phases suggestions."""
        prof = (self._pf_profile.text() or "").strip()
        if not prof:
            return None
        from autosaxs.core.utils import _strip_sub_int_prefix

        stem = _strip_sub_int_prefix(Path(prof).stem)
        name = f"{stem}_fit_sizes.yml"
        out_raw = (self._pf_outdir.text() or "").strip()
        roots: List[Path] = []
        if self._analysis_root is not None:
            roots.append(Path(self._analysis_root))
        if out_raw:
            out = Path(resolve_sample_modeling_dir(out_raw, profile_path=prof))
            root = analysis_root_from_modeling_path(str(out))
            if root is not None:
                roots.append(Path(root))
            roots.append(out)
            roots.append(out.parent)
        seen: set[str] = set()
        for root in roots:
            for cand in (
                root / "fit_sizes" / stem / name,
                root / "_fit_sizes_defaults" / stem / name,
            ):
                key = str(cand)
                if key in seen:
                    continue
                seen.add(key)
                if cand.is_file():
                    return cand
        return None

    def _apply_fit_sizes_component_guesses(self, *, opts_had_max_nph: bool) -> None:
        """
        Surface fit_sizes component guesses in the DR UI.

        Prefills max_nph from ``n_phases_suggested`` when unset. Prefills advanced
        init-radius spins from D(R) peaks when those spins are still empty.
        """
        path = self._fit_sizes_handoff_path()
        if path is None:
            self._lbl_init_hint.setText("")
            return
        try:
            import yaml

            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            self._lbl_init_hint.setText("")
            return
        mm = doc.get("model_mixture") if isinstance(doc, dict) else None
        if not isinstance(mm, dict):
            mm = {}
        quality = doc.get("quality") if isinstance(doc, dict) else None
        if not isinstance(quality, dict):
            quality = {}
        n_ph = mm.get("n_phases_suggested")
        if n_ph is None:
            n_ph = quality.get("n_components_suggested")
        peaks = mm.get("peak_positions_nm") or quality.get("dr_peak_positions_nm") or []
        dist = mm.get("mixture_dist_hint") or ""
        parts: List[str] = []
        if n_ph is not None:
            parts.append(f"n_ph≈{n_ph}")
        if dist:
            parts.append(str(dist))
        peak_vals: List[float] = []
        if isinstance(peaks, list) and peaks:
            try:
                peak_vals = [float(p) for p in peaks[:_MAX_INIT_COMPONENTS]]
                peak_txt = ", ".join(f"{p:.3g}" for p in peak_vals)
                parts.append(f"peaks [{peak_txt}] nm")
            except (TypeError, ValueError):
                peak_vals = []
        if parts:
            self._lbl_init_hint.setText("D(R) init: " + "; ".join(parts))
        else:
            self._lbl_init_hint.setText("")
        if not opts_had_max_nph and n_ph is not None:
            try:
                self._sp_max_nph.setValue(int(max(1, min(3, int(n_ph)))))
            except (TypeError, ValueError):
                pass
        if peak_vals and not self._init_radii_have_values():
            self._set_init_component_values(radii_nm=peak_vals)

    def _init_radii_have_values(self) -> bool:
        return any(float(sp.value()) > 0.0 for sp in self._init_r)

    def _set_init_component_values(
        self,
        *,
        radii_nm: Optional[List[float]] = None,
        fractions: Optional[List[float]] = None,
        poly_nm: Optional[List[float]] = None,
    ) -> None:
        for i in range(_MAX_INIT_COMPONENTS):
            if radii_nm is not None:
                self._init_r[i].setValue(float(radii_nm[i]) if i < len(radii_nm) else 0.0)
            if fractions is not None:
                self._init_f[i].setValue(float(fractions[i]) if i < len(fractions) else 0.0)
            if poly_nm is not None:
                self._init_p[i].setValue(float(poly_nm[i]) if i < len(poly_nm) else 0.0)

    def _collect_init_options(self) -> Dict[str, str]:
        """Serialize advanced component starts to skill CLI strings (omit empties)."""
        from autosaxs.skill.model_mixture import coerce_float_sequence

        radii = [float(sp.value()) for sp in self._init_r if float(sp.value()) > 0.0]
        fracs = [float(sp.value()) for sp in self._init_f if float(sp.value()) > 0.0]
        polys = [float(sp.value()) for sp in self._init_p if float(sp.value()) > 0.0]
        out: Dict[str, str] = {}
        if radii:
            out["init_radii_nm"] = _format_init_csv(radii)
        if fracs:
            out["init_fractions"] = _format_init_csv(fracs)
        if polys:
            out["init_poly_nm"] = _format_init_csv(polys)
        # Validate parse round-trip (skill owns coerce).
        for key in list(out):
            if not coerce_float_sequence(out[key]):
                out.pop(key, None)
        return out

    def _apply_init_options(self, params: Dict[str, Any]) -> None:
        from autosaxs.skill.model_mixture import coerce_float_sequence

        radii = coerce_float_sequence(params.get("init_radii_nm"))
        fracs = coerce_float_sequence(params.get("init_fractions"))
        polys = coerce_float_sequence(params.get("init_poly_nm"))
        if radii or fracs or polys:
            self._set_init_component_values(
                radii_nm=radii or None,
                fractions=fracs or None,
                poly_nm=polys or None,
            )
            # Open advanced when disk/session already customized starts.
            self._adv_box.setChecked(True)

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
        # read_run_params may include init_* strings; normalize keeps skill keys.
        disk_raw = read_run_params(out, profile_path=prof)
        params = normalize_model_mixture_options(disk_raw)
        # normalize drops unknown aliases but keeps init_*; merge back if present on disk.
        for key in ("init_radii_nm", "init_fractions", "init_poly_nm"):
            if key in disk_raw and key not in params:
                params[key] = disk_raw[key]
            elif key in disk_raw:
                params[key] = disk_raw[key]
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
        self._apply_init_options(params)
        return params

    def _clear_result_views(self) -> None:
        """Wipe fit/Δ/D(R)/passport so a sample without MIXTURE results is not misleading."""
        self._rows = []
        self._catalog = MixtureModelCatalog()
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
                "BIC_log": params.get("BIC_log"),
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
        self._lbl_init_hint = QLabel("")
        self._lbl_init_hint.setWordWrap(True)
        self._lbl_init_hint.setStyleSheet("color: #555;")
        ctrl.addWidget(self._lbl_init_hint)

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

        # Advanced: editable MIXTURE component initial guesses (collapsed by default).
        self._adv_box = QGroupBox("Advanced: component initial guesses")
        self._adv_box.setCheckable(True)
        self._adv_box.setChecked(False)
        adv_lay = QVBoxLayout(self._adv_box)
        adv_lay.addWidget(
            QLabel("Per-phase starts for ATSAS MIXTURE (Rout, volume fraction, poly). Empty = skill default / D(R) peaks.")
        )
        adv_form = QFormLayout()
        self._init_r: List[QDoubleSpinBox] = []
        self._init_f: List[QDoubleSpinBox] = []
        self._init_p: List[QDoubleSpinBox] = []
        for i in range(_MAX_INIT_COMPONENTS):
            row = QHBoxLayout()
            sp_r = QDoubleSpinBox()
            sp_r.setRange(0.0, 1e6)
            sp_r.setDecimals(4)
            sp_r.setSpecialValueText("(auto)")
            sp_r.setValue(0.0)
            sp_f = QDoubleSpinBox()
            sp_f.setRange(0.0, 1.0)
            sp_f.setDecimals(4)
            sp_f.setSpecialValueText("(auto)")
            sp_f.setValue(0.0)
            sp_p = QDoubleSpinBox()
            sp_p.setRange(0.0, 1e6)
            sp_p.setDecimals(4)
            sp_p.setSpecialValueText("(auto)")
            sp_p.setValue(0.0)
            row.addWidget(QLabel("R"))
            row.addWidget(sp_r, 1)
            row.addWidget(QLabel("φ"))
            row.addWidget(sp_f, 1)
            row.addWidget(QLabel("poly"))
            row.addWidget(sp_p, 1)
            wrap = QWidget()
            wrap.setLayout(row)
            adv_form.addRow(f"phase {i + 1}", wrap)
            self._init_r.append(sp_r)
            self._init_f.append(sp_f)
            self._init_p.append(sp_p)
        adv_lay.addLayout(adv_form)
        ctrl.addWidget(self._adv_box)

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
            *self._init_r,
            *self._init_f,
            *self._init_p,
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
                **self._collect_init_options(),
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
        best_label = str(result.get("best_label") or "").strip()
        self._catalog = build_mixture_model_catalog(out, best_label=best_label)
        self._rows = [dict(e.row) for e in self._catalog.entries]
        self._sync_fit_combo(select_key=self._catalog.best_key)
        self._open_folder_btn.setEnabled(bool(self._catalog.output_subdir) and Path(self._catalog.output_subdir).is_dir())
        self._show_catalog_entry(self._catalog.best())

    def _sync_fit_combo(self, *, select_key: str = "") -> None:
        self._fit_combo.blockSignals(True)
        try:
            self._fit_combo.clear()
            for entry in self._catalog.entries:
                tip = entry.label
                if entry.bic_log is not None:
                    tip = f"{entry.label}  (BIC_log={entry.bic_log:.4g})"
                self._fit_combo.addItem(tip, entry.key)
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
                self._delta.plot_from_fir(fit)
            except Exception:
                pass
        row = dict(getattr(entry, "row", {}) or {})
        if row:
            self._dist.plot_from_model_row(row, label=str(getattr(entry, "label", "") or "fit"))
        self._passport.set_rows(
            self._passport_rows_for_result(
                {
                    "best_label": self._catalog.best_key,
                    "BIC_log": getattr(entry, "bic_log", None),
                    "chi2": getattr(entry, "chi2", None),
                },
                row=row,
                selected_label=str(getattr(entry, "label", "") or ""),
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
        _add("BIC_log", src.get("BIC_log") if src.get("BIC_log") is not None else result.get("BIC_log"))
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
            title=f"MIXTURE ΔI — {Path(path).name}",
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
