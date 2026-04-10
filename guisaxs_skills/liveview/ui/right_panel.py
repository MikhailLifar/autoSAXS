from __future__ import annotations

import os
from pathlib import Path

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
from ...logic.skill_catalog import discover_skills
from ...logic.session_state import SessionPathHints
from ...ui.preview_panel import PreviewPanel
from ..state import LiveviewSessionState, LiveviewState
from .right_wizards import FitDistancesWizardDialog


class LiveviewRightPanel(QWidget):
    modeling_enabled_changed = pyqtSignal(bool)
    modeling_config_changed = pyqtSignal()
    fit_distances_run_requested = pyqtSignal()
    fit_distances_cancel_requested = pyqtSignal()

    def __init__(self, *, state: LiveviewSessionState) -> None:
        super().__init__()
        self._state = state
        self._fit_wizard: FitDistancesWizardDialog | None = None

        skills = {m.name: m for m in discover_skills()}
        self._meta_fit = skills.get("fit_distances")

        self._model_group = QGroupBox("fit_distances")
        self._state_a_placeholder = QLabel(
            "Modeling is available after calibration. "
            "In State A, fit_distances is not used (see liveview spec §4.2)."
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
        profile_parent = None
        if self._state.current_state() == LiveviewState.CD:
            ls = self._state.last_subtracted_dat_path
            if ls is not None and ls.is_file():
                profile_parent = ls.parent
        if profile_parent is None:
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
        """§4.2 / invariant: modeling is never available in State A; conf on disk does not imply enabled."""
        if self._meta_fit is None:
            return
        in_a = self._state.current_state() == LiveviewState.A
        self._state_a_placeholder.setVisible(in_a)
        self._modeling_inner.setVisible(not in_a)
        if in_a:
            if self._fit_wizard is not None:
                self._fit_wizard.hide()
            was = self._state.fit_distances_enabled
            self._state.fit_distances_enabled = False
            self._enabled.blockSignals(True)
            self._enabled.setChecked(False)
            self._enabled.blockSignals(False)
            if was:
                self.modeling_enabled_changed.emit(False)

    def _open_fit_wizard(self) -> None:
        if self._meta_fit is None:
            return
        if self._state.current_state() == LiveviewState.A:
            return
        hints = self._build_fit_path_hints()
        if self._fit_wizard is None:
            self._fit_wizard = FitDistancesWizardDialog(
                watchdir=self._state.watchdir,
                hints=hints,
                session_state=self._state,
                parent=self,
            )
        else:
            self._fit_wizard.rebuild(hints, self._state)
        self._fit_wizard.show()
        self._fit_wizard.raise_()
        self._fit_wizard.activateWindow()

    def _on_enabled_changed(self) -> None:
        enabled = bool(self._enabled.isChecked())
        self._state.fit_distances_enabled = enabled
        self.modeling_enabled_changed.emit(enabled)

    def build_fit_distances_request_from_wizard(self) -> RunRequest:
        if self._fit_wizard is None:
            raise RuntimeError("Open the fit_distances wizard first")
        return self._fit_wizard.build_fit_request()

    def save_fit_distances_conf_from_open_wizard(self) -> None:
        """Write fit_distances/fit_distances.conf from the open wizard and enable modeling."""
        if self._fit_wizard is None:
            raise RuntimeError("Open the fit_distances wizard first")
        self._persist_fit_distances_conf(self._fit_wizard)

    def _persist_fit_distances_conf(self, wizard: FitDistancesWizardDialog) -> None:
        st = wizard._form.state()  # type: ignore[attr-defined]
        opts = (st.get("options") or {}).copy()
        opts.pop("output_dir", None)
        opts.pop("use_cache", None)

        conf_dir = self._state.watchdir / "fit_distances"
        conf_dir.mkdir(parents=True, exist_ok=True)
        conf_path = conf_dir / "fit_distances.conf"
        conf_path.write_text(yaml.safe_dump(opts, sort_keys=True), encoding="utf-8")
        self._state.fit_distances_conf_path = conf_path
        self.modeling_config_changed.emit()

        self._state.fit_distances_enabled = True
        self._enabled.blockSignals(True)
        self._enabled.setChecked(True)
        self._enabled.blockSignals(False)
        self.modeling_enabled_changed.emit(True)

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
