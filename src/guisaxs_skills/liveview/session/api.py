"""Session mutation API: safe writes to LiveviewSessionState with persist/notify."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from .persistence import (
    load_liveview_session_settings,
    project_config_rules,
    save_liveview_session_settings,
)
from .sample import Sample
from .state import LiveviewIntakeMode, LiveviewSessionState, MonodisperseShapeMode, PolydisperseMixtureMode
from .watchdir_config import load_buffer_rules_from_config_path, write_config_text

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
    configuration_changed = pyqtSignal()
    analysis_arming_changed = pyqtSignal()
    mask_changed = pyqtSignal()

    def __init__(self, *, watchdir: Path, load_settings: bool = True) -> None:
        super().__init__()
        self._state = LiveviewSessionState(watchdir=watchdir)
        if load_settings:
            load_liveview_session_settings(self._state)
        else:
            project_config_rules(self._state)
        self._executor: LiveviewJobExecutor | None = None
        self._right: LiveviewRightPanel | None = None

    @property
    def state(self) -> LiveviewSessionState:
        return self._state

    def bind_executor(self, executor: LiveviewJobExecutor) -> None:
        self._executor = executor
        bind = getattr(executor, "bind_session", None)
        if callable(bind):
            bind(self)

    def bind_right_panel(self, right: LiveviewRightPanel | None) -> None:
        self._right = right
        self.sync_processing_ui()

    def persist(self) -> None:
        save_liveview_session_settings(self._state)

    def reload_configuration(self) -> None:
        """Re-read root (and cached) config.conf after an external/editor Save."""
        project_config_rules(self._state)
        self.configuration_changed.emit()
        self.buffer_changed.emit()

    def mark_configuration_opened(self, *, persist: bool = True) -> None:
        if self._state.configuration_opened_once:
            return
        self._state.configuration_opened_once = True
        if persist:
            self.persist()
        self.configuration_changed.emit()

    def save_configuration_text(self, path: Path, text: str) -> None:
        """Persist validated Configuration editor text and refresh projection."""
        write_config_text(path, text)
        # Refresh cache for this config dir + root projection.
        try:
            rules = load_buffer_rules_from_config_path(path)
        except ValueError:
            rules = None
        if rules is not None:
            self._state.rules_by_config_dir[str(path.parent.resolve())] = rules
        project_config_rules(self._state)
        self.configuration_changed.emit()
        self.buffer_changed.emit()

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
        """
        Legacy helper: update last-picked hint + session subtract options.

        Prefer :meth:`set_buffer_for_sample` for per-sample Set buffer Apply.
        """
        self._state.buffer_dat_path = buffer_path
        self._state.subtract_options = dict(options)
        if persist:
            self.persist()
        self.buffer_changed.emit()

    def set_buffer_for_sample(
        self,
        sample: Sample | str,
        buffer_dat: Path | str,
        subtract_options: Optional[Dict[str, Any]] = None,
        *,
        persist: bool = True,
    ) -> None:
        """Bind buffer for one sample via session overlay (wins over config rules)."""
        if isinstance(sample, Sample):
            key = sample.key
        else:
            key = str(Path(str(sample)).expanduser().resolve())
        buf = Path(buffer_dat).expanduser().resolve()
        self._state.buffer_overrides[key] = str(buf)
        self._state.buffer_dat_path = buf  # last-picked hint
        if subtract_options is not None:
            self._state.subtract_options = dict(subtract_options)
        elif self._state.subtract_options is None:
            self._state.subtract_options = {}
        if persist:
            self.persist()
        self.buffer_changed.emit()

    def clear_buffer_override(
        self,
        sample: Sample | str,
        *,
        persist: bool = True,
    ) -> None:
        if isinstance(sample, Sample):
            key = sample.key
        else:
            key = str(Path(str(sample)).expanduser().resolve())
        if key in self._state.buffer_overrides:
            del self._state.buffer_overrides[key]
            if persist:
                self.persist()
            self.buffer_changed.emit()

    def set_subtract_options(
        self,
        options: Dict[str, Any],
        *,
        persist: bool = True,
    ) -> None:
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

    def apply_inferred_mixture_mode(self, mode: PolydisperseMixtureMode) -> bool:
        """Apply disk-inferred mixture mode only when session mode is still NONE."""
        if not isinstance(mode, PolydisperseMixtureMode):
            mode = PolydisperseMixtureMode(mode)
        if mode == PolydisperseMixtureMode.NONE:
            return False
        if self._state.polydisperse_mixture_mode != PolydisperseMixtureMode.NONE:
            return False
        self._state.polydisperse_mixture_mode = mode
        return True

    # --- calibration / last_* pipeline hints ---------------------------------

    def set_integrator_dir(self, path: Optional[Path], *, persist: bool = False) -> None:
        self._state.integrator_dir = path
        if persist:
            self.persist()

    def set_last_integrated_dat_path(self, path: Optional[Path]) -> None:
        """Pipeline hint only — not sample-tied UI SSOT."""
        self._state.last_integrated_dat_path = path

    def set_last_subtracted_dat_path(self, path: Optional[Path]) -> None:
        """Pipeline hint only — not sample-tied UI SSOT."""
        self._state.last_subtracted_dat_path = path

    def apply_calibration_outcome(
        self,
        *,
        integrator_dir: Optional[Path] = None,
        refined_yml_path: Optional[Path] = None,
        curve_plot_path: Optional[Path] = None,
        persist: bool = True,
    ) -> None:
        """Write calibrate skill outcomes into session facts."""
        if integrator_dir is not None:
            self._state.integrator_dir = integrator_dir
        self._state.calibration_refined_yml_path = refined_yml_path
        self._state.calibration_curve_plot_path = curve_plot_path
        if persist:
            self.persist()
