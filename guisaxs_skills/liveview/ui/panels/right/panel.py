from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QGroupBox, QVBoxLayout, QWidget

from ....session.state import AnalysisMode, LiveviewSessionState
from .config_restore import RightPanelConfigRestore
from .form_helpers import discover_fit_skill_meta
from .mode_selector import AnalysisModeSelector
from .monodisperse.coordinator import MonodisperseCoordinator
from .monodisperse.wizard import MonodisperseWizardWidget
from .output_views import AnalysisOutputViews
from .wizard_coordinator import FitWizardCoordinator

if TYPE_CHECKING:
    from ...wizards.monodisperse import MonodisperseWizardDialog


class LiveviewRightPanel(QWidget):
    analysis_mode_changed = pyqtSignal(object)
    modeling_enabled_changed = pyqtSignal(bool)
    modeling_config_changed = pyqtSignal()
    fit_sizes_run_requested = pyqtSignal()
    fit_mixture_run_requested = pyqtSignal()
    monodisperse_wizard_open_requested = pyqtSignal()
    monodisperse_intervention = pyqtSignal()
    monodisperse_shape_config = pyqtSignal()
    monodisperse_guinier_chain = pyqtSignal()
    monodisperse_gnom_rerun = pyqtSignal()
    monodisperse_shape_rerun = pyqtSignal()
    monodisperse_resume_queue = pyqtSignal()
    monodisperse_stop_queue = pyqtSignal()

    def __init__(self, *, state: LiveviewSessionState) -> None:
        super().__init__()
        self._state = state
        self._meta_fit, self._meta_sizes, self._meta_mixture = discover_fit_skill_meta()
        self._config = RightPanelConfigRestore(state=state)
        self._outputs = AnalysisOutputViews(parent=self)
        self._mono_wizard_widget = MonodisperseWizardWidget()
        self._mono_dialog: Optional[MonodisperseWizardDialog] = None
        self._mono = MonodisperseCoordinator(state=state, wizard=self._mono_wizard_widget)
        self._mono_wizard_widget.bind_state(state)
        self._wire_monodisperse_coordinator()

        self._mode = AnalysisModeSelector(
            state=state,
            on_open_fit_sizes=self._open_fit_sizes,
            on_open_fit_mixture=self._open_fit_mixture,
            on_open_monodisperse=self._open_monodisperse,
            parent=self,
        )
        self._wizards = FitWizardCoordinator(
            state=state,
            parent=self,
            meta_fit=self._meta_fit,
            meta_sizes=self._meta_sizes,
            meta_mixture=self._meta_mixture,
            mode_selector=self._mode,
            on_config_changed=self.modeling_config_changed.emit,
            on_modeling_enabled=self.modeling_enabled_changed.emit,
            config_restore=self._config,
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        grp = QGroupBox("Analysis")
        gl = QVBoxLayout(grp)
        gl.addWidget(self._mode)
        gl.addWidget(self._outputs, 1)
        root.addWidget(grp)

        self._mode.analysis_mode_changed.connect(self._on_mode_changed)
        self._mode.analysis_mode_changed.connect(self.analysis_mode_changed.emit)
        self._mode.modeling_enabled_changed.connect(self.modeling_enabled_changed.emit)

        self._config.reload_all()
        if self._meta_fit is None:
            grp.setEnabled(False)
        self._outputs.update_monodisperse_summary(
            hint="Open Monodisperse analysis to inspect Guinier, GNOM, and shape fits.",
            status="",
        )
        self.sync_modeling_ui_to_session_state()

    def _wire_monodisperse_coordinator(self) -> None:
        self._mono.intervention_requested.connect(self.monodisperse_intervention.emit)
        self._mono.shape_config_changed.connect(self.monodisperse_shape_config.emit)
        self._mono.guinier_chain_requested.connect(self.monodisperse_guinier_chain.emit)
        self._mono.gnom_rerun_requested.connect(self.monodisperse_gnom_rerun.emit)
        self._mono.shape_rerun_requested.connect(self.monodisperse_shape_rerun.emit)
        self._mono.resume_auto_processing_requested.connect(self.monodisperse_resume_queue.emit)
        self._mono.stop_auto_processing_requested.connect(self.monodisperse_stop_queue.emit)
        # auto_toggle_clicked → _on_auto_toggle is wired once in MonodisperseCoordinator._connect_wizard.
        # Do not connect it again here: a double handler resumes then immediately re-pauses (and vice versa).

    @property
    def monodisperse_coordinator(self) -> MonodisperseCoordinator:
        return self._mono

    @property
    def monodisperse_wizard(self) -> MonodisperseWizardWidget:
        return self._mono_wizard_widget

    def _on_mode_changed(self, mode: object) -> None:
        if isinstance(mode, AnalysisMode):
            self._outputs.set_mode_index(self._mode.mode_stack_index(mode))

    def _open_fit_sizes(self) -> None:
        self._wizards.open_fit_sizes()

    def _open_fit_mixture(self) -> None:
        self._wizards.open_fit_mixture()

    def _open_monodisperse(self) -> None:
        self.monodisperse_wizard_open_requested.emit()
        self.show_monodisperse_wizard()

    def show_monodisperse_wizard(self) -> None:
        from ...wizards.monodisperse import MonodisperseWizardDialog

        if self._mono_dialog is None:
            self._mono_dialog = MonodisperseWizardDialog(
                wizard=self._mono_wizard_widget,
                parent=self,
            )
        self._mono_dialog.show()
        self._mono_dialog.raise_()
        self._mono_dialog.activateWindow()

    def clear_output_previews(self) -> None:
        self._outputs.clear_previews()
        self._mono.clear_views()
        self._outputs.update_monodisperse_summary(
            hint="Open Monodisperse analysis to inspect Guinier, GNOM, and shape fits.",
            status="",
        )

    def reload_configs_from_watchdir(self) -> None:
        self._config.reload_all()

    def force_analysis_mode_off(self) -> None:
        self._mode.force_off()
        self._outputs.set_mode_index(0)

    def sync_modeling_ui_to_session_state(self, *, queue_paused: bool | None = None, processing_idle: bool | None = None) -> None:
        if self._meta_fit is None:
            return
        self._mode.sync_from_state(fit_skills_available=True)
        self._outputs.set_mode_index(self._mode.mode_stack_index(self._state.analysis_mode))
        paused = bool(queue_paused) if queue_paused is not None else self._mono_wizard_widget.auto_processing_paused()
        idle = bool(processing_idle) if processing_idle is not None else True
        self._mono_wizard_widget.set_queue_paused(paused, processing_idle=idle)

    def set_queue_ui(self, *, paused: bool, processing_idle: bool) -> None:
        self._mono_wizard_widget.set_queue_paused(paused, processing_idle=processing_idle)

    def set_monodisperse_running(self, running: bool) -> None:
        self._mono_wizard_widget.set_running(running)
        if self._mono_dialog is not None:
            self._mono_dialog.set_running(running)

    def ingest_skill_result(self, result: dict, *, skill_name: str = "") -> None:
        if self._state.analysis_mode == AnalysisMode.MONODISPERSE:
            self._mono.ingest_skill_result(result, skill_name=skill_name)
            self._refresh_monodisperse_summary()
            return
        self._outputs.ingest_skill_result(result, watchdir=self._state.watchdir, skill_name=skill_name)

    def _refresh_monodisperse_summary(self) -> None:
        hint, status = self._mono_wizard_widget.summary_lines()
        self._outputs.update_monodisperse_summary(hint=hint, status=status)

    def set_analysis_busy(self, running: bool) -> None:
        """Global wizard lock while any skill subprocess is running."""
        self._wizards.set_running(running)
        if self._state.analysis_mode == AnalysisMode.MONODISPERSE:
            self.set_monodisperse_running(running)

    # Back-compat alias
    set_fit_distances_running = set_analysis_busy

    def build_fit_sizes_request_from_wizard(self):
        return self._wizards.build_fit_sizes_request()

    def build_fit_mixture_request_from_wizard(self):
        return self._wizards.build_fit_mixture_request()

    def fit_sizes_wizard_has_existing_profile_file(self, watchdir: Path) -> bool:
        return self._wizards.fit_sizes_has_profile(watchdir)

    def fit_mixture_wizard_has_existing_profile_file(self, watchdir: Path) -> bool:
        return self._wizards.fit_mixture_has_profile(watchdir)

    def save_fit_sizes_conf_from_open_wizard(self, *, enable_modeling: bool = True) -> None:
        self._wizards.save_fit_sizes_conf(enable_modeling=enable_modeling)

    def save_fit_mixture_conf_from_open_wizard(self, *, enable_modeling: bool = True) -> None:
        self._wizards.save_fit_mixture_conf(enable_modeling=enable_modeling)

    def load_monodisperse_from_disk(
        self,
        *,
        profile_path: str,
        stem: str,
        tiff_path: str = "",
    ) -> None:
        from ....session.output_paths import tiff_output_root

        root = tiff_output_root(
            watchdir=self._state.watchdir,
            tiff_path=tiff_path,
            mode=self._state.watch_mode,
        )
        self._mono.set_context(
            profile_path=profile_path,
            output_root=root,
            tiff_path=tiff_path,
            watch_mode=self._state.watch_mode,
        )
        self._mono.load_from_disk(
            watchdir=self._state.watchdir,
            stem=stem,
            tiff_path=tiff_path,
            watch_mode=self._state.watch_mode,
        )
        self._refresh_monodisperse_summary()
