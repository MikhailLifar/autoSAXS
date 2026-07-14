from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QMessageBox, QWidget

from ...logic.runner_qprocess import SkillRunner
from ..pipeline import LiveviewJobExecutor, LiveviewQueueStatus
from ..session import load_liveview_session_settings, save_liveview_session_settings
from ..session.state import LiveviewSessionState, LiveviewWatchMode
from ..ui.panels import LiveviewLeftPanel, LiveviewMiddlePanel, LiveviewRightPanel

from .history import LiveviewHistoryHandler
from .ingest import LiveviewIngestHandler, LiveviewWatchdirHandler
from .monodisperse import LiveviewMonodisperseHandler
from .session import (
    LiveviewSessionHandler,
    LiveviewSkillOutcomesHandler,
    LiveviewSkillRunsHandler,
)


class LiveviewController(QObject):
    """Thin facade composing focused handler objects."""

    queue_status = pyqtSignal(object)
    error = pyqtSignal(str)
    latest_artifacts = pyqtSignal(object)
    session_file_completed = pyqtSignal(str)
    tiff_revision_pending = pyqtSignal(object)
    skill_started = pyqtSignal(str)
    skill_finished = pyqtSignal(object)

    def __init__(self, *, watchdir: Path) -> None:
        super().__init__()
        self._state = LiveviewSessionState(watchdir=watchdir)
        load_liveview_session_settings(self._state)

        self._watchdir = watchdir.resolve()
        self._runner = SkillRunner(workdir=watchdir)
        self._executor = LiveviewJobExecutor(state=self._state, runner=self._runner)

        self._left: Optional[LiveviewLeftPanel] = None
        self._middle: Optional[LiveviewMiddlePanel] = None
        self._right: Optional[LiveviewRightPanel] = None
        self._parent_widget: Optional[QWidget] = None

        self.history = LiveviewHistoryHandler(self)
        self.ingest = LiveviewIngestHandler(self)
        self.watchdir_switch = LiveviewWatchdirHandler(self)
        self.session = LiveviewSessionHandler(self)
        self.skill_runs = LiveviewSkillRunsHandler(self)
        self.outcomes = LiveviewSkillOutcomesHandler(self)
        self.monodisperse = LiveviewMonodisperseHandler(self)

        self._connect_executor(self._executor)
        self._executor.start()

    @property
    def state(self) -> LiveviewSessionState:
        return self._state

    @property
    def watchdir(self) -> Path:
        return self._watchdir

    @property
    def runner(self) -> SkillRunner:
        return self._runner

    @property
    def executor(self) -> LiveviewJobExecutor:
        return self._executor

    @property
    def left(self) -> Optional[LiveviewLeftPanel]:
        return self._left

    @property
    def middle(self) -> Optional[LiveviewMiddlePanel]:
        return self._middle

    @property
    def right(self) -> Optional[LiveviewRightPanel]:
        return self._right

    @property
    def parent_widget(self) -> Optional[QWidget]:
        return self._parent_widget

    def bind_panels(
        self,
        *,
        left: LiveviewLeftPanel,
        middle: LiveviewMiddlePanel,
        right: LiveviewRightPanel,
        parent: QWidget,
    ) -> None:
        self._left = left
        self._middle = middle
        self._right = right
        self._parent_widget = parent
        self.session.apply_loaded_to_ui()
        self.history.refresh_chrome()

    def shutdown(self) -> None:
        self.persist_session_settings()
        self.ingest.stop_all()
        try:
            self._executor.stop()
        except Exception:
            pass
        try:
            self._runner.cancel()
        except Exception:
            pass

    def require_idle(self, title: str, message: str) -> bool:
        if self._runner.is_running():
            if self._parent_widget is not None:
                QMessageBox.warning(self._parent_widget, title, message)
            return False
        return True

    def persist_session_settings(self) -> None:
        save_liveview_session_settings(self._state)

    def _connect_executor(self, executor: LiveviewJobExecutor) -> None:
        executor.queue_status.connect(self.on_queue_status)
        executor.error.connect(self.on_error)
        executor.latest_artifacts.connect(self.outcomes.on_latest_artifacts)
        executor.latest_artifacts.connect(self.latest_artifacts.emit)
        executor.session_file_completed.connect(self.history.on_session_file_completed)
        executor.session_file_completed.connect(self.session_file_completed.emit)
        executor.tiff_revision_pending.connect(self.history.on_tiff_revision_pending)
        executor.tiff_revision_pending.connect(self.tiff_revision_pending.emit)
        executor.skill_started.connect(self.outcomes.on_started)
        executor.skill_started.connect(self.skill_started.emit)
        executor.skill_finished.connect(self.outcomes.on_finished)
        executor.skill_finished.connect(self.skill_finished.emit)

    # --- delegated public API (window wiring) ---

    def set_watch_mode(self, new_mode: LiveviewWatchMode) -> None:
        self.ingest.set_watch_mode(new_mode)

    def switch_watchdir(self, new_p: Path) -> bool:
        return self.watchdir_switch.switch(new_p)

    def ingest_dropped_tiffs(self, paths: list[str]) -> None:
        self.ingest.ingest_dropped_tiffs(paths)

    def reset_calibration(self) -> None:
        self.session.reset_calibration()

    def reset_buffer(self) -> None:
        self.session.reset_buffer()

    def on_subtract_config_changed(self) -> None:
        self.session.on_subtract_config_changed()

    def run_calibration(self) -> None:
        self.skill_runs.run_calibration()

    def cancel_running_skill(self) -> None:
        self.skill_runs.cancel_running()

    def run_fit_sizes(self) -> None:
        self.skill_runs.run_fit_sizes()

    def run_fit_mixture(self) -> None:
        self.skill_runs.run_fit_mixture()

    def apply_subtraction_rerun(self, *, scaling_factor: float, sample_dat: str, buffer_dat: str) -> None:
        self.skill_runs.apply_subtraction_rerun(
            scaling_factor=scaling_factor,
            sample_dat=sample_dat,
            buffer_dat=buffer_dat,
        )

    def pause_executor(self, *, source: str = "default") -> None:
        self._executor.pause(source=source)

    def resume_executor(self, *, source: str | None = None) -> None:
        self._executor.resume(source=source)

    def middle_subtraction_context(self) -> dict:
        if self._middle is None:
            return {}
        return self._middle.current_subtraction_context()

    def preview_manual_subtraction_scale(self, scale: float) -> None:
        if self._middle is not None:
            self._middle.preview_manual_subtraction_scale(scale)

    def history_step(self, delta: int) -> None:
        self.history.step(delta)

    def process_history_file(self) -> None:
        self.history.process_current_file()

    def on_analysis_mode_changed(self) -> None:
        self.history.refresh_right_outputs()
        self.monodisperse.refresh_queue_ui()

    def on_monodisperse_wizard_open(self) -> None:
        self.history.refresh_right_outputs()

    def on_monodisperse_intervention(self) -> None:
        self.monodisperse.on_intervention()

    def on_monodisperse_shape_config(self) -> None:
        self.monodisperse.on_shape_config_changed()

    def on_monodisperse_guinier_chain(self) -> None:
        self.monodisperse.on_guinier_chain()

    def on_monodisperse_gnom_rerun(self) -> None:
        self.monodisperse.on_gnom_rerun()

    def on_monodisperse_shape_rerun(self) -> None:
        self.monodisperse.on_shape_rerun()

    def on_monodisperse_resume_queue(self) -> None:
        self.monodisperse.on_resume_queue()

    def on_monodisperse_stop_queue(self) -> None:
        self.monodisperse.on_stop_queue()

    def on_queue_status(self, status: LiveviewQueueStatus) -> None:
        if self._middle is not None:
            self._middle.set_queue_status(status)

    def on_error(self, text: str) -> None:
        if self._parent_widget is not None and text.strip():
            QMessageBox.warning(self._parent_widget, "Live pipeline", text)
