"""Session mutation API: safe writes to LiveviewSessionState with persist/notify."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from .persistence import load_liveview_session_settings, save_liveview_session_settings
from .state import LiveviewIntakeMode, LiveviewSessionState, MonodisperseShapeMode

if TYPE_CHECKING:
    from ..pipeline.executor import LiveviewJobExecutor
    from ..ui.panels import LiveviewRightPanel


class LiveviewSession(QObject):
    """
    Owns session facts (via ``state``) and the safe mutation surface.

    Callers mutate intake / Auto-Manual / buffer / arming / mask through methods here
    so persist and fan-out stay consistent. Read facts via ``state``.
    """

    mode_changed = pyqtSignal(bool, bool)  # stopped, processing_idle
    intake_changed = pyqtSignal(object)  # LiveviewIntakeMode
    buffer_changed = pyqtSignal()
    analysis_arming_changed = pyqtSignal()
    mask_changed = pyqtSignal()

    def __init__(self, *, watchdir: Path, load_settings: bool = True) -> None:
        super().__init__()
        self._state = LiveviewSessionState(watchdir=watchdir)
        if load_settings:
            load_liveview_session_settings(self._state)
        self._executor: LiveviewJobExecutor | None = None
        self._right: LiveviewRightPanel | None = None

    @property
    def state(self) -> LiveviewSessionState:
        return self._state

    def bind_executor(self, executor: LiveviewJobExecutor) -> None:
        self._executor = executor

    def bind_right_panel(self, right: LiveviewRightPanel | None) -> None:
        self._right = right
        self.sync_processing_ui()

    def persist(self) -> None:
        save_liveview_session_settings(self._state)

    # --- intake / Auto-Manual -------------------------------------------------

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
            self.persist()
        if self._right is not None:
            self._right.sync_intake_toggles(mode)
        if notify:
            self.intake_changed.emit(mode)

    @property
    def is_stopped(self) -> bool:
        return not self._state.is_auto_processing()

    def stop(self) -> None:
        self._state.set_auto_processing(False)
        self._sync_executor()
        self.sync_processing_ui()

    def resume(self) -> None:
        if self._executor is None or not self._executor.is_processing_idle():
            return
        self._state.set_auto_processing(True)
        self._sync_executor()
        self.sync_processing_ui()

    def _sync_executor(self) -> None:
        if self._executor is None:
            return
        sync = getattr(self._executor, "sync_auto_processing_from_session", None)
        if callable(sync):
            sync()

    def sync_processing_ui(self) -> None:
        stopped = self.is_stopped
        idle = True if self._executor is None else self._executor.is_processing_idle()
        if self._right is not None:
            self._right.sync_modeling_ui_to_session_state(
                queue_paused=stopped,
                processing_idle=idle,
            )
            self._right.sync_intake_toggles(self.intake_mode)
        self.mode_changed.emit(stopped, idle)

    # --- buffer / mask / arming -----------------------------------------------

    def set_buffer(
        self,
        buffer_path: Path,
        options: Dict[str, Any],
        *,
        persist: bool = True,
    ) -> None:
        self._state.buffer_dat_path = buffer_path
        self._state.subtract_options = dict(options)
        if persist:
            self.persist()
        self.buffer_changed.emit()

    def set_mask_path(
        self,
        path: Optional[Path],
        *,
        preview_path: Optional[Path] = None,
        clear_preview: bool = False,
        persist: bool = True,
    ) -> None:
        self._state.mask_path = path
        if clear_preview or path is None:
            self._state.mask_preview_path = None
        elif preview_path is not None:
            self._state.mask_preview_path = preview_path
        if persist:
            self.persist()
        self.mask_changed.emit()

    def set_mask_preview_path(self, path: Optional[Path], *, persist: bool = False) -> None:
        self._state.mask_preview_path = path
        if persist:
            self.persist()

    def set_monodisperse_armed(self, armed: bool) -> bool:
        """Set mono arming. Returns True if the flag changed."""
        armed = bool(armed)
        if self._state.monodisperse_armed == armed:
            return False
        self._state.monodisperse_armed = armed
        self.analysis_arming_changed.emit()
        return True

    def set_polydisperse_armed(self, armed: bool) -> bool:
        """Set poly arming. Returns True if the flag changed."""
        armed = bool(armed)
        if self._state.polydisperse_armed == armed:
            return False
        self._state.polydisperse_armed = armed
        self.analysis_arming_changed.emit()
        return True

    def disarm_analysis(self) -> bool:
        """Clear both arming flags. Returns True if either was armed."""
        changed = self._state.monodisperse_armed or self._state.polydisperse_armed
        self._state.monodisperse_armed = False
        self._state.polydisperse_armed = False
        if changed:
            self.analysis_arming_changed.emit()
        return changed

    def apply_inferred_shape_mode(self, mode: MonodisperseShapeMode) -> bool:
        """Apply disk-inferred shape mode only when session mode is still NONE."""
        if not isinstance(mode, MonodisperseShapeMode):
            mode = MonodisperseShapeMode(mode)
        if mode == MonodisperseShapeMode.NONE:
            return False
        if self._state.monodisperse_shape_mode != MonodisperseShapeMode.NONE:
            return False
        self._state.monodisperse_shape_mode = mode
        return True
