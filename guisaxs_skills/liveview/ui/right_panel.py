from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Optional

import yaml
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.models import RunRequest
from ...logic.path_normalize import normalize_pathish
from ...logic.skill_catalog import discover_skills
from ...logic.session_state import SessionPathHints
from ...ui.preview_panel import PreviewPanel
from ..state import LiveviewSessionState, LiveviewState
from .right_wizards import FitDistancesWizardDialog


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


class LiveviewRightPanel(QWidget):
    modeling_enabled_changed = pyqtSignal(bool)
    modeling_config_changed = pyqtSignal()
    fit_distances_run_requested = pyqtSignal()
    fit_distances_cancel_requested = pyqtSignal()

    def __init__(self, *, state: LiveviewSessionState) -> None:
        super().__init__()
        self._state = state
        self._fit_wizard: FitDistancesWizardDialog | None = None
        # Session-only form snapshot for fit_distances wizard (positional + options, not output_dir lock).
        self._fit_distances_saved_form: Optional[dict[str, Any]] = None

        skills = {m.name: m for m in discover_skills()}
        self._meta_fit = skills.get("fit_distances")

        self._model_group = QGroupBox("fit_distances")
        self._state_a_placeholder = QLabel(
            "You can open the fit_distances wizard before calibration. "
            "Run/Apply saves options to fit_distances/fit_distances.conf: with a profile it runs the skill; "
            "without a profile it turns on fitting for the pipeline and stores parameters only."
        )
        self._state_a_placeholder.setWordWrap(True)

        self._enabled = QCheckBox("Enable fit_distances")
        self._open_wizard = QPushButton("Set fit_distances")

        top = QHBoxLayout()
        top.addWidget(self._enabled)
        top.addStretch(1)
        top.addWidget(self._open_wizard)

        self._hint_fit = QLabel("Fit vs exp appears after a successful modeling run.")
        self._hint_fit.setWordWrap(True)
        self._fit_plot = PreviewPanel()
        self._fit_plot.setMinimumHeight(120)

        self._hint_pr = QLabel("p(r) appears after a successful modeling run.")
        self._hint_pr.setWordWrap(True)
        self._pr_plot = PreviewPanel()
        self._pr_plot.setMinimumHeight(120)

        self._modeling_inner = QWidget()
        inner_lay = QVBoxLayout(self._modeling_inner)
        inner_lay.setContentsMargins(0, 0, 0, 0)
        inner_lay.addLayout(top)
        inner_lay.addWidget(QLabel("Fit vs exp"))
        inner_lay.addWidget(self._hint_fit)
        inner_lay.addWidget(self._fit_plot, 1)
        inner_lay.addWidget(QLabel("p(r)"))
        inner_lay.addWidget(self._hint_pr)
        inner_lay.addWidget(self._pr_plot, 1)

        model_lay = QVBoxLayout(self._model_group)
        model_lay.addWidget(self._state_a_placeholder)
        model_lay.addWidget(self._modeling_inner, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._model_group)

        self._restore_modeling_conf_from_disk()
        if self._meta_fit is None:
            self._model_group.setEnabled(False)
        self._wire()
        if self._meta_fit is not None:
            self.sync_modeling_ui_to_session_state()

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

    def _wire(self) -> None:
        self._enabled.stateChanged.connect(self._on_enabled_changed)
        self._open_wizard.clicked.connect(self._open_fit_wizard)

    def sync_modeling_ui_to_session_state(self) -> None:
        """Refresh modeling UI from session state; State A allows configuring fit_distances early."""
        if self._meta_fit is None:
            return
        in_a = self._state.current_state() == LiveviewState.A
        self._state_a_placeholder.setVisible(in_a)
        self._modeling_inner.setVisible(True)
        self._enabled.setVisible(not in_a)
        self._enabled.blockSignals(True)
        self._enabled.setChecked(bool(self._state.fit_distances_enabled))
        self._enabled.blockSignals(False)

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

    def _on_enabled_changed(self) -> None:
        enabled = bool(self._enabled.isChecked())
        self._state.fit_distances_enabled = enabled
        self.modeling_enabled_changed.emit(enabled)

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

    def build_fit_distances_request_from_wizard(self) -> RunRequest:
        if self._fit_wizard is None:
            raise RuntimeError("Open the fit_distances wizard first")
        return self._fit_wizard.build_fit_request()

    def fit_distances_wizard_profile_text(self) -> str:
        """First profile path segment (same basis as SkillForm.build_requests for the profile PathField)."""
        if self._fit_wizard is None or self._meta_fit is None:
            return ""
        from ...ui.path_field import PathField

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
        """True only if the profile field resolves to an existing file (matches build_requests input)."""
        t = self.fit_distances_wizard_profile_text().strip()
        if not t:
            return False
        p = Path(t).expanduser()
        path = p.resolve() if p.is_absolute() else (watchdir / p).resolve()
        return path.is_file()

    def save_fit_distances_conf_from_open_wizard(self, *, enable_modeling: bool = True) -> None:
        """Write fit_distances/fit_distances.conf from the open wizard; optionally check Enable fit_distances."""
        if self._fit_wizard is None:
            raise RuntimeError("Open the fit_distances wizard first")
        self._persist_fit_distances_conf(self._fit_wizard, enable_modeling=enable_modeling)

    def _persist_fit_distances_conf(
        self, wizard: FitDistancesWizardDialog, *, enable_modeling: bool = True
    ) -> None:
        st = wizard._form.state()  # type: ignore[attr-defined]
        opts = (st.get("options") or {}).copy()
        opts.pop("output_dir", None)
        opts.pop("use_cache", None)
        # Do not write blank strings for optional numerics (YAML reload + CLI used to pass --first '').
        opts = {k: v for k, v in opts.items() if not (isinstance(v, str) and not v.strip())}

        conf_dir = self._state.watchdir / "fit_distances"
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_path = conf_dir / "fit_distances.conf"
        conf_path.write_text(yaml.safe_dump(opts, sort_keys=True), encoding="utf-8")
        self._state.fit_distances_conf_path = conf_path
        self.modeling_config_changed.emit()

        if enable_modeling:
            was = self._state.fit_distances_enabled
            self._state.fit_distances_enabled = True
            if self._enabled.isVisible():
                self._enabled.blockSignals(True)
                self._enabled.setChecked(True)
                self._enabled.blockSignals(False)
            if not was:
                self.modeling_enabled_changed.emit(True)
        try:
            self._fit_distances_saved_form = _strip_fit_distances_profile_from_saved_form(
                wizard._form.state(),  # type: ignore[attr-defined]
                self._meta_fit,
            )
        except Exception:
            pass

    def set_fit_distances_running(self, running: bool) -> None:
        if self._fit_wizard is not None:
            self._fit_wizard.set_running(bool(running))

    def _restore_modeling_conf_from_disk(self) -> None:
        if self._state.fit_distances_conf_path is not None:
            return
        wd = self._state.watchdir
        for conf in (wd / "fit_distances" / "fit_distances.conf", wd / "runs" / "fit_distances.conf"):
            if conf.is_file():
                self._state.fit_distances_conf_path = conf
                break

    def show_fit_outputs(self, *, fit_png: str, pr_png: str) -> None:
        fp = (fit_png or "").strip()
        pp = (pr_png or "").strip()
        if fp and os.path.isfile(fp):
            self._hint_fit.setVisible(False)
            self._fit_plot.show_path(fp)
        else:
            self._fit_plot.show_path("")
            self._hint_fit.setVisible(True)
        if pp and os.path.isfile(pp):
            self._hint_pr.setVisible(False)
            self._pr_plot.show_path(pp)
        else:
            self._pr_plot.show_path("")
            self._hint_pr.setVisible(True)
