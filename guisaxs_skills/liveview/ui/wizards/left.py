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
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...services.calibration.storage import calibration_subdir, ensure_tiff_in_calibration
from ....logic.session_state import SessionPathHints
from ....logic.skill_catalog import discover_skills
from ....ui.path_field import PathField
from ....ui.run_controls import RunControls
from ....ui.skill_form import SkillForm
from ..widgets.plots import DropTiffImageCanvas
from PyQt5.QtWidgets import QLineEdit
from ..widgets.plots import mpl_navigation_toolbar


def _empty_hints():
    from ....logic.session_state import SessionPathHints

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
            try:
                lbl = form._pos_layout.labelForField(w)  # type: ignore[attr-defined]
                if lbl is not None:
                    lbl.setEnabled(False)
                    lbl.setVisible(False)
            except Exception:
                pass
            w.setEnabled(False)
            w.setVisible(False)
        break


class CalibrationWizardDialog(QDialog):
    reset_requested = pyqtSignal()

    def __init__(self, *, watchdir: Path, parent=None) -> None:
        super().__init__(parent)
        self._watchdir = watchdir
        self.setWindowTitle("Set calibration")
        # Make it a true top-level window (not a "dialog" window type) so WMs show min/max controls.
        self.setWindowFlags(
            Qt.Window
            | Qt.CustomizeWindowHint
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowCloseButtonHint
            | Qt.WindowMinMaxButtonsHint
        )
        self.setSizeGripEnabled(True)
        # Default size: 75% width, 80% height of available screen.
        try:
            scr = QGuiApplication.primaryScreen()
            geo = scr.availableGeometry() if scr is not None else None
            if geo is not None:
                w = max(980, int(0.75 * int(geo.width())))
                h = max(720, int(0.90 * int(geo.height())))
                self.resize(w, h)
                self.setMinimumSize(860, 640)
        except Exception:
            self.setMinimumWidth(980)
            self.resize(1260, 820)

        skills = {m.name: m for m in discover_skills()}
        meta = skills.get("calibrate")
        self._form = SkillForm()
        self._controls = RunControls()
        self._controls.run_button.setText("Run")
        self._meta = meta
        self._viewer = DropTiffImageCanvas()
        self._viewer_toolbar = None
        self._mask_wizard = None

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
            lay.addWidget(
                QLabel(
                    "Calibrate integrator (outputs go to calibration/). "
                    "config_path is optional — leave empty to use bundled autosaxs defaults."
                )
            )
            splitter = QSplitter(Qt.Horizontal)
            splitter.setChildrenCollapsible(False)
            viewer_wrap = QWidget()
            viewer_lay = QVBoxLayout(viewer_wrap)
            viewer_lay.setContentsMargins(0, 0, 0, 0)
            self._viewer_toolbar = mpl_navigation_toolbar(self._viewer, viewer_wrap)
            viewer_lay.addWidget(self._viewer_toolbar, 0)
            viewer_lay.addWidget(self._viewer, 1)
            splitter.addWidget(viewer_wrap)

            right = QWidget()
            right_lay = QVBoxLayout(right)
            right_lay.setContentsMargins(0, 0, 0, 0)
            top_row = QHBoxLayout()
            top_row.addStretch(1)
            self._btn_create_mask = QPushButton("Create mask")
            self._btn_create_mask.setToolTip("Create or edit a mask for this calibration image")
            top_row.addWidget(self._btn_create_mask, 0, Qt.AlignRight)
            right_lay.addLayout(top_row)
            right_lay.addWidget(self._form, 1)
            splitter.addWidget(right)
            splitter.setStretchFactor(0, 2)
            splitter.setStretchFactor(1, 1)

            lay.addWidget(splitter, 1)

            lay.addWidget(self._controls, 0)
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
        if meta is not None:
            self._wire_viewer_updates()
            self._btn_create_mask.clicked.connect(self._open_mask_wizard)  # type: ignore[attr-defined]
            self._viewer.mpl_connect("button_press_event", self._on_viewer_click_open_mask)
            self._viewer.tiff_files_dropped.connect(self._on_tiff_dropped_to_viewer)
            self._refresh_viewer_from_form()

    def _on_tiff_dropped_to_viewer(self, paths_obj: object) -> None:
        if not isinstance(paths_obj, list):
            return
        paths = [p for p in paths_obj if isinstance(p, str) and p.strip()]
        if not paths:
            return
        f = self._calib_image_field()
        if f is None:
            return
        try:
            stored = ensure_tiff_in_calibration(self._watchdir, paths[0])
        except (OSError, FileNotFoundError) as e:
            QMessageBox.warning(self, "Calibration image", str(e))
            return
        f.set_text(stored)
        f.set_browse_start_dir(str(calibration_subdir(self._watchdir)))
        self._refresh_viewer_from_form()

    def _calib_image_field(self) -> PathField | None:
        try:
            w0 = self._form._pos_widgets[0]  # type: ignore[attr-defined]
        except Exception:
            return None
        return w0 if isinstance(w0, PathField) else None

    def _mask_field(self) -> PathField | None:
        try:
            w = self._form._opt_fields.get("mask")  # type: ignore[attr-defined]
        except Exception:
            return None
        return w if isinstance(w, PathField) else None

    def _mask_mode_field(self):
        try:
            w = self._form._opt_fields.get("mask_mode")  # type: ignore[attr-defined]
        except Exception:
            return None
        return w if isinstance(w, QLineEdit) else None

    def _wire_viewer_updates(self) -> None:
        f = self._calib_image_field()
        if f is not None:
            f.path_changed.connect(self._refresh_viewer_from_form)
            f.set_browse_start_dir(str(calibration_subdir(self._watchdir)))
        mask_f = self._mask_field()
        if mask_f is not None:
            mask_f.set_browse_start_dir(str(calibration_subdir(self._watchdir)))

    def _refresh_viewer_from_form(self) -> None:
        f = self._calib_image_field()
        path = f.text().strip() if f is not None else ""
        if not path:
            self._viewer.clear()
            return
        try:
            self._viewer.show_tiff(path)
        except Exception:
            self._viewer.clear()

    def _is_left_click_in_axes(self, ev: object) -> bool:
        if getattr(ev, "inaxes", None) is None:
            return False
        return int(getattr(ev, "button", 0)) == 1

    def _on_viewer_click_open_mask(self, ev: object) -> None:
        if not self._is_left_click_in_axes(ev):
            return
        # Don't open mask wizard when user is zooming/panning.
        tb = self._viewer_toolbar
        if tb is not None and str(getattr(tb, "mode", "") or ""):
            return
        f = self._calib_image_field()
        if f is None or not f.text().strip():
            return
        self._open_mask_wizard()

    def _open_mask_wizard(self) -> None:
        # Lazy import to avoid circular imports while editing.
        from .mask import MaskWizardDialog  # type: ignore

        calib_field = self._calib_image_field()
        mask_field = self._mask_field()
        calib_path = calib_field.text().strip() if calib_field is not None else ""
        mask_path = mask_field.text().strip() if mask_field is not None else ""
        if self._mask_wizard is None:
            self._mask_wizard = MaskWizardDialog(
                watchdir=self._watchdir,
                default_image_path=calib_path,
                default_mask_path=mask_path,
                parent=self,
            )
        else:
            self._mask_wizard.set_defaults(
                image_path=calib_path,
                mask_path=mask_path,
            )
        if self._mask_wizard.exec_() == QDialog.Accepted:
            pass
        # Update mask field if the wizard saved something, regardless of accept/reject.
        chosen = self._mask_wizard.saved_mask_path()
        if chosen and mask_field is not None:
            mask_field.set_text(chosen)
            mm = self._mask_mode_field()
            if mm is not None and not mm.text().strip():
                mm.setText("from_file")

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
        self._wire_viewer_updates()
        self._refresh_viewer_from_form()

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

