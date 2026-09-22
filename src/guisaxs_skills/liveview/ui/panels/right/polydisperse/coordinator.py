"""Thin Qt wiring between polydisperse window panes, config sync, and artifact presenter."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from .....session.state import LiveviewSessionState, LiveviewWatchMode
from .artifact_presenter import PolydisperseArtifactPresenter
from .config_sync import PolydisperseConfigSync
from .format_display import format_sizes_passport_html


class PolydisperseCoordinator(QObject):
    intervention_requested = pyqtSignal()
    mixture_config_changed = pyqtSignal()
    guinier_rerun_requested = pyqtSignal()
    sizes_rerun_requested = pyqtSignal()
    mixture_rerun_requested = pyqtSignal()
    resume_auto_processing_requested = pyqtSignal()
    stop_auto_processing_requested = pyqtSignal()

    def __init__(self, *, state: LiveviewSessionState, window: Any) -> None:
        super().__init__()
        self._state = state
        self._window = window
        self._config = PolydisperseConfigSync(state=state, window=window)
        self._presenter = PolydisperseArtifactPresenter(state=state, window=window)
        self._sizes_adjust = None
        self._connect_window()

    def _connect_window(self) -> None:
        w = self._window
        w.guinier_pane.range_changed.connect(self._on_guinier_range_changed)
        w.sizes_pane.adjust_requested.connect(self.open_sizes_adjust_wizard)
        w.mixture_pane.mode_changed.connect(self._on_mixture_mode_changed)
        w.mixture_pane.params_changed.connect(self._on_mixture_params_changed)
        w.mixture_pane.rerun_mixture_requested.connect(self._on_rerun_mixture)
        w.auto_toggle_clicked.connect(self._on_auto_toggle)
        router = getattr(w, "_plot_clicks", None)
        if router is not None and hasattr(router, "set_sizes_open_handler"):
            router.set_sizes_open_handler(self.open_sizes_adjust_wizard)

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

    def open_sizes_adjust_wizard(self) -> None:
        from ....wizards.sizes_adjust import SizesAdjustWizardDialog

        from ......ui.style import COLOR_QUALITY_POOR

        if self._sizes_adjust is None:
            parent = self._window.window() if self._window is not None else None
            self._sizes_adjust = SizesAdjustWizardDialog(parent)
            self._sizes_adjust.editing_started.connect(self.intervention_requested.emit)
            self._sizes_adjust.params_changed.connect(self._on_sizes_adjust_params_changed)
        prof = self._presenter.profile_path
        wp = dict(self._state.polydisperse_window_params or {})
        auto = dict(wp.get("sizes_auto") or {})
        working = {
            k: wp[k]
            for k in ("first", "last", "rmin_nm", "rmax_nm", "alpha", "force_zero_rmin", "force_zero_rmax")
            if k in wp
        }
        passport_html = ""
        quality = getattr(self._presenter, "last_sizes_result", None) or {}
        if quality:
            passport_html = format_sizes_passport_html(quality, poor_color=COLOR_QUALITY_POOR)
        if not passport_html or passport_html == "—":
            try:
                passport_html = self._window.sizes_pane._lbl_diagnostics.text()  # noqa: SLF001
            except Exception:
                passport_html = ""
        self._sizes_adjust.set_context(
            profile_path=prof,
            auto_snapshot=auto,
            working_params=working,
            gnom_out_path=self._presenter.last_gnom_out,
            passport_html=passport_html if passport_html and passport_html != "—" else "",
        )
        self._sizes_adjust.show()
        self._sizes_adjust.raise_()
        self._sizes_adjust.activateWindow()

    def _on_guinier_range_changed(self, first: int, last: int) -> None:
        self.intervention_requested.emit()
        self._config.store_guinier_interval(int(first), int(last))
        self.guinier_rerun_requested.emit()

    def _on_sizes_adjust_params_changed(self) -> None:
        if self._sizes_adjust is not None:
            self._config.store_sizes_working(self._sizes_adjust.sizes_params())
        self.sync_params_to_state()
        self.sizes_rerun_requested.emit()

    def _on_mixture_mode_changed(self, mode: str) -> None:
        self._config.apply_mixture_mode(mode)
        if str(mode).lower() == "none":
            self._window.mixture_pane.clear_view()
        self._window.mixture_pane.set_rerun_enabled(self._presenter.can_rerun_mixture())
        self.mixture_config_changed.emit()

    def _on_mixture_params_changed(self) -> None:
        self.intervention_requested.emit()
        self.sync_params_to_state()
        self.mixture_rerun_requested.emit()

    def _on_rerun_mixture(self) -> None:
        self.intervention_requested.emit()
        self.sync_params_to_state()
        self.mixture_rerun_requested.emit()

    def _on_auto_toggle(self) -> None:
        if self._window.auto_processing_paused():
            self.resume_auto_processing_requested.emit()
        else:
            self.stop_auto_processing_requested.emit()

    def clear_views(self) -> None:
        self._presenter.clear_views()

    def ingest_skill_result(self, result: dict, *, skill_name: str = "") -> None:
        self._presenter.ingest_skill_result(result, skill_name=skill_name)
        if self._sizes_adjust is not None and self._sizes_adjust.isVisible():
            gnom_out = self._presenter.last_gnom_out
            if gnom_out:
                self._sizes_adjust.show_from_gnom_out(gnom_out)
            quality = getattr(self._presenter, "last_sizes_result", None) or {}
            if quality:
                self._sizes_adjust.set_passport_from_quality(quality)

    def apply_bundle(self, bundle: object) -> None:
        self._presenter.apply_bundle(bundle)

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

    def summary_text(self) -> tuple[str, str]:
        return self._presenter.summary_text()

    def set_running(self, running: bool) -> None:
        try:
            self._window.sizes_pane.set_running(bool(running))
        except Exception:
            pass
        if self._sizes_adjust is not None:
            self._sizes_adjust.set_running(bool(running))

    @property
    def profile_path(self) -> str:
        return self._presenter.profile_path

    @property
    def output_root(self) -> Optional[Path]:
        return self._presenter.output_root

    @property
    def config_sync(self) -> PolydisperseConfigSync:
        return self._config
