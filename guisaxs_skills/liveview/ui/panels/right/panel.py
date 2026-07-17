from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ....pipeline.monodisperse_pipeline import FIT_GUINIER_MONO_STEP, FIT_GUINIER_POLY_STEP
from ....session.state import LiveviewSessionState
from ...icons import monodisperse_analysis_icon, polydisperse_analysis_icon
from ...log_panel import LiveviewLogPanel
from .config_restore import RightPanelConfigRestore
from .form_helpers import discover_fit_skill_meta
from .monodisperse.coordinator import MonodisperseCoordinator
from .monodisperse.wizard import MonodisperseWizardWidget
from .polydisperse.coordinator import PolydisperseCoordinator
from .polydisperse.window_widget import PolydisperseWindowWidget

if TYPE_CHECKING:
    from ...wizards.monodisperse import MonodisperseWizardDialog
    from ...windows.polydisperse import PolydisperseAnalysisWindow


_MONO_SKILLS = frozenset({"fit_guinier", "fit_distances", "fit_dammif", "fit_bodies"})
_POLY_SKILLS = frozenset({"fit_guinier", "fit_sizes", "fit_mixture"})


class LiveviewRightPanel(QWidget):
    analysis_arming_changed = pyqtSignal()
    modeling_enabled_changed = pyqtSignal(bool)
    monodisperse_wizard_open_requested = pyqtSignal()
    monodisperse_intervention = pyqtSignal()
    monodisperse_shape_config = pyqtSignal()
    monodisperse_guinier_chain = pyqtSignal()
    monodisperse_gnom_rerun = pyqtSignal()
    monodisperse_shape_rerun = pyqtSignal()
    monodisperse_resume_queue = pyqtSignal()
    monodisperse_stop_queue = pyqtSignal()
    polydisperse_window_open_requested = pyqtSignal()
    polydisperse_intervention = pyqtSignal()
    polydisperse_mixture_config = pyqtSignal()
    polydisperse_guinier_rerun = pyqtSignal()
    polydisperse_sizes_rerun = pyqtSignal()
    polydisperse_mixture_rerun = pyqtSignal()
    polydisperse_resume_queue = pyqtSignal()
    polydisperse_stop_queue = pyqtSignal()

    def __init__(self, *, state: LiveviewSessionState) -> None:
        super().__init__()
        self._state = state
        self._meta_fit, self._meta_sizes, self._meta_mixture = discover_fit_skill_meta()
        self._config = RightPanelConfigRestore(state=state)
        self._log = LiveviewLogPanel(parent=self)

        self._mono_wizard_widget = MonodisperseWizardWidget()
        self._mono_dialog: Optional[MonodisperseWizardDialog] = None
        self._mono = MonodisperseCoordinator(state=state, wizard=self._mono_wizard_widget)
        self._mono_wizard_widget.bind_state(state)
        self._wire_monodisperse_coordinator()

        self._poly_window_widget = PolydisperseWindowWidget()
        self._poly_dialog: Optional[PolydisperseAnalysisWindow] = None
        self._poly = PolydisperseCoordinator(state=state, window=self._poly_window_widget)
        self._poly_window_widget.bind_state(state)
        self._wire_polydisperse_coordinator()

        self._btn_mono = QToolButton()
        self._btn_mono.setIcon(monodisperse_analysis_icon())
        self._btn_mono.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_mono.setCheckable(True)
        self._btn_mono.setAutoRaise(True)
        self._btn_mono.setToolTip(
            "Monodisperse analysis (Guinier → GNOM → shape).\n"
            "Opens the window and arms that chain for new TIFFs while open."
        )
        self._btn_mono.clicked.connect(self._on_mono_button)

        self._btn_poly = QToolButton()
        self._btn_poly.setIcon(polydisperse_analysis_icon())
        self._btn_poly.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._btn_poly.setCheckable(True)
        self._btn_poly.setAutoRaise(True)
        self._btn_poly.setToolTip(
            "Polydisperse analysis (Guinier → D(R) → optional mixture).\n"
            "Opens the window and arms that chain for new TIFFs while open."
        )
        self._btn_poly.clicked.connect(self._on_poly_button)

        for btn in (self._btn_mono, self._btn_poly):
            btn.setFixedSize(72, 72)
            btn.setIconSize(btn.size() * 0.88)

        tools = QWidget()
        tools_lay = QVBoxLayout(tools)
        tools_lay.setContentsMargins(4, 4, 4, 4)
        title = QLabel("Analysis")
        title.setStyleSheet("font-weight: 600;")
        row = QHBoxLayout()
        row.addWidget(self._btn_mono)
        row.addWidget(self._btn_poly)
        row.addStretch(1)
        tools_lay.addWidget(title)
        tools_lay.addLayout(row)
        tools_lay.addStretch(1)
        tools.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

        split = QSplitter(Qt.Vertical)
        split.addWidget(tools)
        split.addWidget(self._log)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 4)
        split.setSizes([120, 480])

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(split)

        self._config.reload_all()
        if self._meta_fit is None:
            self._btn_mono.setEnabled(False)
            self._btn_poly.setEnabled(False)
        self.sync_modeling_ui_to_session_state()

    @property
    def log_panel(self) -> LiveviewLogPanel:
        return self._log

    def _wire_monodisperse_coordinator(self) -> None:
        self._mono.intervention_requested.connect(self.monodisperse_intervention.emit)
        self._mono.shape_config_changed.connect(self.monodisperse_shape_config.emit)
        self._mono.guinier_chain_requested.connect(self.monodisperse_guinier_chain.emit)
        self._mono.gnom_rerun_requested.connect(self.monodisperse_gnom_rerun.emit)
        self._mono.shape_rerun_requested.connect(self.monodisperse_shape_rerun.emit)
        self._mono.resume_auto_processing_requested.connect(self.monodisperse_resume_queue.emit)
        self._mono.stop_auto_processing_requested.connect(self.monodisperse_stop_queue.emit)

    def _wire_polydisperse_coordinator(self) -> None:
        self._poly.intervention_requested.connect(self.polydisperse_intervention.emit)
        self._poly.mixture_config_changed.connect(self.polydisperse_mixture_config.emit)
        self._poly.guinier_rerun_requested.connect(self.polydisperse_guinier_rerun.emit)
        self._poly.sizes_rerun_requested.connect(self.polydisperse_sizes_rerun.emit)
        self._poly.mixture_rerun_requested.connect(self.polydisperse_mixture_rerun.emit)
        self._poly.resume_auto_processing_requested.connect(self.polydisperse_resume_queue.emit)
        self._poly.stop_auto_processing_requested.connect(self.polydisperse_stop_queue.emit)

    @property
    def monodisperse_coordinator(self) -> MonodisperseCoordinator:
        return self._mono

    @property
    def monodisperse_wizard(self) -> MonodisperseWizardWidget:
        return self._mono_wizard_widget

    @property
    def polydisperse_coordinator(self) -> PolydisperseCoordinator:
        return self._poly

    @property
    def polydisperse_window(self) -> PolydisperseWindowWidget:
        return self._poly_window_widget

    def _on_mono_button(self) -> None:
        self.monodisperse_wizard_open_requested.emit()
        self.show_monodisperse_wizard()

    def _on_poly_button(self) -> None:
        self.polydisperse_window_open_requested.emit()
        self.show_polydisperse_window()

    def _emit_arming(self, *, prev_enabled: bool) -> None:
        self._sync_button_checked()
        now = self._state.analysis_enabled()
        if prev_enabled != now:
            self.modeling_enabled_changed.emit(now)
        self.analysis_arming_changed.emit()

    def _sync_button_checked(self) -> None:
        self._btn_mono.blockSignals(True)
        self._btn_poly.blockSignals(True)
        try:
            self._btn_mono.setChecked(bool(self._state.monodisperse_armed))
            self._btn_poly.setChecked(bool(self._state.polydisperse_armed))
        finally:
            self._btn_mono.blockSignals(False)
            self._btn_poly.blockSignals(False)

    def show_monodisperse_wizard(self) -> None:
        from ...wizards.monodisperse import MonodisperseWizardDialog

        prev = self._state.analysis_enabled()
        if self._mono_dialog is None:
            self._mono_dialog = MonodisperseWizardDialog(
                wizard=self._mono_wizard_widget,
                parent=self,
            )
            self._mono_dialog.closed.connect(self._on_mono_dialog_closed)
        if not self._state.monodisperse_armed:
            self._state.monodisperse_armed = True
            self._log.append_app("Monodisperse analysis armed")
            self._emit_arming(prev_enabled=prev)
        self._mono_dialog.show()
        self._mono_dialog.raise_()
        self._mono_dialog.activateWindow()
        self._sync_button_checked()

    def show_polydisperse_window(self) -> None:
        from ...windows.polydisperse import PolydisperseAnalysisWindow

        prev = self._state.analysis_enabled()
        if self._poly_dialog is None:
            self._poly_dialog = PolydisperseAnalysisWindow(
                window_widget=self._poly_window_widget,
                parent=self,
            )
            self._poly_dialog.closed.connect(self._on_poly_dialog_closed)
        if not self._state.polydisperse_armed:
            self._state.polydisperse_armed = True
            self._log.append_app("Polydisperse analysis armed")
            self._emit_arming(prev_enabled=prev)
        self._poly_dialog.show()
        self._poly_dialog.raise_()
        self._poly_dialog.activateWindow()
        self._sync_button_checked()

    def _on_mono_dialog_closed(self) -> None:
        if not self._state.monodisperse_armed:
            self._sync_button_checked()
            return
        prev = self._state.analysis_enabled()
        self._state.monodisperse_armed = False
        self._log.append_app("Monodisperse analysis disarmed")
        self._emit_arming(prev_enabled=prev)

    def _on_poly_dialog_closed(self) -> None:
        if not self._state.polydisperse_armed:
            self._sync_button_checked()
            return
        prev = self._state.analysis_enabled()
        self._state.polydisperse_armed = False
        self._log.append_app("Polydisperse analysis disarmed")
        self._emit_arming(prev_enabled=prev)

    def close_analysis_windows(self) -> None:
        """Close both analysis dialogs and clear arm flags (used on calibration/buffer reset)."""
        prev = self._state.analysis_enabled()
        self._state.monodisperse_armed = False
        self._state.polydisperse_armed = False
        if self._mono_dialog is not None and self._mono_dialog.isVisible():
            self._mono_dialog.hide()
        if self._poly_dialog is not None and self._poly_dialog.isVisible():
            self._poly_dialog.hide()
        if prev:
            self._log.append_app("Analysis disarmed (session reset)")
            self.modeling_enabled_changed.emit(False)
        self._sync_button_checked()
        self.analysis_arming_changed.emit()

    def clear_output_previews(self) -> None:
        self._mono.clear_views()
        self._poly.clear_views()

    def reload_configs_from_watchdir(self) -> None:
        self._config.reload_all()

    def force_analysis_disarmed(self) -> None:
        self.close_analysis_windows()

    # Back-compat alias for older call sites during transition.
    force_analysis_mode_off = force_analysis_disarmed

    def sync_modeling_ui_to_session_state(
        self, *, queue_paused: bool | None = None, processing_idle: bool | None = None
    ) -> None:
        if self._meta_fit is None:
            return
        self._sync_button_checked()
        paused = bool(queue_paused) if queue_paused is not None else False
        idle = bool(processing_idle) if processing_idle is not None else True
        self._mono_wizard_widget.set_queue_paused(paused, processing_idle=idle)
        self._poly_window_widget.set_queue_paused(paused, processing_idle=idle)

    def set_queue_ui(self, *, paused: bool, processing_idle: bool) -> None:
        self._mono_wizard_widget.set_queue_paused(paused, processing_idle=processing_idle)
        self._poly_window_widget.set_queue_paused(paused, processing_idle=processing_idle)

    def set_monodisperse_running(self, running: bool) -> None:
        self._mono_wizard_widget.set_running(running)
        if self._mono_dialog is not None:
            self._mono_dialog.set_running(running)

    def set_polydisperse_running(self, running: bool) -> None:
        self._poly_window_widget.set_running(running)
        if self._poly_dialog is not None:
            self._poly_dialog.set_running(running)

    def ingest_skill_result(self, result: dict, *, skill_name: str = "") -> None:
        if not isinstance(result, dict):
            return
        sn = (skill_name or result.get("skill_name") or "").strip()
        step = str(result.get("_liveview_step") or "").strip()
        route_mono, route_poly = self._route_ingest(sn=sn, step=step, result=result)
        if route_mono and self._state.monodisperse_armed:
            self._mono.ingest_skill_result(result, skill_name=sn)
        if route_poly and self._state.polydisperse_armed:
            self._poly.ingest_skill_result(result, skill_name=sn)

    def _route_ingest(self, *, sn: str, step: str, result: dict) -> tuple[bool, bool]:
        if step == FIT_GUINIER_POLY_STEP:
            return False, True
        if step == FIT_GUINIER_MONO_STEP:
            return True, False
        if sn == "fit_guinier" or result.get("guinier_region_path"):
            path_hint = " ".join(
                str(result.get(k) or "")
                for k in ("guinier_region_path", "output_subdir", "output_dir")
            ).replace("\\", "/")
            if "guinier_poly" in path_hint:
                return False, True
            if "guinier_mono" in path_hint:
                return True, False
            # Ambiguous: only the armed chain that matches skill family.
            return self._state.monodisperse_armed, self._state.polydisperse_armed and not self._state.monodisperse_armed
        if sn in ("fit_distances", "fit_dammif", "fit_bodies") or sn in _MONO_SKILLS - {"fit_guinier"}:
            return True, False
        if sn in ("fit_sizes", "fit_mixture") or sn in _POLY_SKILLS - {"fit_guinier"}:
            return False, True
        return False, False

    def set_analysis_busy(self, running: bool) -> None:
        if self._state.monodisperse_armed:
            self.set_monodisperse_running(running)
        if self._state.polydisperse_armed:
            self.set_polydisperse_running(running)

    set_fit_distances_running = set_analysis_busy

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

    def load_polydisperse_from_disk(
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
        self._poly.set_context(
            profile_path=profile_path,
            output_root=root,
            tiff_path=tiff_path,
            watch_mode=self._state.watch_mode,
        )
        self._poly.load_from_disk(
            watchdir=self._state.watchdir,
            stem=stem,
            tiff_path=tiff_path,
            watch_mode=self._state.watch_mode,
        )
