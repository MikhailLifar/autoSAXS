from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QWidget,
    QVBoxLayout,
)

from ..core.event_bus import EventBus
from ..core.settings import KEY_MAIN_GEOM, KEY_MAIN_STATE, KEY_SPLITTER, settings
from ..core.models import flatten_artifacts
from ..logic.inputs_copy import maybe_copy_inputs
from ..logic.default_output_dir import default_output_dir_for_skill
from ..logic.runner_qprocess import SkillRunner
from ..logic.session_state import SessionState
from ..logic.skill_catalog import discover_skills
from .artifacts_panel import ArtifactsPanel
from .catalog_tabs import CatalogTabs
from .data_panel import DataPanel
from .help_dialog import HelpDialog
from .log_view import LogView
from .preview_panel import PreviewPanel
from .run_controls import RunControls
from .skill_form import SkillForm
from .skill_header import SkillHeader


class MainWindow(QMainWindow):
    def __init__(self, *, bus: EventBus, workdir: Path) -> None:
        super().__init__()
        self._bus = bus
        self._state = SessionState(workdir=workdir)
        self._runner = SkillRunner(workdir=workdir)
        self._pending_requests = []

        self.setWindowTitle("guisaxs-skills")

        self._splitter = QSplitter(Qt.Horizontal)
        self._catalog = CatalogTabs(skills=discover_skills())
        self._middle = QWidget()
        self._right = QWidget()

        self._header = SkillHeader()
        self._form = SkillForm()
        self._per_skill = DataPanel()
        self._controls = RunControls()
        self._logs = LogView()

        mid_lay = QVBoxLayout(self._middle)
        mid_lay.setContentsMargins(0, 0, 0, 0)
        mid_lay.addWidget(self._header)
        mid_lay.addWidget(self._form)
        # Per-skill panel is currently disabled (kept for future use).
        self._per_skill.setVisible(False)
        mid_lay.addWidget(self._controls)
        mid_lay.addWidget(self._logs, 1)

        self._artifacts = ArtifactsPanel()
        self._preview = PreviewPanel()
        right_lay = QVBoxLayout(self._right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.addWidget(self._preview, 1)
        right_lay.addWidget(self._artifacts, 1)

        self._splitter.addWidget(self._catalog)
        self._splitter.addWidget(self._middle)
        self._splitter.addWidget(self._right)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setStretchFactor(2, 1)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        self._workdir_label = QLabel(f"Workdir: {workdir}")
        self._workdir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self._workdir_label)
        layout.addWidget(self._splitter, 1)
        self.setCentralWidget(container)

        self._wire()
        self._restore_state()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._save_state()
        super().closeEvent(event)

    def _restore_state(self) -> None:
        s = settings()
        geom = s.value(KEY_MAIN_GEOM)
        if geom is not None:
            self.restoreGeometry(geom)
        state = s.value(KEY_MAIN_STATE)
        if state is not None:
            self.restoreState(state)
        split = s.value(KEY_SPLITTER)
        if split is not None:
            self._splitter.restoreState(split)

    def _save_state(self) -> None:
        s = settings()
        s.setValue(KEY_MAIN_GEOM, self.saveGeometry())
        s.setValue(KEY_MAIN_STATE, self.saveState())
        s.setValue(KEY_SPLITTER, self._splitter.saveState())

    def _wire(self) -> None:
        self._catalog.skill_selected.connect(self._on_skill_selected)
        self._header.help_button.clicked.connect(self._on_help)
        self._controls.run_button.clicked.connect(self._on_run)
        self._controls.cancel_button.clicked.connect(self._on_cancel)
        self._controls.copy_cli_button.clicked.connect(self._on_copy_cli)

        self._runner.started.connect(lambda _: self._controls.set_running(True))
        self._runner.stdout.connect(self._logs.append_stdout)
        self._runner.stderr.connect(self._logs.append_stderr)
        self._runner.finished.connect(self._on_finished)
        self._artifacts.artifact_selected.connect(self._preview.show_path)

        # Catalog widget handles default selection itself.

    def _on_skill_selected(self, meta) -> None:
        # Save previous skill form state before switching.
        prev = self._state.selected_skill.name if self._state.selected_skill else None
        if prev:
            self._state.form_state_by_skill[prev] = self._form.state()

        self._state.selected_skill = meta
        self._header.set_skill_name(meta.name)
        # Restore remembered fields if available; otherwise use default output dir.
        out = default_output_dir_for_skill(workdir=self._state.workdir, skill_name=meta.name)
        self._form.set_skill(meta, default_output_dir=str(out))
        saved = self._state.form_state_by_skill.get(meta.name)
        if saved:
            self._form.set_state(saved)
        # Per-skill panel disabled (kept for future use)

    def _on_help(self) -> None:
        meta = self._state.selected_skill
        if not meta:
            return
        dlg = HelpDialog(title=meta.name, text=meta.doc or meta.summary or meta.name, parent=self)
        dlg.exec_()

    def _on_copy_cli(self) -> None:
        try:
            req = self._form.build_request()
        except Exception as e:
            QMessageBox.critical(self, "Cannot build request", str(e))
            return
        text = "autosaxs " + " ".join(req.cli_argv())
        cb = self.clipboard()
        cb.setText(text)

    def _on_run(self) -> None:
        self._logs.clear()
        try:
            reqs = self._form.build_requests()
        except Exception as e:
            QMessageBox.critical(self, "Invalid input", str(e))
            return
        self._pending_requests = [
            maybe_copy_inputs(request=r, workdir=self._state.workdir, enabled=self._form.copy_inputs_enabled())
            for r in reqs
        ]
        self._start_next_request()

    def _on_cancel(self) -> None:
        self._pending_requests = []
        self._runner.cancel()

    def _start_next_request(self) -> None:
        if not self._pending_requests:
            return
        nxt = self._pending_requests.pop(0)
        self._runner.start(nxt)

    def _on_finished(self, outcome) -> None:
        self._controls.set_running(False)
        self._state.result = outcome.result
        self._state.artifacts = flatten_artifacts(outcome.result)
        self._artifacts.set_result(outcome.result)
        # Per-skill panel disabled (kept for future use)
        # Continue batch if any
        if self._pending_requests:
            self._logs.append_stderr("\n--- next item ---\n")
            self._start_next_request()

