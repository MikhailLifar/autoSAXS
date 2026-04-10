from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import (
    QGroupBox,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...logic.session_state import SessionPathHints
from ...ui.preview_panel import PreviewPanel
from ..state import LiveviewSessionState
from .left_wizards import BufferWizardDialog, CalibrationWizardDialog


def pick_calibration_curve_image_path(result: Dict[str, Any]) -> str:
    """Return path to a calibration curve PNG from a calibrate skill result, or ""."""
    p = result.get("calibration_curve_plot_path")
    if isinstance(p, str) and os.path.isfile(p):
        return p
    pngs = result.get("calibration_pngs")
    if isinstance(pngs, list):
        curve_first: list[str] = []
        rest: list[str] = []
        for cand in pngs:
            if not isinstance(cand, str) or not os.path.isfile(cand):
                continue
            low = cand.lower()
            if "curve" in low or "calib" in low:
                curve_first.append(cand)
            else:
                rest.append(cand)
        for group in (curve_first, rest):
            if group:
                return group[0]
    return ""


class LiveviewLeftPanel(QWidget):
    calibration_changed = pyqtSignal()
    calibration_cancel_requested = pyqtSignal()
    subtract_config_changed = pyqtSignal()

    def __init__(self, *, state: LiveviewSessionState) -> None:
        super().__init__()
        self._state = state
        self._cal_wizard: CalibrationWizardDialog | None = None
        self._buf_wizard: BufferWizardDialog | None = None

        self._cal_group = QGroupBox("Calibration")
        self._cal_open = QPushButton("Set calibration")
        self._cal_hint = QLabel("Run calibration from the wizard to see the curve plot.")
        self._cal_hint.setWordWrap(True)
        self._cal_preview = PreviewPanel()
        self._cal_preview.setMinimumHeight(140)
        cal_lay = QVBoxLayout(self._cal_group)
        cal_lay.addWidget(self._cal_open)
        cal_lay.addWidget(self._cal_hint)
        cal_lay.addWidget(self._cal_preview, 1)

        self._buf_group = QGroupBox("Buffer")
        self._buf_open = QPushButton("Set buffer")
        self._buf_hint = QLabel("Choose buffer .dat and options in the wizard, then Apply.")
        self._buf_hint.setWordWrap(True)
        self._buf_preview = PreviewPanel()
        self._buf_preview.setMinimumHeight(140)
        buf_lay = QVBoxLayout(self._buf_group)
        buf_lay.addWidget(self._buf_open)
        buf_lay.addWidget(self._buf_hint)
        buf_lay.addWidget(self._buf_preview, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._cal_group)
        lay.addWidget(self._buf_group)
        lay.addStretch(1)

        self._cal_open.clicked.connect(self._open_calibration_wizard)
        self._buf_open.clicked.connect(self._open_buffer_wizard)

        self._refresh_buffer_preview_from_state()

    def _build_buffer_path_hints(self) -> SessionPathHints:
        h = SessionPathHints()
        wd = self._state.watchdir
        if self._state.integrator_dir is not None:
            h.integrator_dir = str(self._state.integrator_dir.resolve())
        av = wd / "averaged"
        if av.is_dir():
            h.integrate_output_dir = str(av.resolve())
        lip = self._state.last_integrated_dat_path
        if lip is not None and lip.is_file():
            h.last_integrated_dat_path = str(lip.resolve())
            h.one_d_profile_dir = str(lip.parent.resolve())
        return h

    def _open_calibration_wizard(self) -> None:
        if self._cal_wizard is None:
            self._cal_wizard = CalibrationWizardDialog(watchdir=self._state.watchdir, parent=self)
        self._cal_wizard.show()
        self._cal_wizard.raise_()
        self._cal_wizard.activateWindow()

    def _open_buffer_wizard(self) -> None:
        hints = self._build_buffer_path_hints()
        if self._buf_wizard is None:
            self._buf_wizard = BufferWizardDialog(
                watchdir=self._state.watchdir,
                hints=hints,
                parent=self,
            )
        else:
            self._buf_wizard.rebuild(hints)
        self._buf_wizard.show()
        self._buf_wizard.raise_()
        self._buf_wizard.activateWindow()

    def build_calibrate_request(self):
        if self._cal_wizard is None:
            raise RuntimeError("Calibration wizard is not open")
        return self._cal_wizard.build_calibrate_request()

    def set_calibration_running(self, running: bool) -> None:
        if self._cal_wizard is not None:
            self._cal_wizard.set_running(bool(running))

    def set_calibration_preview_path(self, path: str) -> None:
        p = (path or "").strip()
        if not p or not os.path.isfile(p):
            self._cal_preview.show_path("")
            self._cal_hint.setVisible(True)
            return
        self._cal_hint.setVisible(False)
        self._cal_preview.show_path(p)

    def _refresh_buffer_preview_from_state(self) -> None:
        buf = self._state.buffer_dat_path
        if buf is not None and buf.is_file():
            self._buf_hint.setVisible(False)
            self._buf_preview.show_path(str(buf))
        else:
            self._buf_preview.show_path("")
            self._buf_hint.setVisible(True)

    def _apply_buffer_from_wizard(self) -> None:
        if self._buf_wizard is None:
            return
        try:
            st = self._buf_wizard._form.state()  # type: ignore[attr-defined]
            pos = st.get("positional") or []
            buffer_text = _extract_pathfield_text(pos[1] if len(pos) > 1 else None)
            if not buffer_text:
                raise ValueError("Buffer .dat must be selected")
            buffer_path = Path(buffer_text)
            if not buffer_path.exists():
                raise ValueError(f"Buffer does not exist: {buffer_path}")

            outdir = self._state.watchdir / "subtracted"
            outdir.mkdir(parents=True, exist_ok=True)
            dest_buffer = outdir / "buffer.dat"
            try:
                dest_buffer.write_bytes(buffer_path.read_bytes())
            except Exception:
                dest_buffer = buffer_path

            opts = (st.get("options") or {}).copy()
            opts.pop("output_dir", None)
            opts.pop("use_cache", None)
            conf_path = outdir / "subtract.conf"
            conf_path.write_text(yaml.safe_dump(opts, sort_keys=True), encoding="utf-8")

            self._state.buffer_dat_path = dest_buffer
            self._state.subtract_conf_path = conf_path
            self._refresh_buffer_preview_from_state()
            self.subtract_config_changed.emit()
        except Exception as e:
            QMessageBox.critical(self._buf_wizard or self, "Cannot apply buffer", str(e))


def _extract_pathfield_text(state_obj: Optional[dict]) -> str:
    if not isinstance(state_obj, dict):
        return ""
    return (state_obj.get("text") or "").strip()
