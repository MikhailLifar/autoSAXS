from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from ...core.models import RunRequest
from ...logic.skill_catalog import discover_skills
from ...logic.session_state import SessionPathHints
from ...ui.path_field import PathField
from ...ui.run_controls import RunControls
from ...ui.skill_form import SkillForm
from ..state import LiveviewSessionState


def _force_no_cache_and_fixed_output(form: SkillForm, *, outdir: str) -> None:
    try:
        cb = form._opt_fields.get("use_cache")  # type: ignore[attr-defined]
        if cb is not None:
            cb.setChecked(False)
            cb.setEnabled(False)
    except Exception:
        pass
    try:
        out = form._opt_fields.get("output_dir")  # type: ignore[attr-defined]
        if out is not None:
            out.set_text(outdir)
            out.setEnabled(False)
    except Exception:
        pass


class FitDistancesWizardDialog(QDialog):
    def __init__(
        self,
        *,
        watchdir: Path,
        hints: SessionPathHints,
        session_state: LiveviewSessionState,
        saved_form_state: Optional[dict[str, Any]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set fit_distances")
        self.setMinimumWidth(560)
        self.resize(720, 640)
        self._watchdir = watchdir

        skills = {m.name: m for m in discover_skills()}
        meta = skills.get("fit_distances")
        self._form = SkillForm()
        self._controls = RunControls()
        self._controls.run_button.setText("Run")
        self._meta = meta

        lay = QVBoxLayout(self)
        if meta is not None:
            out = watchdir / "fit_distances"
            out.mkdir(parents=True, exist_ok=True)
            self._form.set_skill(
                meta,
                workdir=watchdir,
                default_output_dir=str(out),
                hints=hints,
                saved_state=saved_form_state,
            )
            _force_no_cache_and_fixed_output(self._form, outdir=str(out))
            self._prime_profile_default(session_state)
            lay.addWidget(
                QLabel(
                    "Profile .dat — default: last integrated curve in states B/BD; "
                    "last subtracted curve in states C/CD (falls back to integrated if no subtraction yet). "
                    "Run saves fit_distances/fit_distances.conf, enables modeling, and runs fit_distances "
                    "(outputs under fit_distances/). "
                    "New .tif files: integrate → fit_distances (BD) or integrate → subtract → fit_distances (CD)."
                )
            )
            lay.addWidget(self._form, 1)
            lay.addWidget(self._controls)
        else:
            lay.addWidget(QLabel("fit_distances skill is not available."))

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        lay.addWidget(buttons)

        self._controls.run_button.clicked.connect(self._on_run_clicked)
        self._controls.cancel_button.clicked.connect(self._on_cancel_clicked)
        self._controls.copy_cli_button.clicked.connect(self._on_copy_cli)

    def rebuild(self, hints: SessionPathHints, session_state: LiveviewSessionState) -> None:
        if self._meta is None:
            return
        saved = None
        par = self.parent()
        if par is not None:
            saved = getattr(par, "_fit_distances_saved_form", None)
        out = self._watchdir / "fit_distances"
        out.mkdir(parents=True, exist_ok=True)
        self._form.set_skill(
            self._meta,
            workdir=self._watchdir,
            default_output_dir=str(out),
            hints=hints,
            saved_state=saved,
        )
        _force_no_cache_and_fixed_output(self._form, outdir=str(out))
        self._prime_profile_default(session_state)

    def _prime_profile_default(self, session_state: LiveviewSessionState) -> None:
        if self._meta is None:
            return
        lp = session_state.default_fit_distances_profile_path()
        if lp is None:
            return
        for i, p in enumerate(self._meta.positional_params):
            if p.name != "profile":
                continue
            widgets = getattr(self._form, "_pos_widgets", [])
            if i < len(widgets):
                w = widgets[i]
                if isinstance(w, PathField) and not w.text().strip():
                    w.set_text(str(lp.resolve()))
            break

    def build_fit_request(self) -> RunRequest:
        if self._meta is None:
            raise RuntimeError("fit_distances skill is not available")
        return self._form.build_request()

    def set_running(self, running: bool) -> None:
        if self._meta is not None:
            self._controls.set_running(bool(running))

    def _on_run_clicked(self) -> None:
        if self._meta is None:
            return
        parent = self.parent()
        if parent is not None and hasattr(parent, "fit_distances_run_requested"):
            getattr(parent, "fit_distances_run_requested").emit()

    def _on_cancel_clicked(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "fit_distances_cancel_requested"):
            getattr(parent, "fit_distances_cancel_requested").emit()

    def _on_copy_cli(self) -> None:
        if self._meta is None:
            return
        try:
            req = self._form.build_request()
        except Exception as e:
            QMessageBox.critical(self, "Cannot build request", str(e))
            return
        text = "autosaxs " + " ".join(req.cli_argv())
        QGuiApplication.clipboard().setText(text)
