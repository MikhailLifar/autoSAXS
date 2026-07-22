"""Single Auto/Manual gate for liveview queue suspension."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt5.QtCore import QObject, pyqtSignal

if TYPE_CHECKING:
    from ..pipeline.executor import LiveviewJobExecutor
    from ..ui.panels import LiveviewRightPanel

PAUSE_SOURCE_MANUAL = "manual"


class ProcessingModeGate(QObject):
    """
    Widgets talk only to this gate for Auto/Manual switching:

    - ``stop()`` — enter Manual if not already stopped (idempotent)
    - ``resume()`` — return to Auto only when processing is idle

    Cancel-current stays at call sites that invalidate in-flight work.
    """

    mode_changed = pyqtSignal(bool, bool)  # stopped, processing_idle

    def __init__(self, executor: LiveviewJobExecutor) -> None:
        super().__init__()
        self._executor = executor
        self._right: LiveviewRightPanel | None = None

    def bind_right_panel(self, right: LiveviewRightPanel | None) -> None:
        self._right = right
        self.sync_ui()

    @property
    def is_stopped(self) -> bool:
        return bool(self._executor.queue_suspended)

    def stop(self) -> None:
        self._executor.pause(source=PAUSE_SOURCE_MANUAL)
        self.sync_ui()

    def resume(self) -> None:
        if not self._executor.is_processing_idle():
            return
        self._executor.resume(source=PAUSE_SOURCE_MANUAL)
        self.sync_ui()

    def sync_ui(self) -> None:
        stopped = self.is_stopped
        idle = self._executor.is_processing_idle()
        if self._right is not None:
            self._right.sync_modeling_ui_to_session_state(
                queue_paused=stopped,
                processing_idle=idle,
            )
        self.mode_changed.emit(stopped, idle)
