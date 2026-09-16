"""Single owner for liveview intake mode (2D/1D/Sub) and Auto/Manual queue suspension."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt5.QtCore import QObject, pyqtSignal

from ..session.state import LiveviewIntakeMode

if TYPE_CHECKING:
    from ..pipeline.executor import LiveviewJobExecutor
    from ..session.state import LiveviewSessionState
    from ..ui.panels import LiveviewRightPanel


class LiveviewProcessingMode(QObject):
    """
    Widgets talk only to this object for processing-mode concepts:

    - intake (2D / 1D / Sub) — where new samples board
    - ``stop()`` / ``resume()`` — Auto vs Manual via ``session.auto_processing``

    Cancel-current stays at call sites that invalidate in-flight work.
    """

    mode_changed = pyqtSignal(bool, bool)  # stopped, processing_idle
    intake_changed = pyqtSignal(object)  # LiveviewIntakeMode

    def __init__(
        self,
        executor: LiveviewJobExecutor,
        *,
        state: LiveviewSessionState,
    ) -> None:
        super().__init__()
        self._executor = executor
        self._state = state
        self._right: LiveviewRightPanel | None = None
        self._on_intake_changed = None  # Optional[Callable]

    def bind_right_panel(self, right: LiveviewRightPanel | None) -> None:
        self._right = right
        self.sync_ui()

    def set_intake_listener(self, callback) -> None:
        """Controller wires watcher rebind / UI reshape here (single fan-out)."""
        self._on_intake_changed = callback

    @property
    def intake_mode(self) -> LiveviewIntakeMode:
        return self._state.intake_mode

    def set_intake(
        self,
        mode: LiveviewIntakeMode,
        *,
        persist: bool = True,
        notify: bool = True,
    ) -> None:
        if not isinstance(mode, LiveviewIntakeMode):
            mode = LiveviewIntakeMode(mode)
        if self._state.intake_mode == mode:
            return
        self._state.intake_mode = mode
        if persist:
            from ..session.persistence import save_liveview_session_settings

            save_liveview_session_settings(self._state)
        if self._right is not None:
            self._right.sync_intake_toggles(mode)
        if notify:
            self.intake_changed.emit(mode)
            if self._on_intake_changed is not None:
                self._on_intake_changed(mode)

    @property
    def is_stopped(self) -> bool:
        return not self._state.is_auto_processing()

    def stop(self) -> None:
        self._state.set_auto_processing(False)
        self._sync_executor()
        self.sync_ui()

    def resume(self) -> None:
        if not self._executor.is_processing_idle():
            return
        self._state.set_auto_processing(True)
        self._sync_executor()
        self.sync_ui()

    def _sync_executor(self) -> None:
        for name in ("sync_from_session", "sync_auto_processing_from_session"):
            sync = getattr(self._executor, name, None)
            if callable(sync):
                sync()
                return

    def sync_ui(self) -> None:
        stopped = self.is_stopped
        idle = self._executor.is_processing_idle()
        if self._right is not None:
            self._right.sync_modeling_ui_to_session_state(
                queue_paused=stopped,
                processing_idle=idle,
            )
            self._right.sync_intake_toggles(self.intake_mode)
        self.mode_changed.emit(stopped, idle)


# Backward-compatible alias
ProcessingModeGate = LiveviewProcessingMode
