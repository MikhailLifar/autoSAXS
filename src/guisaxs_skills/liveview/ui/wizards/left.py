from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...services.calibration.storage import calibration_subdir, ensure_tiff_in_calibration
from ....core.models import RunRequest
from ....logic.session_state import SessionPathHints
from ....logic.skill_catalog import discover_skills
from ....ui.path_field import PathField
from ....ui.skill_form import SkillForm
from ..skill_form_utils import (
    liveview_run_controls,
    liveview_skill_form,
    normalize_calibrate_mask_mode,
    prepare_liveview_calibrate_form,
    prepare_liveview_subtract_form,
)
from ..widgets.plots import DropTiffImageCanvas, LogCurvePlot, open_dat_curve_dialog
from PyQt5.QtWidgets import QLineEdit
from ..widgets.plots import mpl_navigation_toolbar


def _empty_hints():
    from ....logic.session_state import SessionPathHints

    return SessionPathHints()


class _AspectPlotHost(QWidget):
    """Center a plot canvas at a fixed width/height aspect so it does not stretch oddly."""

    def __init__(self, child: QWidget, *, aspect: float = 1.55) -> None:
        super().__init__()
        self._child = child
        self._aspect = max(0.5, float(aspect))
        self._child.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._child, 0, Qt.AlignCenter)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addStretch(1)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        avail_w = max(1, int(self.width()) - 8)
        avail_h = max(1, int(self.height()) - 8)
        # Fit largest rectangle of the target aspect inside the host.
        w = avail_w
        h = int(round(w / self._aspect))
        if h > avail_h:
            h = avail_h
            w = int(round(h * self._aspect))
        w = max(240, w)
        h = max(160, h)
        self._child.setFixedSize(w, h)


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
    attention_context_changed = pyqtSignal()

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
        self._form = liveview_skill_form()
        self._controls = liveview_run_controls()
        self._controls.run_button.setText("Run")
        self._meta = meta
        self._viewer = DropTiffImageCanvas()
        self._viewer_toolbar = None
        self._mask_wizard = None
        self._run_coach_dismissed = False
        self._close_coach_armed = False
        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.close)

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
            prepare_liveview_calibrate_form(self._form, outdir=str(out))
            lay.addWidget(QLabel("Calibrate integrator (outputs go to calibration/)."))
            splitter = QSplitter(Qt.Horizontal)
            splitter.setChildrenCollapsible(False)
            viewer_wrap = QWidget()
            viewer_lay = QVBoxLayout(viewer_wrap)
            viewer_lay.setContentsMargins(0, 0, 0, 0)
            self._viewer_toolbar = mpl_navigation_toolbar(self._viewer, viewer_wrap)
            viewer_lay.addWidget(self._viewer_toolbar, 0)
            # Host frame so coaching can draw a CSS border around the canvas
            # (matplotlib canvases ignore QGraphicsEffect).
            self._viewer_host = QWidget()
            self._viewer_host.setAttribute(Qt.WA_StyledBackground, True)
            host_lay = QVBoxLayout(self._viewer_host)
            host_lay.setContentsMargins(6, 6, 6, 6)
            host_lay.addWidget(self._viewer, 1)
            viewer_lay.addWidget(self._viewer_host, 1)
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
            rr.addWidget(self._btn_close, 0, Qt.AlignRight)
            lay.addLayout(rr)
        else:
            lay.addWidget(QLabel("calibrate skill is not available."))
            self._btn_reset = None  # type: ignore[assignment]
            lay.addWidget(self._btn_close, 0, Qt.AlignRight)

        self._controls.run_button.clicked.connect(self._on_run_clicked)
        self._controls.cancel_button.clicked.connect(self._on_cancel_clicked)
        if meta is not None:
            self._wire_viewer_updates()
            self._btn_create_mask.clicked.connect(self._open_mask_wizard)  # type: ignore[attr-defined]
            self._viewer.mpl_connect("button_press_event", self._on_viewer_click_open_mask)
            self._viewer.tiff_files_dropped.connect(self._on_tiff_dropped_to_viewer)
            self._refresh_viewer_from_form()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.attention_context_changed.emit()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self.attention_context_changed.emit()

    def has_calibrant_image(self) -> bool:
        f = self._calib_image_field()
        return bool(f is not None and f.text().strip())

    def has_mask(self) -> bool:
        f = self._mask_field()
        return bool(f is not None and f.text().strip())

    def run_coach_dismissed(self) -> bool:
        return bool(self._run_coach_dismissed)

    def arm_close_coach(self) -> None:
        self._close_coach_armed = True

    def close_coach_armed(self) -> bool:
        return bool(self._close_coach_armed)

    def close_button(self):
        return self._btn_close

    def mask_browse_button(self):
        f = self._mask_field()
        return f.browse_button if f is not None else None

    def create_mask_button(self):
        return getattr(self, "_btn_create_mask", None)

    def run_button(self):
        return self._controls.run_button

    def drop_canvas(self):
        """Widget to coach for the empty-TIFF step (host frame + canvas pulse)."""
        return getattr(self, "_viewer_host", None) or getattr(self, "_viewer", None)

    def drop_hint_canvas(self):
        return getattr(self, "_viewer", None)

    def mask_wizard(self):
        return self._mask_wizard

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
        self.attention_context_changed.emit()

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
            try:
                f.path_changed.disconnect(self._refresh_viewer_from_form)
            except TypeError:
                pass
            try:
                f.path_changed.disconnect(self._on_coach_path_changed)
            except TypeError:
                pass
            f.path_changed.connect(self._refresh_viewer_from_form)
            f.path_changed.connect(self._on_coach_path_changed)
            f.set_browse_start_dir(str(calibration_subdir(self._watchdir)))
        mask_f = self._mask_field()
        if mask_f is not None:
            try:
                mask_f.path_changed.disconnect(self._on_coach_path_changed)
            except TypeError:
                pass
            mask_f.path_changed.connect(self._on_coach_path_changed)
            mask_f.set_browse_start_dir(str(calibration_subdir(self._watchdir)))

    def _on_coach_path_changed(self) -> None:
        self._run_coach_dismissed = False
        self.attention_context_changed.emit()

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
            self._mask_wizard.attention_context_changed.connect(self.attention_context_changed.emit)
            self._mask_wizard.mask_committed.connect(self._on_mask_committed)
        else:
            self._mask_wizard.set_defaults(
                image_path=calib_path,
                mask_path=mask_path,
            )
        self._mask_wizard.show()
        self._mask_wizard.raise_()
        self._mask_wizard.activateWindow()
        self.attention_context_changed.emit()

    def _on_mask_committed(self, path: str) -> None:
        chosen = (path or "").strip()
        mask_field = self._mask_field()
        if chosen and mask_field is not None:
            mask_field.set_text(chosen)
            mm = self._mask_mode_field()
            if mm is not None and not mm.text().strip():
                mm.setText("from_file")
        self._run_coach_dismissed = False
        self.attention_context_changed.emit()

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
        prepare_liveview_calibrate_form(self._form, outdir=str(out))
        self._wire_viewer_updates()
        self._refresh_viewer_from_form()
        self._run_coach_dismissed = False
        self._close_coach_armed = False
        self.attention_context_changed.emit()

    def _on_run_clicked(self) -> None:
        if self._meta is None:
            return
        self._run_coach_dismissed = True
        self._close_coach_armed = False
        self.attention_context_changed.emit()
        parent = self.parent()
        if parent is not None and hasattr(parent, "calibration_changed"):
            getattr(parent, "calibration_changed").emit()

    def _on_cancel_clicked(self) -> None:
        parent = self.parent()
        if parent is not None and hasattr(parent, "calibration_cancel_requested"):
            getattr(parent, "calibration_cancel_requested").emit()

    def build_calibrate_request(self):
        req = self._form.build_request()
        opts = dict(req.options or {})
        mm = normalize_calibrate_mask_mode(opts.get("mask_mode") if isinstance(opts.get("mask_mode"), str) else None)
        if mm is not None:
            opts["mask_mode"] = mm
        elif "mask_mode" in opts:
            opts.pop("mask_mode", None)
        return RunRequest(skill_name=req.skill_name, positional=list(req.positional), options=opts)

    def set_running(self, running: bool) -> None:
        self._controls.set_running(bool(running))


