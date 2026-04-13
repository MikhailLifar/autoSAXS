from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ...logic.session_state import SessionPathHints
from ...logic.skill_catalog import discover_skills
from ...ui.run_controls import RunControls
from ...ui.skill_form import SkillForm


def _empty_hints():
    from ...logic.session_state import SessionPathHints

    return SessionPathHints()


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


def _disable_subtract_sample_field(form: SkillForm, meta) -> None:
    """Liveview supplies sample .dat from the queue; hide the sample_1d row."""
    for i, p in enumerate(meta.positional_params):
        if p.name != "sample_1d":
            continue
        if i < len(form._pos_widgets):  # type: ignore[attr-defined]
            w = form._pos_widgets[i]  # type: ignore[attr-defined]
            w.setEnabled(False)
            w.setVisible(False)
        break


class CalibrationWizardDialog(QDialog):
    reset_requested = pyqtSignal()

    def __init__(self, *, watchdir: Path, parent=None) -> None:
        super().__init__(parent)
        self._watchdir = watchdir
        self.setWindowTitle("Set calibration")
        self.setMinimumWidth(560)
        self.resize(720, 640)

        skills = {m.name: m for m in discover_skills()}
        meta = skills.get("calibrate")
        self._form = SkillForm()
        self._controls = RunControls()
        self._controls.run_button.setText("Run")
        self._meta = meta

        lay = QVBoxLayout(self)
        if meta is not None:
            out = watchdir / "calibration"
            out.mkdir(parents=True, exist_ok=True)
            self._form.set_skill(
                meta,
                workdir=watchdir,
                default_output_dir=str(out),
                hints=_empty_hints(),
                saved_state=None,
            )
            _force_no_cache_and_fixed_output(self._form, outdir=str(out))
            lay.addWidget(QLabel("Calibrate integrator (outputs go to calibration/)."))
            lay.addWidget(self._form, 1)
            lay.addWidget(self._controls)
            self._btn_reset = QPushButton("Reset")
            self._btn_reset.setToolTip(
                "Clear calibration from this session (state A), turn analysis Off, and empty this form"
            )
            self._btn_reset.clicked.connect(self._on_reset_clicked)
            rr = QHBoxLayout()
            rr.addWidget(self._btn_reset, 0, Qt.AlignLeft)
            rr.addStretch(1)
            lay.addLayout(rr)
        else:
            lay.addWidget(QLabel("calibrate skill is not available."))
            self._btn_reset = None  # type: ignore[assignment]

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        lay.addWidget(buttons)

        self._controls.run_button.clicked.connect(self._on_run_clicked)
        self._controls.cancel_button.clicked.connect(self._on_cancel_clicked)
        self._controls.copy_cli_button.clicked.connect(self._on_copy_cli)

    def _on_reset_clicked(self) -> None:
        self.reset_requested.emit()

    def reset_form_to_defaults(self) -> None:
        if self._meta is None:
            return
        out = self._watchdir / "calibration"
        out.mkdir(parents=True, exist_ok=True)
        self._form.set_skill(
            self._meta,
            workdir=self._watchdir,
            default_output_dir=str(out),
            hints=_empty_hints(),
            saved_state=None,
        )
        _force_no_cache_and_fixed_output(self._form, outdir=str(out))

    def _on_run_clicked(self) -> None:
        if self._meta is None:
            return
        parent = self.parent()
        if parent is not None and hasattr(parent, "calibration_changed"):
            getattr(parent, "calibration_changed").emit()

    def _on_cancel_clicked(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "calibration_cancel_requested"):
            getattr(parent, "calibration_cancel_requested").emit()

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

    def build_calibrate_request(self):
        return self._form.build_request()

    def set_running(self, running: bool) -> None:
        self._controls.set_running(bool(running))


class BufferWizardDialog(QDialog):
    reset_requested = pyqtSignal()

    def __init__(self, *, watchdir: Path, hints: SessionPathHints, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set buffer")
        self.setMinimumWidth(560)
        self.resize(720, 640)
        self._watchdir = watchdir

        skills = {m.name: m for m in discover_skills()}
        meta = skills.get("subtract")
        self._form = SkillForm()
        self._apply = QPushButton("Apply buffer + subtraction settings")
        self._meta = meta

        lay = QVBoxLayout(self)
        if meta is not None:
            out = watchdir / "subtracted"
            out.mkdir(parents=True, exist_ok=True)
            self._form.set_skill(
                meta,
                workdir=watchdir,
                default_output_dir=str(out),
                hints=hints,
                saved_state=None,
            )
            _force_no_cache_and_fixed_output(self._form, outdir=str(out))
            _disable_subtract_sample_field(self._form, meta)
            lay.addWidget(
                QLabel(
                    "Buffer .dat and subtract options (sample curve comes from the live queue). "
                    "When the pipeline has produced an integrated curve, the buffer field defaults to that "
                    ".dat and the file browser opens in its directory."
                )
            )
            lay.addWidget(self._form, 1)
            row = QHBoxLayout()
            row.addWidget(self._apply)
            self._btn_reset = QPushButton("Reset")
            self._btn_reset.setToolTip(
                "Clear buffer and subtraction settings (state B), turn analysis Off, and empty this form"
            )
            self._btn_reset.clicked.connect(self._on_reset_clicked)
            row.addWidget(self._btn_reset)
            row.addStretch(1)
            lay.addLayout(row)
            lay.addWidget(QLabel("These settings are kept for this session and applied to new files."))
        else:
            lay.addWidget(QLabel("subtract skill is not available."))
            self._btn_reset = None  # type: ignore[assignment]

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        buttons.accepted.connect(self.close)
        lay.addWidget(buttons)

        self._apply.clicked.connect(self._on_apply)

    def _on_reset_clicked(self) -> None:
        self.reset_requested.emit()

    def reset_form_to_empty(self, hints: SessionPathHints) -> None:
        if self._meta is None:
            return
        out = self._watchdir / "subtracted"
        out.mkdir(parents=True, exist_ok=True)
        self._form.set_skill(
            self._meta,
            workdir=self._watchdir,
            default_output_dir=str(out),
            hints=hints,
            saved_state=None,
        )
        _force_no_cache_and_fixed_output(self._form, outdir=str(out))
        _disable_subtract_sample_field(self._form, self._meta)

    def rebuild(self, hints: SessionPathHints) -> None:
        if self._meta is None:
            return
        saved = self._form.state()
        out = self._watchdir / "subtracted"
        out.mkdir(parents=True, exist_ok=True)
        self._form.set_skill(
            self._meta,
            workdir=self._watchdir,
            default_output_dir=str(out),
            hints=hints,
            saved_state=saved,
        )
        _force_no_cache_and_fixed_output(self._form, outdir=str(out))
        _disable_subtract_sample_field(self._form, self._meta)

    def _on_apply(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "_apply_buffer_from_wizard"):
            getattr(parent, "_apply_buffer_from_wizard")()

