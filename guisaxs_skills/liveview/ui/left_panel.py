from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...logic.session_state import SessionPathHints
from ...ui.preview_panel import PreviewPanel
from ..logic.calibration_display import refined_yml_display_rows
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
    calibration_reset_requested = pyqtSignal()
    buffer_reset_requested = pyqtSignal()
    subtract_config_changed = pyqtSignal()

    def __init__(self, *, state: LiveviewSessionState) -> None:
        super().__init__()
        self._state = state
        self._cal_wizard: CalibrationWizardDialog | None = None
        self._buf_wizard: BufferWizardDialog | None = None

        self._cal_group = QGroupBox("Calibration")
        self._cal_open = QPushButton("Set calibration")
        self._cal_reset = QPushButton("Reset")
        self._cal_reset.setToolTip("Clear calibration (state A), turn analysis Off, and reset the calibration wizard form")
        self._cal_reset.clicked.connect(self.calibration_reset_requested.emit)
        self._cal_hint = QLabel("—")
        self._cal_hint.setWordWrap(True)
        self._cal_preview = PreviewPanel()
        self._cal_preview.setMinimumHeight(140)
        self._cal_params_table = QTableWidget(0, 2)
        self._cal_params_table.setHorizontalHeaderLabels(["Parameter", "Value"])
        hdr = self._cal_params_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        self._cal_params_table.verticalHeader().setVisible(False)
        self._cal_params_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._cal_params_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._cal_params_table.setFocusPolicy(Qt.NoFocus)
        self._cal_params_table.setShowGrid(True)
        self._cal_params_table.setMinimumHeight(72)
        self._cal_params_table.setMaximumHeight(240)
        self._cal_params_table.setVisible(False)
        cal_lay = QVBoxLayout(self._cal_group)
        cal_btns = QHBoxLayout()
        cal_btns.addWidget(self._cal_open, 1)
        cal_btns.addWidget(self._cal_reset, 0)
        cal_lay.addLayout(cal_btns)
        cal_lay.addWidget(self._cal_hint)
        cal_lay.addWidget(self._cal_preview, 1)
        cal_lay.addWidget(self._cal_params_table, 0)

        self._buf_group = QGroupBox("Buffer")
        self._buf_open = QPushButton("Set buffer")
        self._buf_reset = QPushButton("Reset")
        self._buf_reset.setToolTip("Clear buffer and subtraction settings (state B), turn analysis Off, and reset the buffer wizard form")
        self._buf_reset.clicked.connect(self.buffer_reset_requested.emit)
        self._buf_hint = QLabel("—")
        self._buf_hint.setWordWrap(True)
        self._buf_preview = PreviewPanel()
        self._buf_preview.setMinimumHeight(140)
        buf_lay = QVBoxLayout(self._buf_group)
        buf_btns = QHBoxLayout()
        buf_btns.addWidget(self._buf_open, 1)
        buf_btns.addWidget(self._buf_reset, 0)
        buf_lay.addLayout(buf_btns)
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
            self._cal_wizard.reset_requested.connect(self.calibration_reset_requested.emit)
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
            self._buf_wizard.reset_requested.connect(self.buffer_reset_requested.emit)
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
            self.set_calibration_params_from_path(None)
            return
        self._cal_hint.setVisible(False)
        self._cal_preview.show_path(p)

    def set_calibration_params_from_path(self, path: Optional[str]) -> None:
        """Fill geometry + wavelength table from autosaxs ``refined.yml`` (or clear)."""
        p = (path or "").strip()
        rows = (
            refined_yml_display_rows(p, integrator_dir=self._state.integrator_dir)
            if p and os.path.isfile(p)
            else []
        )
        self._cal_params_table.setRowCount(len(rows))
        for i, (label, val) in enumerate(rows):
            li = QTableWidgetItem(label)
            li.setFlags(li.flags() & ~Qt.ItemIsEditable)
            vi = QTableWidgetItem(val)
            vi.setFlags(vi.flags() & ~Qt.ItemIsEditable)
            vi.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self._cal_params_table.setItem(i, 0, li)
            self._cal_params_table.setItem(i, 1, vi)
        self._cal_params_table.setVisible(bool(rows))

    def _refresh_buffer_preview_from_state(self) -> None:
        buf = self._state.buffer_dat_path
        if buf is not None and buf.is_file():
            self._buf_hint.setVisible(False)
            self._buf_preview.show_path(str(buf))
        else:
            self._buf_preview.show_path("")
            self._buf_hint.setVisible(True)

    def sync_buffer_preview_from_state(self) -> None:
        """Refresh buffer thumbnail from ``LiveviewSessionState`` (e.g. after watch-folder change)."""
        self._refresh_buffer_preview_from_state()

    def reset_calibration_wizard_form(self) -> None:
        if self._cal_wizard is not None:
            self._cal_wizard.reset_form_to_defaults()

    def reset_buffer_wizard_form(self) -> None:
        if self._buf_wizard is not None:
            self._buf_wizard.reset_form_to_empty(self._build_buffer_path_hints())

    def _apply_buffer_from_wizard(self) -> None:
        if self._buf_wizard is None:
            return
        try:
            st = self._buf_wizard._form.state()  # type: ignore[attr-defined]
            pos = st.get("positional") or []
            buffer_text = _extract_pathfield_text(pos[1] if len(pos) > 1 else None)
            if not buffer_text:
                raise ValueError("Buffer .dat must be selected")
            opts = (st.get("options") or {}).copy()
            opts.pop("output_dir", None)
            opts.pop("use_cache", None)
            buffer_path = Path(buffer_text).expanduser()
            buffer_path = buffer_path.resolve() if buffer_path.is_absolute() else (self._state.watchdir / buffer_path).resolve()
            if not buffer_path.exists():
                raise ValueError(f"Buffer does not exist: {buffer_path}")
            if not buffer_path.is_file():
                raise ValueError(f"Buffer is not a file: {buffer_path}")

            # Do not copy buffer.dat or write subtract.conf.
            # Keep the selected buffer and subtract parameters in session state only.
            self._state.buffer_dat_path = buffer_path
            self._state.subtract_options = dict(opts)
            self._refresh_buffer_preview_from_state()
            self.subtract_config_changed.emit()
        except Exception as e:
            QMessageBox.critical(self._buf_wizard or self, "Cannot apply buffer", str(e))


def _extract_pathfield_text(state_obj: Optional[dict]) -> str:
    if not isinstance(state_obj, dict):
        return ""
    return (state_obj.get("text") or "").strip()
