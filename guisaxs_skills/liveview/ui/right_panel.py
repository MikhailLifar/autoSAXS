from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Optional

import yaml
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.models import RunRequest
from ...logic.path_normalize import normalize_pathish
from ...logic.skill_catalog import discover_skills
from ...logic.session_state import SessionPathHints
from ...ui.curve_plot import CurvePlot
from ...ui.path_field import PathField
from ...ui.preview_panel import PreviewPanel
from ..state import AnalysisMode, LiveviewSessionState, LiveviewState
from .right_wizards import (
    LIVEVIEW_MIXTURE_YML_NAME,
    FitBodiesWizardDialog,
    FitDistancesWizardDialog,
    FitMixtureWizardDialog,
    FitSizesWizardDialog,
)
from .viewer_3d import LiveviewViewer3D

try:
    from autosaxs.skill.fit_bodies import BODIES_SHAPES_LIST
except Exception:
    BODIES_SHAPES_LIST = [
        "cylinder",
        "dumbbell",
        "ellipsoid",
        "elliptic-cylinder",
        "hollow-cylinder",
        "hollow-sphere",
        "parallelepiped",
        "rotation-ellipsoid",
    ]


def _strip_fit_distances_profile_from_saved_form(
    saved: Optional[dict[str, Any]], meta_fit: Any
) -> Optional[dict[str, Any]]:
    """Do not persist profile path in the liveview session snapshot (options only)."""
    if saved is None or meta_fit is None:
        return saved
    out = copy.deepcopy(saved)
    pos = list(out.get("positional") or [])
    for i, p in enumerate(meta_fit.positional_params):
        if p.name != "profile" or i >= len(pos):
            continue
        prev = pos[i] if isinstance(pos[i], dict) else {}
        pos[i] = {
            "text": "",
            "dropped_paths": [],
            "mode": prev.get("mode", "any"),
        }
    out["positional"] = pos
    return out


def _norm_path(val: object) -> str:
    if not isinstance(val, str):
        return ""
    s = val.strip()
    if not s or s.lower() == "none":
        return ""
    return s