class BufferWizardDialog(QDialog):
    reset_requested = pyqtSignal()
    attention_context_changed = pyqtSignal()

    def __init__(self, *, watchdir: Path, hints: SessionPathHints, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set buffer")
        self.setWindowFlags(
            Qt.Window
            | Qt.CustomizeWindowHint
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowCloseButtonHint
            | Qt.WindowMinMaxButtonsHint
        )
        self.setSizeGripEnabled(True)
        try:
            scr = QGuiApplication.primaryScreen()
            geo = scr.availableGeometry() if scr is not None else None
            if geo is not None:
                w = max(1000, int(0.72 * int(geo.width())))
                h = max(560, int(0.58 * int(geo.height())))
                self.resize(w, h)
                self.setMinimumSize(900, 520)
        except Exception:
            self.setMinimumWidth(900)
            self.resize(1120, 640)
        self._watchdir = watchdir
        self._dat_viewer = None
        self._apply_coach_dismissed = False
        self._close_coach_armed = False
        self._btn_close = QPushButton("Close")
        self._btn_close.clicked.connect(self.close)

        skills = {m.name: m for m in discover_skills()}
        meta = skills.get("subtract")
        self._form = liveview_skill_form()
        self._apply = QPushButton("Apply subtraction settings")
        self._meta = meta
        self._plot = LogCurvePlot()
        self._plot.mpl_connect("button_press_event", self._on_plot_click)
        self._splitter: QSplitter | None = None

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
            prepare_liveview_subtract_form(self._form, outdir=str(out))
            _disable_subtract_sample_field(self._form, meta)
            self._wire_form_plot_updates()

            splitter = QSplitter(Qt.Horizontal)
            splitter.setChildrenCollapsible(False)
            self._splitter = splitter
            left = QWidget()
            left_lay = QVBoxLayout(left)
            left_lay.setContentsMargins(0, 0, 0, 0)
            left_lay.addWidget(QLabel("Buffer curve (click to enlarge)"))
            left_lay.addWidget(_AspectPlotHost(self._plot, aspect=1.55), 1)
            splitter.addWidget(left)

            right = QWidget()
            right.setMinimumWidth(340)
            right_lay = QVBoxLayout(right)
            right_lay.setContentsMargins(0, 0, 0, 0)
            right_lay.addWidget(self._form, 1)
            row = QHBoxLayout()
            row.addWidget(self._apply)
            self._btn_reset = QPushButton("Reset")
            self._btn_reset.setToolTip(
                "Clear buffer and subtraction settings (state B), turn analysis Off, and empty this form"
            )
            self._btn_reset.clicked.connect(self._on_reset_clicked)
            row.addWidget(self._btn_reset)
            row.addStretch(1)
            row.addWidget(self._btn_close)
            right_lay.addLayout(row)
            right_lay.addWidget(QLabel("These settings are kept for this session and applied to new files."))
            splitter.addWidget(right)
            splitter.setStretchFactor(0, 3)
            splitter.setStretchFactor(1, 2)
            lay.addWidget(splitter, 1)
            self._attach_options_help_button()
            self._refresh_buffer_plot()
        else:
            lay.addWidget(QLabel("subtract skill is not available."))
            self._btn_reset = None  # type: ignore[assignment]
            lay.addWidget(self._btn_close, 0, Qt.AlignRight)

        self._apply.clicked.connect(self._on_apply)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        sp = self._splitter
        if sp is not None:
            total = int(sp.width()) or int(self.width())
            if total >= 400:
                left = int(round(total * 0.58))
                right = max(340, total - left)
                left = total - right
                try:
                    sp.setSizes([left, right])
                except Exception:
                    pass
        self.attention_context_changed.emit()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self.attention_context_changed.emit()

    def has_buffer_path(self) -> bool:
        return bool(self._buffer_path_text())

    def has_q_range(self) -> bool:
        q_min, q_max = self._q_range()
        return q_min is not None and q_max is not None

    def apply_coach_dismissed(self) -> bool:
        return bool(self._apply_coach_dismissed)

    def arm_close_coach(self) -> None:
        self._close_coach_armed = True

    def close_coach_armed(self) -> bool:
        return bool(self._close_coach_armed)

    def close_button(self):
        return self._btn_close

    def buffer_browse_button(self):
        f = self._buffer_field()
        return f.browse_button if f is not None else None

    def q_min_field(self):
        w = self._form._opt_fields.get("q_min")  # type: ignore[attr-defined]
        return w if isinstance(w, QLineEdit) else None

    def q_max_field(self):
        w = self._form._opt_fields.get("q_max")  # type: ignore[attr-defined]
        return w if isinstance(w, QLineEdit) else None

    def apply_button(self):
        return self._apply

    def _wire_form_plot_updates(self) -> None:
        buf = self._buffer_field()
        if buf is not None:
            try:
                buf.path_changed.disconnect(self._refresh_buffer_plot)
            except TypeError:
                pass
            try:
                buf.path_changed.disconnect(self._on_coach_inputs_changed)
            except TypeError:
                pass
            buf.path_changed.connect(self._refresh_buffer_plot)
            buf.path_changed.connect(self._on_coach_inputs_changed)
        for name in ("q_min", "q_max"):
            w = self._form._opt_fields.get(name)  # type: ignore[attr-defined]
            if isinstance(w, QLineEdit):
                try:
                    w.textChanged.disconnect(self._on_q_text_changed)
                except TypeError:
                    pass
                try:
                    w.textChanged.disconnect(self._on_coach_inputs_changed)
                except TypeError:
                    pass
                w.textChanged.connect(self._on_q_text_changed)
                w.textChanged.connect(self._on_coach_inputs_changed)

    def _on_coach_inputs_changed(self, *_args) -> None:
        self._apply_coach_dismissed = False
        self.attention_context_changed.emit()

    def _on_q_text_changed(self, _text: str = "") -> None:
        self._refresh_buffer_plot()

    def _buffer_field(self) -> PathField | None:
        if self._meta is None:
            return None
        for i, p in enumerate(self._meta.positional_params):
            if p.name != "buffer_1d":
                continue
            widgets = getattr(self._form, "_pos_widgets", [])
            if i < len(widgets) and isinstance(widgets[i], PathField):
                return widgets[i]
        return None

    def _buffer_path_text(self) -> str:
        f = self._buffer_field()
        return f.text().strip() if f is not None else ""

    def _q_range(self) -> tuple[object, object]:
        def _read(name: str):
            w = self._form._opt_fields.get(name)  # type: ignore[attr-defined]
            if not isinstance(w, QLineEdit):
                return None
            t = w.text().strip()
            if not t:
                return None
            try:
                return float(t)
            except ValueError:
                return None

        return _read("q_min"), _read("q_max")

    def _refresh_buffer_plot(self) -> None:
        path = self._buffer_path_text()
        q_min, q_max = self._q_range()
        if not path:
            self._plot.clear()
            return
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            resolved = (self._watchdir / resolved).resolve()
        if not resolved.is_file():
            self._plot.clear()
            return
        try:
            self._plot.plot_dat(str(resolved), q_min=q_min, q_max=q_max)
        except Exception:
            self._plot.clear()
        if self._dat_viewer is not None and self._dat_viewer.isVisible():
            open_dat_curve_dialog(
                self,
                str(resolved),
                reuse=self._dat_viewer,
                q_min=q_min,
                q_max=q_max,
                window_title="Buffer curve",
            )

    def _on_plot_click(self, ev: object) -> None:
        if getattr(ev, "inaxes", None) is None:
            return
        if int(getattr(ev, "button", 0)) != 1:
            return
        path = self._buffer_path_text()
        if not path:
            return
        resolved = Path(path).expanduser()
        if not resolved.is_absolute():
            resolved = (self._watchdir / resolved).resolve()
        if not resolved.is_file():
            return
        q_min, q_max = self._q_range()
        self._dat_viewer = open_dat_curve_dialog(
            self,
            str(resolved),
            reuse=self._dat_viewer,
            q_min=q_min,
            q_max=q_max,
            window_title="Buffer curve",
        )

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
        prepare_liveview_subtract_form(self._form, outdir=str(out))
        _disable_subtract_sample_field(self._form, self._meta)
        self._wire_form_plot_updates()
        self._attach_options_help_button()
        self._refresh_buffer_plot()
        self._apply_coach_dismissed = False
        self._close_coach_armed = False
        self.attention_context_changed.emit()

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
        prepare_liveview_subtract_form(self._form, outdir=str(out))
        _disable_subtract_sample_field(self._form, self._meta)
        self._wire_form_plot_updates()
        self._attach_options_help_button()
        self._refresh_buffer_plot()
        self.attention_context_changed.emit()

    def _attach_options_help_button(self) -> None:
        """Standard ``?`` help control at the top-right of the Options group."""
        btn = QPushButton("?")
        btn.setObjectName("helpButton")
        btn.setFixedSize(22, 22)
        btn.setToolTip("Auto-scale / q-range help")
        btn.clicked.connect(self._on_options_help)
        host = QWidget()
        hl = QHBoxLayout(host)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addStretch(1)
        hl.addWidget(btn, 0, Qt.AlignRight)
        self._form._opt_layout.insertRow(0, host)  # type: ignore[attr-defined]

    def _on_options_help(self) -> None:
        QMessageBox.information(
            self,
            "Options",
            "Auto-scale relies on Porod and linear approximations of SAXS data in higher q region. "
            "q min and q max are the boundaries of the region where approximations hold.\n"
            "q max is also a point where the algorithm matches buffer and sample. "
            'Choose it close to the "knee" of a SAXS curve',
        )

    def _on_apply(self) -> None:
        self._apply_coach_dismissed = True
        self._close_coach_armed = False
        self.attention_context_changed.emit()
        parent = self.parent()
        if parent is not None and hasattr(parent, "_apply_buffer_from_wizard"):
            getattr(parent, "_apply_buffer_from_wizard")()
