"""Thin Qt wiring between monodisperse wizard panes, config sync, and artifact presenter."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from .....session.state import LiveviewSessionState, LiveviewWatchMode
from .artifact_presenter import MonodisperseArtifactPresenter
from .config_sync import MonodisperseConfigSync


class MonodisperseCoordinator(QObject):
    """Bridge monodisperse wizard UI signals to config/presenter and controller."""

    intervention_requested = pyqtSignal()
    shape_config_changed = pyqtSignal()
    guinier_chain_requested = pyqtSignal()
    gnom_rerun_requested = pyqtSignal()
    shape_rerun_requested = pyqtSignal()
    resume_auto_processing_requested = pyqtSignal()
    stop_auto_processing_requested = pyqtSignal()

    def __init__(self, *, state: LiveviewSessionState, wizard: Any) -> None:
        super().__init__()
        self._state = state
        self._wizard = wizard
        self._config = MonodisperseConfigSync(state=state, wizard=wizard)
        self._presenter = MonodisperseArtifactPresenter(state=state, wizard=wizard)
        self._connect_wizard()

    def _connect_wizard(self) -> None:
        w = self._wizard
        w.guinier_pane.range_changed.connect(self._on_guinier_range_changed)
        w.gnom_pane.params_changed.connect(self._on_gnom_params_changed)
        w.shape_pane.mode_changed.connect(self._on_shape_mode_changed)
        w.shape_pane.rerun_shape_requested.connect(self._on_rerun_shape)
        w.auto_toggle_clicked.connect(self._on_auto_toggle)

    def set_context(
        self,
        *,
        profile_path: str,
        output_root: Path,
        tiff_path: str = "",
        watch_mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
    ) -> None:
        self._presenter.set_context(
            profile_path=profile_path,
            output_root=output_root,
            tiff_path=tiff_path,
            watch_mode=watch_mode,
        )

    def sync_params_to_state(self) -> None:
        self._config.sync_params_to_state()

    def _on_guinier_range_changed(self, first: int, last: int) -> None:
        self.intervention_requested.emit()
        self._config.store_guinier_interval(int(first), int(last))
        self.guinier_chain_requested.emit()

    def _on_gnom_params_changed(self) -> None:
        self.intervention_requested.emit()
        self.sync_params_to_state()
        self.gnom_rerun_requested.emit()

    def _on_shape_mode_changed(self, mode: str) -> None:
        self._config.apply_shape_mode(mode)
        if str(mode).lower() == "none":
            self._wizard.shape_pane.clear_view()
        self._wizard.shape_pane._update_mode_ui()
        self._wizard.shape_pane.set_rerun_enabled(self._presenter.can_rerun_shape())
        self.shape_config_changed.emit()

    def _on_rerun_shape(self) -> None:
        self.intervention_requested.emit()
        self.sync_params_to_state()
        self.shape_rerun_requested.emit()

    def _on_auto_toggle(self) -> None:
        if self._wizard.auto_processing_paused():
            self.resume_auto_processing_requested.emit()
        else:
            self.stop_auto_processing_requested.emit()

    def clear_views(self) -> None:
        self._presenter.clear_views()

    def ingest_skill_result(self, result: dict, *, skill_name: str = "") -> None:
        self._presenter.ingest_skill_result(result, skill_name=skill_name)

    def load_from_disk(
        self,
        *,
        watchdir: Path,
        stem: str,
        tiff_path: str = "",
        watch_mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
    ) -> None:
        self._presenter.load_from_disk(
            watchdir=watchdir,
            stem=stem,
            tiff_path=tiff_path,
            watch_mode=watch_mode,
        )

    @property
    def last_guinier_handoff(self) -> Dict[str, Any]:
        return self._presenter.last_guinier_handoff

    @property
    def last_gnom_out(self) -> str:
        return self._presenter.last_gnom_out

    def gnom_out_for_dammif(self) -> str:
        return self._presenter.gnom_out_for_dammif()

    @property
    def profile_path(self) -> str:
        return self._presenter.profile_path

    @property
    def output_root(self) -> Optional[Path]:
        return self._presenter.output_root