def _newest_dammif_dummy_cif(subdir: Path) -> Optional[str]:
    cifs = sorted(
        subdir.glob("dammif-*-1.cif"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return str(cifs[0].resolve()) if cifs else None


def _best_dammif_cif(subdir: Path) -> Optional[str]:
    yml = subdir / "dammif_fits.yml"
    if not yml.is_file():
        return _newest_dammif_dummy_cif(subdir)
    try:
        data = yaml.safe_load(yml.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict) or not data:
            return _newest_dammif_dummy_cif(subdir)
        best_k = None
        best_c = float("inf")
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            c = v.get("chi2")
            try:
                cf = float(c)
            except (TypeError, ValueError):
                continue
            if cf < best_c:
                best_c = cf
                best_k = k
        if not best_k or not isinstance(best_k, str):
            return _newest_dammif_dummy_cif(subdir)
        # dammif-1 -> dammif-1-1.cif (matches autosaxs fit_dammif / ATSAS prefix)
        cif = subdir / f"{best_k}-1.cif"
        if cif.is_file():
            return str(cif.resolve())
        # Legacy autosaxs YAML used 0-based keys (dammif-0, dammif-1) while CIFs are dammif-1-1.cif, …
        legacy_zero_based = any(isinstance(k, str) and k == "dammif-0" for k in data.keys())
        if legacy_zero_based and best_k.startswith("dammif-"):
            try:
                idx = int(best_k.split("-", 1)[1])
            except (ValueError, IndexError):
                idx = -1
            if idx >= 0:
                leg = subdir / f"dammif-{idx + 1}-1.cif"
                if leg.is_file():
                    return str(leg.resolve())
        return _newest_dammif_dummy_cif(subdir)
    except Exception:
        return _newest_dammif_dummy_cif(subdir)


def _bodies_best_fit(
    subdir: Path,
) -> tuple[Optional[str], dict[str, float], Optional[str]]:
    """
    Lowest-``chi2`` row from ``bodies_fits.yml`` plus CSV path if present.
    Returns ``(best_shape, params_without_chi2, csv_path_or_none)``.
    """
    yml = subdir / "bodies_fits.yml"
    csv_path = subdir / "bodies_fits.csv"
    csv_str = str(csv_path.resolve()) if csv_path.is_file() else None
    if not yml.is_file():
        return None, {}, csv_str
    try:
        data = yaml.safe_load(yml.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict) or not data:
            return None, {}, csv_str
        best_shape: Optional[str] = None
        best_c = float("inf")
        best_row: Optional[dict[str, Any]] = None
        for k, v in data.items():
            if not isinstance(v, dict):
                continue
            c = v.get("chi2")
            try:
                cf = float(c)
            except (TypeError, ValueError):
                continue
            if cf < best_c:
                best_c = cf
                best_shape = k if isinstance(k, str) else None
                best_row = v
        if not best_shape or best_row is None:
            return None, {}, csv_str
        params: dict[str, float] = {}
        for pk, pv in best_row.items():
            if pk == "chi2" or isinstance(pv, bool):
                continue
            try:
                params[str(pk)] = float(pv)
            except (TypeError, ValueError):
                continue
        return best_shape, params, csv_str
    except Exception:
        return None, {}, csv_str


class LiveviewRightPanel(QWidget):
    analysis_mode_changed = pyqtSignal(object)
    modeling_enabled_changed = pyqtSignal(bool)
    modeling_config_changed = pyqtSignal()
    fit_distances_run_requested = pyqtSignal()
    fit_distances_cancel_requested = pyqtSignal()
    fit_sizes_run_requested = pyqtSignal()
    fit_mixture_run_requested = pyqtSignal()
    fit_bodies_run_requested = pyqtSignal()

    _MODE_ITEMS: tuple[tuple[str, AnalysisMode], ...] = (
        ("Off", AnalysisMode.OFF),
        ("Monodisperse analysis: p(r)", AnalysisMode.MONODISPERSE_PR),
        ("Monodisperse analysis: DAM", AnalysisMode.MONODISPERSE_DAM),
        ("Monodisperse analysis: primitives", AnalysisMode.MONODISPERSE_BODIES),
        ("Polydisperse analysis: d(r)", AnalysisMode.POLYDISPERSE_DR),
        ("Polydisperse analysis: mixture", AnalysisMode.POLYDISPERSE_MIXTURE),
    )

    def __init__(self, *, state: LiveviewSessionState) -> None:
        super().__init__()
        self._state = state
        self._fit_wizard: FitDistancesWizardDialog | None = None
        self._fit_sizes_wizard: FitSizesWizardDialog | None = None
        self._fit_mixture_wizard: FitMixtureWizardDialog | None = None
        self._fit_bodies_wizard: FitBodiesWizardDialog | None = None
        self._fit_distances_saved_form: Optional[dict[str, Any]] = None
        self._fit_sizes_saved_form: Optional[dict[str, Any]] = None
        self._fit_mixture_saved_form: Optional[dict[str, Any]] = None
        self._fit_mixture_saved_mixture_params: Optional[dict[str, Any]] = None
        self._mode_combo_block = False

        skills = {m.name: m for m in discover_skills()}
        self._meta_fit = skills.get("fit_distances")
        self._meta_sizes = skills.get("fit_sizes")
        self._meta_mixture = skills.get("fit_mixture")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        self._mode_combo = QComboBox()
        for label, mode in self._MODE_ITEMS:
            self._mode_combo.addItem(label, mode)
        self._mode_combo.currentIndexChanged.connect(self._on_mode_combo_changed)

        self._state_a_placeholder = QLabel("Select a mode; it applies after calibration.")
        self._state_a_placeholder.setWordWrap(True)

        self._params_stack = QStackedWidget()
        self._params_stack.addWidget(self._build_params_off())
        self._params_stack.addWidget(self._build_params_gnom())
        self._params_stack.addWidget(self._build_params_gnom_dam())
        self._params_stack.addWidget(self._build_params_bodies())
        self._params_stack.addWidget(self._build_params_sizes())
        self._params_stack.addWidget(self._build_params_mixture())

        self._output_stack = QStackedWidget()
        self._output_stack.addWidget(self._build_output_off())
        self._output_stack.addWidget(self._build_output_pr())
        self._output_stack.addWidget(self._build_output_dam())
        self._output_stack.addWidget(self._build_output_bodies())
        self._output_stack.addWidget(self._build_output_dr())
        self._output_stack.addWidget(self._build_output_mixture())

        grp = QGroupBox("Analysis")
        gl = QVBoxLayout(grp)
        gl.addWidget(self._mode_combo)
        gl.addWidget(self._state_a_placeholder)
        gl.addWidget(self._params_stack, 1)
        gl.addWidget(self._output_stack, 1)
        root.addWidget(grp)

        self._restore_modeling_conf_from_disk()
        self._restore_fit_sizes_conf_from_disk()
        self._restore_bodies_conf_from_disk()
        self._restore_mixture_from_disk()
        if self._meta_fit is None:
            grp.setEnabled(False)
        self.sync_modeling_ui_to_session_state()

    def _build_params_off(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel(""))
        lay.addStretch(1)
        return w

    def _build_params_gnom(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        btn = QPushButton("Set fit_distances (GNOM / p(r))…")
        btn.clicked.connect(self._open_fit_wizard)
        lay.addWidget(btn)
        lay.addStretch(1)
        return w

    def _build_params_gnom_dam(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(QLabel("Same GNOM as p(r); DAMMIF on best .out."))
        btn = QPushButton("Set fit_distances (GNOM)…")
        btn.clicked.connect(self._open_fit_wizard)
        lay.addWidget(btn)
        lay.addStretch(1)
        return w

    def _build_params_bodies(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(
            QLabel(
                "Primitives: the queue runs fit_bodies on each profile. BODIES --first is derived via "
                "in-process Guinier (no GNOM / fit_distances step)."
            )
        )
        lay.addWidget(QLabel("Body models to fit:"))
        btn_shapes = QPushButton("Set fit_bodies (shapes)…")
        btn_shapes.clicked.connect(self._open_fit_bodies_wizard)
        lay.addWidget(btn_shapes)
        lay.addStretch(1)
        return w

    def _build_params_sizes(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        btn = QPushButton("Set fit_sizes (d(r))…")
        btn.clicked.connect(self._open_fit_sizes_wizard)
        lay.addWidget(btn)
        lay.addStretch(1)
        return w

    def _build_params_mixture(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        btn = QPushButton("Set fit_mixture (MIXTURE)…")
        btn.clicked.connect(self._open_fit_mixture_wizard)
        lay.addWidget(btn)
        lay.addStretch(1)
        return w

    def _build_output_off(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._hint_off = QLabel("—")
        self._hint_off.setWordWrap(True)
        lay.addWidget(self._hint_off)
        lay.addStretch(1)
        return w

    def _build_output_pr(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._hint_fit_pr = QLabel("—")
        self._hint_fit_pr.setWordWrap(True)
        self._fit_plot_pr = PreviewPanel()
        self._fit_plot_pr.setMinimumHeight(120)
        self._hint_pr_pr = QLabel("—")
        self._hint_pr_pr.setWordWrap(True)
        self._pr_plot_pr = PreviewPanel()
        self._pr_plot_pr.setMinimumHeight(120)
        lay.addWidget(QLabel("Fit"))
        lay.addWidget(self._hint_fit_pr)
        lay.addWidget(self._fit_plot_pr, 1)
        lay.addWidget(QLabel("p(r)"))
        lay.addWidget(self._hint_pr_pr)
        lay.addWidget(self._pr_plot_pr, 1)
        return w

    def _build_output_dam(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._hint_fit_dam = QLabel("—")
        self._hint_fit_dam.setWordWrap(True)
        self._fit_plot_dam = PreviewPanel()
        self._fit_plot_dam.setMinimumHeight(100)
        self._hint_pr_dam = QLabel("—")
        self._hint_pr_dam.setWordWrap(True)
        self._pr_plot_dam = PreviewPanel()
        self._pr_plot_dam.setMinimumHeight(100)
        lay.addWidget(QLabel("Fit"))
        lay.addWidget(self._hint_fit_dam)
        lay.addWidget(self._fit_plot_dam, 1)
        lay.addWidget(QLabel("p(r)"))
        lay.addWidget(self._hint_pr_dam)
        lay.addWidget(self._pr_plot_dam, 1)
        lay.addWidget(QLabel("3D"))
        self._viewer_dam = LiveviewViewer3D()
        lay.addWidget(self._viewer_dam, 1)
        return w

    def _build_output_bodies(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._hint_curve_bodies = QLabel("—")
        self._hint_curve_bodies.setWordWrap(True)
        self._curve_bodies = CurvePlot()
        self._curve_bodies.setMinimumHeight(140)
        self._bodies_png_fallback = PreviewPanel()
        self._bodies_png_fallback.setMinimumHeight(140)
        self._bodies_png_fallback.setVisible(False)
        lay.addWidget(self._hint_curve_bodies)
        lay.addWidget(self._curve_bodies, 1)
        lay.addWidget(self._bodies_png_fallback, 1)
        lay.addWidget(QLabel("3D"))
        self._viewer_bodies = LiveviewViewer3D()
        lay.addWidget(self._viewer_bodies, 1)
        return w

    def _build_output_dr(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._hint_fit_dr = QLabel("—")
        self._hint_fit_dr.setWordWrap(True)
        self._fit_plot_dr = PreviewPanel()
        self._fit_plot_dr.setMinimumHeight(120)
        self._hint_dr = QLabel("—")
        self._hint_dr.setWordWrap(True)
        self._dr_plot = PreviewPanel()
        self._dr_plot.setMinimumHeight(120)
        lay.addWidget(QLabel("Fit"))
        lay.addWidget(self._hint_fit_dr)
        lay.addWidget(self._fit_plot_dr, 1)
        lay.addWidget(QLabel("d(r)"))
        lay.addWidget(self._hint_dr)
        lay.addWidget(self._dr_plot, 1)
        return w

    def _build_output_mixture(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self._hint_mix_c = QLabel("—")
        self._hint_mix_c.setWordWrap(True)
        self._mix_comp = PreviewPanel()
        self._mix_comp.setMinimumHeight(120)
        self._hint_mix_d = QLabel("—")
        self._hint_mix_d.setWordWrap(True)
        self._mix_dist = PreviewPanel()
        self._mix_dist.setMinimumHeight(120)
        lay.addWidget(QLabel("Cmp"))
        lay.addWidget(self._hint_mix_c)
        lay.addWidget(self._mix_comp, 1)
        lay.addWidget(QLabel("Dist"))
        lay.addWidget(self._hint_mix_d)
        lay.addWidget(self._mix_dist, 1)
        return w

    def clear_output_previews(self) -> None:
        """Clear analysis thumbnails and 3D viewers (e.g. after switching watch directory)."""
        self._fit_plot_pr.show_path("")
        self._hint_fit_pr.setVisible(True)
        self._pr_plot_pr.show_path("")
        self._hint_pr_pr.setVisible(True)

        self._fit_plot_dam.show_path("")
        self._hint_fit_dam.setVisible(True)
        self._pr_plot_dam.show_path("")
        self._hint_pr_dam.setVisible(True)
        self._viewer_dam.clear()

        self._curve_bodies.clear()
        self._curve_bodies.setVisible(True)
        self._bodies_png_fallback.show_path("")
        self._bodies_png_fallback.setVisible(False)
        self._hint_curve_bodies.setVisible(True)
        self._viewer_bodies.clear()

        self._fit_plot_dr.show_path("")
        self._hint_fit_dr.setVisible(True)
        self._dr_plot.show_path("")
        self._hint_dr.setVisible(True)

        self._mix_comp.show_path("")
        self._hint_mix_c.setVisible(True)
        self._mix_dist.show_path("")
        self._hint_mix_d.setVisible(True)

    def reload_configs_from_watchdir(self) -> None:
        """Re-scan ``fit_distances`` / ``fit_sizes`` / ``fit_bodies`` / mixture sidecars for the current ``state.watchdir``."""
        self._restore_modeling_conf_from_disk()
        self._restore_fit_sizes_conf_from_disk()
        self._restore_bodies_conf_from_disk()
        self._restore_mixture_from_disk()

    def _open_fit_bodies_wizard(self) -> None:
        if self._fit_bodies_wizard is None:
            self._fit_bodies_wizard = FitBodiesWizardDialog(
                watchdir=self._state.watchdir,
                saved_shapes=self._state.fit_bodies_shapes,
                parent=self,
            )
        else:
            self._fit_bodies_wizard.rebuild(saved_shapes=self._state.fit_bodies_shapes)
        self._fit_bodies_wizard.show()
        self._fit_bodies_wizard.raise_()
        self._fit_bodies_wizard.activateWindow()

    def _mode_stack_index(self, mode: AnalysisMode) -> int:
        for i, (_lbl, m) in enumerate(self._MODE_ITEMS):
            if m == mode:
                return i
        return 0

    def _set_mode_combo_to_state(self) -> None:
        self._mode_combo_block = True
        try:
            idx = self._mode_stack_index(self._state.analysis_mode)
            self._mode_combo.setCurrentIndex(idx)
            self._params_stack.setCurrentIndex(idx)
            self._output_stack.setCurrentIndex(idx)
        finally:
            self._mode_combo_block = False

    def force_analysis_mode_off(self) -> None:
        """Set analysis combo to Off and notify listeners (e.g. after calibration/buffer reset)."""
        prev = self._state.analysis_enabled()
        self._state.analysis_mode = AnalysisMode.OFF
        self._set_mode_combo_to_state()
        self.analysis_mode_changed.emit(AnalysisMode.OFF)
        if prev:
            self.modeling_enabled_changed.emit(False)

    def _build_fit_path_hints(self) -> SessionPathHints:
        h = SessionPathHints()
        wd = self._state.watchdir
        if self._state.integrator_dir is not None:
            h.integrator_dir = str(self._state.integrator_dir.resolve())
        st = self._state.current_state()
        profile_file = self._state.default_fit_distances_profile_path()
        if profile_file is not None and profile_file.is_file():
            h.preferred_profile_dat_path = str(profile_file.resolve())
            h.one_d_profile_dir = str(profile_file.parent.resolve())
        else:
            profile_parent = None
            if st in (LiveviewState.C, LiveviewState.CD):
                ls = self._state.last_subtracted_dat_path
                if ls is not None and ls.is_file():
                    profile_parent = ls.parent
            if profile_parent is None and st in (LiveviewState.C, LiveviewState.CD):
                lip = self._state.last_integrated_dat_path
                if lip is not None and lip.is_file():
                    profile_parent = lip.parent
            if profile_parent is None and st in (LiveviewState.B, LiveviewState.BD):
                lip = self._state.last_integrated_dat_path
                if lip is not None and lip.is_file():
                    profile_parent = lip.parent
            if profile_parent is not None:
                h.one_d_profile_dir = str(profile_parent.resolve())
        av = wd / "averaged"
        if av.is_dir():
            h.integrate_output_dir = str(av.resolve())
        sub = wd / "subtracted"
        if sub.is_dir() and self._state.buffer_dat_path is not None:
            h.subtract_output_dir = str(sub.resolve())
        return h

    def _open_fit_wizard(self) -> None:
        if self._meta_fit is None:
            return
        hints = self._build_fit_path_hints()
        if self._fit_wizard is None:
            self._fit_wizard = FitDistancesWizardDialog(
                watchdir=self._state.watchdir,
                hints=hints,
                saved_form_state=_strip_fit_distances_profile_from_saved_form(
                    self._fit_distances_saved_form, self._meta_fit
                ),
                parent=self,
            )
            self._fit_wizard.finished.connect(self._persist_fit_wizard_form)
        else:
            self._fit_wizard.rebuild(hints)
        self._fit_wizard.show()
        self._fit_wizard.raise_()
        self._fit_wizard.activateWindow()

    def _open_fit_sizes_wizard(self) -> None:
        if self._meta_sizes is None:
            return
        hints = self._build_fit_path_hints()
        if self._fit_sizes_wizard is None:
            self._fit_sizes_wizard = FitSizesWizardDialog(
                watchdir=self._state.watchdir,
                hints=hints,
                saved_form_state=self._fit_sizes_saved_form,
                parent=self,
            )
            self._fit_sizes_wizard.finished.connect(self._persist_fit_sizes_wizard_form)
        else:
            self._fit_sizes_wizard.rebuild(hints)
        self._fit_sizes_wizard.show()
        self._fit_sizes_wizard.raise_()
        self._fit_sizes_wizard.activateWindow()

    def _open_fit_mixture_wizard(self) -> None:
        if self._meta_mixture is None:
            return
        hints = self._build_fit_path_hints()
        if self._fit_mixture_wizard is None:
            self._fit_mixture_wizard = FitMixtureWizardDialog(
                watchdir=self._state.watchdir,
                hints=hints,
                saved_form_state=self._fit_mixture_saved_form,
                saved_mixture_params=self._fit_mixture_saved_mixture_params,
                parent=self,
            )
            self._fit_mixture_wizard.finished.connect(self._persist_fit_mixture_wizard_form)
        else:
            self._fit_mixture_wizard.rebuild(hints)
        self._fit_mixture_wizard.show()
        self._fit_mixture_wizard.raise_()
        self._fit_mixture_wizard.activateWindow()

    def _persist_fit_wizard_form(self, _result: int = 0) -> None:
        w = self._fit_wizard
        if w is None or self._meta_fit is None:
            return
        try:
            self._fit_distances_saved_form = _strip_fit_distances_profile_from_saved_form(
                w._form.state(),  # type: ignore[attr-defined]
                self._meta_fit,
            )
        except Exception:
            pass

    def _persist_fit_sizes_wizard_form(self, _result: int = 0) -> None:
        w = self._fit_sizes_wizard
        if w is None or self._meta_sizes is None:
            return
        try:
            self._fit_sizes_saved_form = w._form.state()  # type: ignore[attr-defined]
        except Exception:
            pass

    def _persist_fit_mixture_wizard_form(self, _result: int = 0) -> None:
        w = self._fit_mixture_wizard
        if w is None or self._meta_mixture is None:
            return
        try:
            self._fit_mixture_saved_form = w._form.state()  # type: ignore[attr-defined]
            self._fit_mixture_saved_mixture_params = w.mixture_params()
        except Exception:
            pass

    def _ingest_mixture_yaml_for_saved_params(self, yml_path: Path) -> None:
        try:
            raw = yml_path.read_text(encoding="utf-8", errors="replace")
            data = yaml.safe_load(raw)
            if not isinstance(data, dict):
                return
            m = data.get("fit_mixture")
            if not isinstance(m, dict):
                m = data.get("mixture")
            if not isinstance(m, dict):
                return
            self._state.fit_mixture_options = {str(k): v for k, v in m.items()}
            keys = ("max_nph", "maxit", "r_min", "r_max", "poly_min", "poly_max")
            self._fit_mixture_saved_mixture_params = {k: m[k] for k in keys if k in m}
        except Exception:
            pass

    def _on_mode_combo_changed(self, _idx: int) -> None:
        if self._mode_combo_block:
            return
        mode = self._mode_combo.currentData()
        if not isinstance(mode, AnalysisMode):
            return
        prev = self._state.analysis_enabled()
        self._state.analysis_mode = mode
        self._params_stack.setCurrentIndex(self._mode_stack_index(mode))
        self._output_stack.setCurrentIndex(self._mode_stack_index(mode))
        self.analysis_mode_changed.emit(mode)
        now = self._state.analysis_enabled()
        if prev != now:
            self.modeling_enabled_changed.emit(now)

    def _write_mixture_sidecar(self, path: Path) -> None:
        try:
            d = self._state.watchdir / "mixture"
            d.mkdir(parents=True, exist_ok=True)
            (d / "liveview_mixture_config.txt").write_text(str(path.resolve()), encoding="utf-8")
        except Exception:
            pass

    def sync_modeling_ui_to_session_state(self) -> None:
        if self._meta_fit is None:
            return
        in_a = self._state.current_state() == LiveviewState.A
        self._state_a_placeholder.setVisible(in_a)
        self._set_mode_combo_to_state()

    def build_fit_distances_request_from_wizard(self) -> RunRequest:
        if self._fit_wizard is None:
            raise RuntimeError("Open the fit_distances wizard first")
        return self._fit_wizard.build_fit_request()

    def build_fit_sizes_request_from_wizard(self) -> RunRequest:
        if self._fit_sizes_wizard is None:
            raise RuntimeError("Open the fit_sizes wizard first")
        return self._fit_sizes_wizard.build_fit_sizes_request()

    def build_fit_mixture_request_from_wizard(self) -> RunRequest:
        if self._fit_mixture_wizard is None:
            raise RuntimeError("Open the fit_mixture wizard first")
        req = self._fit_mixture_wizard.build_fit_mixture_request()
        opts = dict(req.options)
        saved = self._state.fit_mixture_options
        if isinstance(saved, dict):
            for k, v in saved.items():
                if k not in ("output_dir", "use_cache", "config_path") and v is not None:
                    if not (isinstance(v, str) and not str(v).strip()):
                        opts[k] = v
        opts.pop("config_path", None)
        opts["use_cache"] = False
        return RunRequest(skill_name=req.skill_name, positional=list(req.positional), options=opts)

    def fit_distances_wizard_profile_text(self) -> str:
        if self._fit_wizard is None or self._meta_fit is None:
            return ""
        form = self._fit_wizard._form  # type: ignore[attr-defined]
        widgets = getattr(form, "_pos_widgets", [])
        for i, p in enumerate(self._meta_fit.positional_params):
            if p.name != "profile" or i >= len(widgets):
                continue
            w = widgets[i]
            if isinstance(w, PathField):
                parts = [normalize_pathish(x) for x in w.paths() if normalize_pathish(x)]
                if parts:
                    return parts[0].split(",")[0].strip()
                t = (w.text() or "").strip()
                return t.split(",")[0].strip() if t else ""
        return ""

    def fit_distances_wizard_has_existing_profile_file(self, watchdir: Path) -> bool:
        t = self.fit_distances_wizard_profile_text().strip()
        if not t:
            return False
        p = Path(t).expanduser()
        path = p.resolve() if p.is_absolute() else (watchdir / p).resolve()
        return path.is_file()

    def fit_sizes_wizard_profile_text(self) -> str:
        if self._fit_sizes_wizard is None or self._meta_sizes is None:
            return ""
        form = self._fit_sizes_wizard._form  # type: ignore[attr-defined]
        widgets = getattr(form, "_pos_widgets", [])
        for i, p in enumerate(self._meta_sizes.positional_params):
            if p.name != "profile" or i >= len(widgets):
                continue
            w = widgets[i]
            if isinstance(w, PathField):
                parts = [normalize_pathish(x) for x in w.paths() if normalize_pathish(x)]
                if parts:
                    return parts[0].split(",")[0].strip()
                t = (w.text() or "").strip()
                return t.split(",")[0].strip() if t else ""
        return ""

    def fit_sizes_wizard_has_existing_profile_file(self, watchdir: Path) -> bool:
        t = self.fit_sizes_wizard_profile_text().strip()
        if not t:
            return False
        p = Path(t).expanduser()
        path = p.resolve() if p.is_absolute() else (watchdir / p).resolve()
        return path.is_file()

    def fit_mixture_wizard_profile_text(self) -> str:
        if self._fit_mixture_wizard is None or self._meta_mixture is None:
            return ""
        form = self._fit_mixture_wizard._form  # type: ignore[attr-defined]
        widgets = getattr(form, "_pos_widgets", [])
        for i, p in enumerate(self._meta_mixture.positional_params):
            if p.name != "profile" or i >= len(widgets):
                continue
            w = widgets[i]
            if isinstance(w, PathField):
                parts = [normalize_pathish(x) for x in w.paths() if normalize_pathish(x)]
                if parts:
                    return parts[0].split(",")[0].strip()
                t = (w.text() or "").strip()
                return t.split(",")[0].strip() if t else ""
        return ""

    def fit_mixture_wizard_has_existing_profile_file(self, watchdir: Path) -> bool:
        t = self.fit_mixture_wizard_profile_text().strip()
        if not t:
            return False
        p = Path(t).expanduser()
        path = p.resolve() if p.is_absolute() else (watchdir / p).resolve()
        return path.is_file()

    def save_fit_distances_conf_from_open_wizard(self, *, enable_modeling: bool = True) -> None:
        if self._fit_wizard is None:
            raise RuntimeError("Open the fit_distances wizard first")
        self._persist_fit_distances_conf(self._fit_wizard, enable_modeling=enable_modeling)

    def save_fit_sizes_conf_from_open_wizard(self, *, enable_modeling: bool = True) -> None:
        if self._fit_sizes_wizard is None:
            raise RuntimeError("Open the fit_sizes wizard first")
        self._persist_fit_sizes_conf(self._fit_sizes_wizard, enable_modeling=enable_modeling)

    def save_fit_mixture_conf_from_open_wizard(self, *, enable_modeling: bool = True) -> None:
        if self._fit_mixture_wizard is None:
            raise RuntimeError("Open the fit_mixture wizard first")
        self._persist_fit_mixture_conf(self._fit_mixture_wizard, enable_modeling=enable_modeling)

    def save_fit_bodies_conf_from_open_wizard(self, *, enable_modeling: bool = True) -> None:
        if self._fit_bodies_wizard is None:
            raise RuntimeError("Open the fit_bodies (shapes) wizard first")
        w = self._fit_bodies_wizard
        shapes = w.selected_shapes()
        if not shapes:
            raise ValueError("Select at least one body model.")
        for s in shapes:
            if s not in BODIES_SHAPES_LIST:
                raise ValueError(f"Unknown bodies shape {s!r}")
        conf_dir = self._state.watchdir / "fit_bodies"
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_path = conf_dir / "fit_bodies.conf"
        conf_path.write_text(
            yaml.safe_dump({"shapes": shapes}, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )
        self._state.fit_bodies_conf_path = conf_path
        self._state.fit_bodies_shapes = list(shapes)
        self.modeling_config_changed.emit()

        if enable_modeling:
            prev_ae = self._state.analysis_enabled()
            if self._state.analysis_mode == AnalysisMode.OFF:
                self._state.analysis_mode = AnalysisMode.MONODISPERSE_BODIES
                self._set_mode_combo_to_state()
            if not prev_ae and self._state.analysis_enabled():
                self.modeling_enabled_changed.emit(True)

    def _persist_fit_distances_conf(
        self, wizard: FitDistancesWizardDialog, *, enable_modeling: bool = True
    ) -> None:
        if self._meta_fit is None:
            return
        st = wizard._form.state()  # type: ignore[attr-defined]
        opts = (st.get("options") or {}).copy()
        opts.pop("output_dir", None)
        opts.pop("use_cache", None)
        opts = {k: v for k, v in opts.items() if not (isinstance(v, str) and not v.strip())}

        conf_dir = self._state.watchdir / "fit_distances"
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_path = conf_dir / "fit_distances.conf"
        conf_path.write_text(yaml.safe_dump(opts, sort_keys=True), encoding="utf-8")
        self._state.fit_distances_conf_path = conf_path
        self.modeling_config_changed.emit()

        if enable_modeling:
            prev_ae = self._state.analysis_enabled()
            if self._state.analysis_mode == AnalysisMode.OFF:
                self._state.analysis_mode = AnalysisMode.MONODISPERSE_PR
                self._set_mode_combo_to_state()
            if not prev_ae and self._state.analysis_enabled():
                self.modeling_enabled_changed.emit(True)
        try:
            self._fit_distances_saved_form = _strip_fit_distances_profile_from_saved_form(
                wizard._form.state(),  # type: ignore[attr-defined]
                self._meta_fit,
            )
        except Exception:
            pass

    def _persist_fit_sizes_conf(
        self, wizard: FitSizesWizardDialog, *, enable_modeling: bool = True
    ) -> None:
        if self._meta_sizes is None:
            return
        st = wizard._form.state()  # type: ignore[attr-defined]
        opts = (st.get("options") or {}).copy()
        opts.pop("output_dir", None)
        opts.pop("use_cache", None)
        opts = {k: v for k, v in opts.items() if not (isinstance(v, str) and not v.strip())}

        conf_dir = self._state.watchdir / "fit_sizes"
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_path = conf_dir / "fit_sizes.conf"
        conf_path.write_text(yaml.safe_dump(opts, sort_keys=True), encoding="utf-8")
        self._state.fit_sizes_conf_path = conf_path
        self.modeling_config_changed.emit()

        if enable_modeling:
            prev_ae = self._state.analysis_enabled()
            if self._state.analysis_mode == AnalysisMode.OFF:
                self._state.analysis_mode = AnalysisMode.POLYDISPERSE_DR
                self._set_mode_combo_to_state()
            if not prev_ae and self._state.analysis_enabled():
                self.modeling_enabled_changed.emit(True)
        try:
            self._fit_sizes_saved_form = wizard._form.state()  # type: ignore[attr-defined]
        except Exception:
            pass

    def _persist_fit_mixture_conf(
        self, wizard: FitMixtureWizardDialog, *, enable_modeling: bool = True
    ) -> None:
        if self._meta_mixture is None:
            return
        from .right_wizards import fit_mixture_run_options_from_wizard

        self._state.fit_mixture_options = fit_mixture_run_options_from_wizard(wizard)
        cfg_path = wizard.write_mixture_config_yaml()
        self._state.fit_mixture_config_path = cfg_path
        self._write_mixture_sidecar(cfg_path)
        self.modeling_config_changed.emit()

        if enable_modeling:
            prev_ae = self._state.analysis_enabled()
            if self._state.analysis_mode == AnalysisMode.OFF:
                self._state.analysis_mode = AnalysisMode.POLYDISPERSE_MIXTURE
                self._set_mode_combo_to_state()
            if not prev_ae and self._state.analysis_enabled():
                self.modeling_enabled_changed.emit(True)
        try:
            self._fit_mixture_saved_form = wizard._form.state()  # type: ignore[attr-defined]
            self._fit_mixture_saved_mixture_params = wizard.mixture_params()
        except Exception:
            pass

    def set_fit_distances_running(self, running: bool) -> None:
        if self._fit_wizard is not None:
            self._fit_wizard.set_running(bool(running))
        if self._fit_sizes_wizard is not None:
            self._fit_sizes_wizard.set_running(bool(running))
        if self._fit_mixture_wizard is not None:
            self._fit_mixture_wizard.set_running(bool(running))
        if self._fit_bodies_wizard is not None:
            self._fit_bodies_wizard.set_running(bool(running))

    def _restore_modeling_conf_from_disk(self) -> None:
        if self._state.fit_distances_conf_path is not None:
            return
        wd = self._state.watchdir
        for conf in (wd / "fit_distances" / "fit_distances.conf", wd / "runs" / "fit_distances.conf"):
            if conf.is_file():
                self._state.fit_distances_conf_path = conf
                break

    def _restore_fit_sizes_conf_from_disk(self) -> None:
        if self._state.fit_sizes_conf_path is not None:
            return
        p = self._state.watchdir / "fit_sizes" / "fit_sizes.conf"
        if p.is_file():
            self._state.fit_sizes_conf_path = p

    def _ingest_bodies_conf_file(self, path: Path) -> None:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, TypeError, yaml.YAMLError):
            self._state.fit_bodies_shapes = None
            return
        if not isinstance(data, dict):
            self._state.fit_bodies_shapes = None
            return
        sh = data.get("shapes")
        if not isinstance(sh, list) or not sh:
            self._state.fit_bodies_shapes = None
            return
        out: list[str] = []
        for x in sh:
            if not isinstance(x, str):
                continue
            t = x.strip()
            if t in BODIES_SHAPES_LIST and t not in out:
                out.append(t)
        self._state.fit_bodies_shapes = out if out else None

    def _restore_bodies_conf_from_disk(self) -> None:
        if self._state.fit_bodies_conf_path is not None:
            p = self._state.fit_bodies_conf_path
            if p.is_file():
                self._ingest_bodies_conf_file(p)
            return
        p = self._state.watchdir / "fit_bodies" / "fit_bodies.conf"
        if p.is_file():
            self._state.fit_bodies_conf_path = p
            self._ingest_bodies_conf_file(p)

    def _restore_mixture_from_disk(self) -> None:
        wd = self._state.watchdir
        live = (wd / "mixture" / LIVEVIEW_MIXTURE_YML_NAME).resolve()
        if live.is_file():
            self._state.fit_mixture_config_path = live
            self._ingest_mixture_yaml_for_saved_params(live)
            return
        p = self._state.fit_mixture_config_path
        if p is not None and p.is_file():
            self._ingest_mixture_yaml_for_saved_params(p)
            return
        side = wd / "mixture" / "liveview_mixture_config.txt"
        if side.is_file():
            raw = side.read_text(encoding="utf-8", errors="replace").strip().splitlines()
            if raw:
                ext = Path(raw[0].strip()).expanduser()
                path = ext.resolve() if ext.is_absolute() else (wd / ext).resolve()
                if path.is_file():
                    self._state.fit_mixture_config_path = path
                    self._ingest_mixture_yaml_for_saved_params(path)

    def show_fit_outputs(self, *, fit_png: str, pr_png: str) -> None:
        self.ingest_skill_result(
            {"fit_vs_exp_png_path": fit_png or "", "best_pr_png_path": pr_png or ""}
        )

    def ingest_skill_result(self, result: dict) -> None:
        if not isinstance(result, dict):
            return
        if result.get("comparison_path") or result.get("distributions_path"):
            self._apply_mixture_outputs(result)
            return
        if _norm_path(result.get("best_dr_png_path")):
            self._apply_fit_sizes_outputs(result)
            return
        if _norm_path(result.get("best_pr_png_path")) or _norm_path(result.get("fit_vs_exp_png_path")):
            self._apply_gnom_pr_outputs(result)
            return
        sub = result.get("output_subdir")
        if isinstance(sub, str) and sub.strip():
            sd = Path(sub.strip()).resolve()
            if (sd / "dammif_fits.yml").is_file() or list(sd.glob("dammif-*.cif")):
                self._apply_dammif_subdir(sd)
                return
            if (sd / "bodies_fits.yml").is_file():
                self._apply_bodies_subdir(sd)
                return

    def _apply_gnom_pr_outputs(self, result: dict) -> None:
        fp = _norm_path(result.get("fit_vs_exp_png_path"))
        pp = _norm_path(result.get("best_pr_png_path"))
        pairs = (
            (self._fit_plot_pr, self._hint_fit_pr, self._pr_plot_pr, self._hint_pr_pr, True),
            (self._fit_plot_dam, self._hint_fit_dam, self._pr_plot_dam, self._hint_pr_dam, False),
        )
        for plot, hint, prplot, prhint, show_fname in pairs:
            if fp and os.path.isfile(fp):
                hint.setVisible(False)
                plot.show_path(fp, path_label_visible=show_fname)
            else:
                plot.show_path("")
                hint.setVisible(True)
            if pp and os.path.isfile(pp):
                prhint.setVisible(False)
                prplot.show_path(pp, path_label_visible=show_fname)
            else:
                prplot.show_path("")
                prhint.setVisible(True)

    def _apply_fit_sizes_outputs(self, result: dict) -> None:
        fp = _norm_path(result.get("fit_vs_exp_png_path"))
        dp = _norm_path(result.get("best_dr_png_path"))
        if fp and os.path.isfile(fp):
            self._hint_fit_dr.setVisible(False)
            self._fit_plot_dr.show_path(fp)
        else:
            self._fit_plot_dr.show_path("")
            self._hint_fit_dr.setVisible(True)
        if dp and os.path.isfile(dp):
            self._hint_dr.setVisible(False)
            self._dr_plot.show_path(dp)
        else:
            self._dr_plot.show_path("")
            self._hint_dr.setVisible(True)

    def _apply_mixture_outputs(self, result: dict) -> None:
        c = _norm_path(result.get("comparison_path"))
        d = _norm_path(result.get("distributions_path"))
        if c and os.path.isfile(c):
            self._hint_mix_c.setVisible(False)
            self._mix_comp.show_path(c)
        else:
            self._mix_comp.show_path("")
            self._hint_mix_c.setVisible(True)
        if d and os.path.isfile(d):
            self._hint_mix_d.setVisible(False)
            self._mix_dist.show_path(d)
        else:
            self._mix_dist.show_path("")
            self._hint_mix_d.setVisible(True)

    def _apply_dammif_subdir(self, sd: Path) -> None:
        pngs = list(sd.glob("*_fits.png"))
        png = pngs[0] if pngs else None
        if png is not None and png.is_file():
            self._hint_fit_dam.setVisible(False)
            self._fit_plot_dam.show_path(str(png), path_label_visible=False)
        cif = _best_dammif_cif(sd)
        self._viewer_dam.set_model_path(cif)

    def _apply_bodies_subdir(self, sd: Path) -> None:
        best_shape, best_params, csv_p = _bodies_best_fit(sd)
        plotted = False
        if csv_p and os.path.isfile(csv_p) and best_shape:
            try:
                import pandas as pd

                df = pd.read_csv(csv_p)
                if "q" in df.columns and "exp" in df.columns and best_shape in df.columns:
                    q = df["q"].to_numpy()
                    e = df["exp"].to_numpy()
                    f = df[best_shape].to_numpy()
                    self._curve_bodies.plot_two_series(q, e, q, f, label1="exp", label2=best_shape)
                    self._curve_bodies.setVisible(True)
                    self._bodies_png_fallback.setVisible(False)
                    self._hint_curve_bodies.setVisible(False)
                    plotted = True
            except Exception:
                plotted = False
        if not plotted:
            self._curve_bodies.clear()
            self._curve_bodies.setVisible(False)
            pngs = list(sd.glob("*_fits.png"))
            if pngs and pngs[0].is_file():
                self._bodies_png_fallback.show_path(str(pngs[0]))
                self._bodies_png_fallback.setVisible(True)
                self._hint_curve_bodies.setVisible(False)
            else:
                self._bodies_png_fallback.show_path("")
                self._bodies_png_fallback.setVisible(True)
                self._hint_curve_bodies.setVisible(True)
        if (
            best_shape
            and best_shape in BODIES_SHAPES_LIST
            and best_params
        ):
            self._viewer_bodies.set_bodies_analytical(
                best_shape, best_params, folder=sd
            )
        else:
            self._viewer_bodies.set_model_path(str(sd.resolve()))
