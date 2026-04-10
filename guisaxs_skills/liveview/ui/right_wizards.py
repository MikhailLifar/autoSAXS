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


def _clear_fit_distances_profile_field(form: SkillForm, meta) -> None:
    """Liveview: never pre-fill or restore profile path (options only); browse dirs still come from hints."""
    if meta is None:
        return
    for i, p in enumerate(meta.positional_params):
        if p.name != "profile":
            continue
        widgets = getattr(form, "_pos_widgets", [])
        if i < len(widgets):
            w = widgets[i]
            if isinstance(w, PathField):
                w.set_text("")
        break


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
        self._controls.run_button.setText("Run/Apply")
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
            _clear_fit_distances_profile_field(self._form, meta)
            lay.addWidget(
                QLabel(
                    "Run/Apply always writes fit_distances/fit_distances.conf and turns on pipeline fitting "
                    "(same as Enable fit_distances). If the profile field points to an existing .dat file, "
                    "fit_distances runs immediately on that file; otherwise only parameters are stored and the "
                    "queue uses subtracted or integrated curves when applicable (CD / BD)."
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

    def rebuild(self, hints: SessionPathHints) -> None:
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
        _clear_fit_distances_profile_field(self._form, self._meta)
        self._controls.run_button.setText("Run/Apply")

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
