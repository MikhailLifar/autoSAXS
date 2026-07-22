from __future__ import annotations

from pathlib import Path

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
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
from ..logic.smart_defaults import update_session_hints_from_success
from ..logic.skill_catalog import discover_skills
from ..logic.workdir import select_workdir
from .artifacts_panel import ArtifactsPanel
from .catalog_tabs import CatalogTabs
from .data_panel import DataPanel
from .help_dialog import HelpDialog
from .log_view import LogView
from .preview_panel import PreviewPanel
from .run_controls import RunControls
from .skill_form import SkillForm
from .skill_header import SkillHeader
from .toast import Toast


class MainWindow(QMainWindow):
    def __init__(self, *, bus: EventBus, workdir: Path) -> None:
        super().__init__()
        self._bus = bus
        self._state = SessionState(workdir=workdir)
        self._runner = SkillRunner(workdir=workdir)

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

        self._init_menu()
        self._wire()
        self._restore_state()
        # Catch Enter/Return presses anywhere within this window.
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._save_state()
        super().closeEvent(event)

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        try:
            if (
                self.isActiveWindow()
                and isinstance(obj, QWidget)
                and obj.window() is self
                and event.type() == event.KeyPress
                and event.key() in (Qt.Key_Return, Qt.Key_Enter)
                and event.modifiers() == Qt.NoModifier
            ):
                self._on_enter_submit()
                return True
        except Exception:
            # If the filter fails for any reason, don't break normal event processing.
            return False
        return super().eventFilter(obj, event)

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

    def _init_menu(self) -> None:
        mb = self.menuBar()
        file_menu = mb.addMenu("File")

        act_open = QAction("Open working directory…", self)
        act_open.triggered.connect(self._on_open_workdir)
        file_menu.addAction(act_open)

        file_menu.addSeparator()
        act_exit = QAction("Exit", self)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

    def _wire(self) -> None:
        self._catalog.skill_selected.connect(self._on_skill_selected)
        self._header.help_button.clicked.connect(self._on_help)
        self._controls.run_button.clicked.connect(self._on_run)
        self._controls.cancel_button.clicked.connect(self._on_cancel)
        self._controls.copy_cli_button.clicked.connect(self._on_copy_cli)
        self._form.submit_requested.connect(self._on_enter_submit)

        self._wire_runner()
        self._artifacts.artifact_selected.connect(self._preview.show_path)

        # Catalog widget handles default selection itself, but the initial selection
        # may happen before the MainWindow connects signals. Sync once on startup.
        meta = self._catalog.current_skill()
        if meta is not None:
            self._on_skill_selected(meta)

    def _wire_runner(self) -> None:
        self._runner.started.connect(lambda _: self._controls.set_running(True))
        self._runner.stdout.connect(self._logs.append_stdout)
        self._runner.stderr.connect(self._logs.append_stderr)
        self._runner.finished.connect(self._on_finished)

    def _reset_for_workdir(self, workdir: Path) -> None:
        # Cancel any running job and swap runner.
        old_runner = self._runner
        try:
            old_runner.cancel()
        except Exception:
            pass
        self._runner = SkillRunner(workdir=workdir)
        self._wire_runner()
        try:
            old_runner.deleteLater()
        except Exception:
            pass

        # Reset state + UI to match a fresh launch.
        self._state = SessionState(workdir=workdir)
        self._workdir_label.setText(f"Workdir: {workdir}")
        self._controls.set_running(False)
        self._logs.clear()
        self._artifacts.set_result({})
        self._preview.show_path("")

        # Reset selection and reinitialize the middle column.
        self._catalog.select_skill("calibrate")
        meta = self._catalog.current_skill()
        if meta is not None:
            self._on_skill_selected(meta)

    def _on_open_workdir(self) -> None:
        path = select_workdir(parent=self, initial_directory=str(self._state.workdir))
        if path is None:
            return
        self._reset_for_workdir(Path(path))

    def _on_skill_selected(self, meta) -> None:
        # Save previous skill form state before switching.
        prev = self._state.selected_skill.name if self._state.selected_skill else None
        if prev:
            self._state.form_state_by_skill[prev] = self._form.state()

        self._state.selected_skill = meta
        self._header.set_skill_name(meta.name)
        # Restore remembered fields if available; otherwise use default output dir.
        out = default_output_dir_for_skill(workdir=self._state.workdir, skill_name=meta.name)
        saved = self._state.form_state_by_skill.get(meta.name)
        self._form.set_skill(
            meta,
            workdir=self._state.workdir,
            default_output_dir=str(out),
            hints=self._state.path_hints,
            saved_state=saved,
        )
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
            req = self._form.build_request()
        except Exception as e:
            QMessageBox.critical(self, "Invalid input", str(e))
            return
        req = maybe_copy_inputs(request=req, workdir=self._state.workdir, enabled=self._form.copy_inputs_enabled())
        self._runner.start(req)

    def _on_enter_submit(self) -> None:
        # Enter runs only when input is valid; otherwise show a non-blocking warning.
        try:
            _ = self._form.build_request()
        except Exception as e:
            Toast(text=f"Cannot run: {e}", parent=self).show_near_bottom()
            return
        self._on_run()

    def _on_cancel(self) -> None:
        self._runner.cancel()

    def _on_finished(self, outcome) -> None:
        self._controls.set_running(False)
        self._state.result = outcome.result
        self._state.artifacts = flatten_artifacts(outcome.result)
        self._artifacts.set_result(outcome.result)
        if outcome.success and outcome.request is not None:
            update_session_hints_from_success(
                self._state.path_hints,
                workdir=self._state.workdir,
                skill_name=outcome.request.skill_name,
                result=outcome.result,
                request=outcome.request,
            )

