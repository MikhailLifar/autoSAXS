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
from .polydisperse import LiveviewPolydisperseHandler
from .processing_mode import ProcessingModeGate
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
        self.processing_mode = ProcessingModeGate(self._executor)

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
        self.polydisperse = LiveviewPolydisperseHandler(self)

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
        self.processing_mode.bind_right_panel(right)
        self._wire_log_streams()
        self.session.apply_loaded_to_ui()
        self.history.refresh_chrome()

    def _wire_log_streams(self) -> None:
        if self._right is None:
            return
        log = self._right.log_panel
        self._runner.stdout.connect(
            lambda t: log.append_skill_stdout(t, skill=self._runner_skill_label())
        )
        self._runner.stderr.connect(
            lambda t: log.append_skill_stderr(t, skill=self._runner_skill_label())
        )

    def _runner_skill_label(self) -> str:
        try:
            job = self._executor._current_job
            name = getattr(self._executor, "_pending_step_name", None) or ""
            if name:
                return str(name)
            if job is not None and job.steps:
                return str(job.steps[0].request.skill_name)
        except Exception:
            pass
        return ""

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

    def run_model_mixture(self) -> None:
        self.skill_runs.run_model_mixture()

    def apply_subtraction_rerun(self, *, scaling_factor: float, sample_dat: str, buffer_dat: str) -> None:
        self.skill_runs.apply_subtraction_rerun(
            scaling_factor=scaling_factor,
            sample_dat=sample_dat,
            buffer_dat=buffer_dat,
        )

    def on_subtraction_control_changed(self) -> None:
        """Scale spinbox touched — enter Manual (idempotent)."""
        self.processing_mode.stop()

    def on_subtraction_wizard_closed(self) -> None:
        """Close without Apply: resume Auto only when no analysis windows are armed."""
        if not (self._state.monodisperse_armed or self._state.polydisperse_armed):
            self.processing_mode.resume()

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

    def on_analysis_arming_changed(self) -> None:
        self.history.refresh_right_outputs()
        self.processing_mode.sync_ui()

    def on_analysis_mode_changed(self) -> None:
        """Deprecated alias."""
        self.on_analysis_arming_changed()

    def append_app_log(self, text: str) -> None:
        if self._right is not None:
            self._right.log_panel.append_app(text)

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

    def on_polydisperse_window_open(self) -> None:
        self.history.refresh_right_outputs()

    def on_polydisperse_intervention(self) -> None:
        self.polydisperse.on_intervention()

    def on_polydisperse_mixture_config(self) -> None:
        self.polydisperse.on_mixture_config_changed()

    def on_polydisperse_guinier_rerun(self) -> None:
        self.polydisperse.on_guinier_rerun()

    def on_polydisperse_sizes_rerun(self) -> None:
        self.polydisperse.on_sizes_rerun()

    def on_polydisperse_mixture_rerun(self) -> None:
        self.polydisperse.on_mixture_rerun()

    def on_polydisperse_resume_queue(self) -> None:
        self.polydisperse.on_resume_queue()

    def on_polydisperse_stop_queue(self) -> None:
        self.polydisperse.on_stop_queue()

    def on_queue_status(self, status: LiveviewQueueStatus) -> None:
        if self._middle is not None:
            self._middle.set_queue_status(status)

    def on_error(self, text: str) -> None:
        msg = (text or "").strip()
        if msg and self._right is not None:
            self._right.log_panel.append_app(f"Error: {msg}")
        if self._parent_widget is not None and msg:
            QMessageBox.warning(self._parent_widget, "Live pipeline", text)
