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

    def __init__(
        self,
        *,
        state: LiveviewSessionState,
        wizard: Any,
        modeling: Any = None,
    ) -> None:
        super().__init__()
        self._state = state
        self._wizard = wizard
        self._modeling = modeling
        self._config = MonodisperseConfigSync(state=state, wizard=wizard)
        self._presenter = MonodisperseArtifactPresenter(state=state, wizard=wizard)
        self._gnom_adjust = None
        self._guinier_adjust = None
        self._connect_wizard()

    def _connect_wizard(self) -> None:
        w = self._wizard
        w.guinier_pane.adjust_requested.connect(self.open_guinier_adjust_wizard)
        w.gnom_pane.adjust_requested.connect(self.open_gnom_adjust_wizard)
        w.shape_pane.mode_changed.connect(self._on_shape_mode_changed)
        w.shape_pane.n_runs_changed.connect(self._on_n_runs_changed)
        w.shape_pane.denss_settings_changed.connect(self._on_denss_settings_changed)
        w.shape_pane.rerun_shape_requested.connect(self._on_rerun_shape)
        w.shape_pane.start_modeling_requested.connect(self._on_start_modeling)
        w.auto_toggle_clicked.connect(self._on_auto_toggle)
        # Click on Guinier / P(r) / I(q) embedded plots opens the adjust wizards.
        from .plot_clicks import MonodispersePlotClickRouter

        router = getattr(w, "_plot_clicks", None)
        if isinstance(router, MonodispersePlotClickRouter):
            router.set_gnom_open_handler(self.open_gnom_adjust_wizard)
            router.set_guinier_open_handler(self.open_guinier_adjust_wizard)

    def set_context(
        self,
        *,
        profile_path: str,
        output_root: Path,
        tiff_path: str = "",
        watch_mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
        stem: str = "",
        sample_id: str = "",
    ) -> None:
        self._presenter.set_context(
            profile_path=profile_path,
            output_root=output_root,
            tiff_path=tiff_path,
            watch_mode=watch_mode,
            stem=stem,
        )
        if self._modeling is not None:
            self._modeling.push_shape_context(
                profile_path=profile_path,
                gnom_path=self.gnom_out_for_dammif() or "",
                output_root=output_root,
                stem=stem or getattr(self._presenter, "sample_stem", "") or "",
                sample_id=sample_id or tiff_path or "",
            )

    def sync_params_to_state(self) -> None:
        self._config.sync_params_to_state()

    def open_guinier_adjust_wizard(self) -> None:
        from ....wizards.guinier_adjust import GuinierAdjustWizardDialog

        if self._guinier_adjust is None:
            parent = self._wizard.window() if self._wizard is not None else None
            self._guinier_adjust = GuinierAdjustWizardDialog(parent)
            self._guinier_adjust.editing_started.connect(self.intervention_requested.emit)
            self._guinier_adjust.params_changed.connect(self._on_guinier_adjust_params_changed)
        prof = self._presenter.profile_path
        wp = dict(self._state.monodisperse_wizard_params or {})
        auto = dict(wp.get("guinier_auto") or {})
        working: Dict[str, Any] = {}
        if wp.get("guinier_first") is not None and wp.get("guinier_last") is not None:
            working["first"] = int(wp["guinier_first"])
            working["last"] = int(wp["guinier_last"])
        elif auto.get("first") is not None and auto.get("last") is not None:
            working["first"] = int(auto["first"])
            working["last"] = int(auto["last"])
        else:
            try:
                gf, gl = self._wizard.guinier_pane.first_last()
                if gf is not None and gl is not None:
                    working["first"] = int(gf)
                    working["last"] = int(gl)
            except Exception:
                pass
        results = getattr(self._presenter, "last_guinier_results", "") or ""
        if not results:
            try:
                results = str(getattr(self._presenter, "_last_guinier_results", "") or "")
            except Exception:
                results = ""
        if (not working) and results:
            try:
                from autosaxs.core.guinier import parse_guinier_results_txt
                from autosaxs.skill.fit_guinier.guinier import guinier_point_range_1based

                parsed = parse_guinier_results_txt(results)
                fp = parsed.get("first_point_1based")
                lp = parsed.get("last_point_1based")
                if fp is None or lp is None:
                    fp, lp = guinier_point_range_1based(parsed)
                if fp is not None and lp is not None:
                    working["first"] = int(fp)
                    working["last"] = int(lp)
            except Exception:
                pass
        self._guinier_adjust.set_context(
            profile_path=prof,
            working_params=working,
            auto_snapshot=auto,
            results_path=results,
        )
        self._guinier_adjust.show()
        self._guinier_adjust.raise_()
        self._guinier_adjust.activateWindow()

    def open_gnom_adjust_wizard(self) -> None:
        from ....wizards.gnom_adjust import GnomAdjustWizardDialog

        if self._gnom_adjust is None:
            parent = self._wizard.window() if self._wizard is not None else None
            self._gnom_adjust = GnomAdjustWizardDialog(parent)
            self._gnom_adjust.editing_started.connect(self.intervention_requested.emit)
            self._gnom_adjust.params_changed.connect(self._on_gnom_adjust_params_changed)
        prof = self._presenter.profile_path
        wp = dict(self._state.monodisperse_wizard_params or {})
        auto = dict(wp.get("gnom_auto") or {})
        working = {
            k: wp[k]
            for k in (
                "first",
                "last",
                "q_min",
                "q_max",
                "dmax_nm",
                "alpha",
                "force_zero_rmin",
                "force_zero_rmax",
                "rg_nm",
            )
            if k in wp
        }
        gnom_out = self._presenter.last_gnom_out
        # Fill missing boundary flags from the on-disk .out (do not override session
        # values — those may be newer than disk while a refine job is still running).
        if gnom_out:
            try:
                from autosaxs.core.atsas_gnom import normalize_force_zero
                from autosaxs.core.gnom import parse_gnom_out

                parsed = parse_gnom_out(gnom_out)
                for key in ("force_zero_rmin", "force_zero_rmax"):
                    if key not in working and parsed.get(key) is not None:
                        working[key] = normalize_force_zero(parsed.get(key))
            except Exception:
                pass
        passport_html = ""
        quality = getattr(self._presenter, "last_gnom_result", None) or {}
        if quality:
            from .format_display import format_gnom_passport_html
            from ......ui.style import COLOR_QUALITY_POOR

            passport_html = format_gnom_passport_html(
                quality,
                guinier_handoff=self._presenter.last_guinier_handoff,
                poor_color=COLOR_QUALITY_POOR,
            )
        if not passport_html or passport_html == "—":
            try:
                passport_html = self._wizard.gnom_pane._lbl_diagnostics.text()  # noqa: SLF001
            except Exception:
                passport_html = ""
        self._gnom_adjust.set_context(
            profile_path=prof,
            guinier_handoff=self._presenter.last_guinier_handoff,
            auto_snapshot=auto,
            working_params=working,
            gnom_out_path=gnom_out,
            passport_text="",
            passport_html=passport_html if passport_html and passport_html != "—" else "",
        )
        self._gnom_adjust.show()
        self._gnom_adjust.raise_()
        self._gnom_adjust.activateWindow()

    def _on_guinier_adjust_params_changed(self) -> None:
        if self._guinier_adjust is not None:
            p = self._guinier_adjust.guinier_params()
            self._config.store_guinier_interval(int(p["first"]), int(p["last"]))
            try:
                self._wizard.guinier_pane.set_range(int(p["first"]), int(p["last"]))
            except Exception:
                pass
        self.sync_params_to_state()
        self.guinier_chain_requested.emit()

    def _on_gnom_adjust_params_changed(self) -> None:
        # Pause already fired via editing_started on first edit.
        if self._gnom_adjust is not None:
            self._config.store_gnom_working(self._gnom_adjust.gnom_params())
        self.sync_params_to_state()
        self.gnom_rerun_requested.emit()

    def _on_shape_mode_changed(self, mode: str) -> None:
        self._config.apply_shape_mode(mode)
        m = str(mode).lower()
        if m == "none":
            self._wizard.shape_pane.clear_view()
            self._wizard.shape_pane._update_mode_ui()
        else:
            self._wizard.shape_pane._update_mode_ui()
            self._presenter.refresh_shape_view_for_current_mode()
        self._wizard.shape_pane.set_rerun_enabled(self._presenter.can_rerun_shape())
        self.shape_config_changed.emit()

    def _on_n_runs_changed(self, n: int) -> None:
        self._config.apply_n_runs(n)

    def _on_denss_settings_changed(self) -> None:
        self._config.apply_denss_settings()

    def _on_rerun_shape(self) -> None:
        self.intervention_requested.emit()
        self.sync_params_to_state()
        self.shape_rerun_requested.emit()

    def _on_start_modeling(self) -> None:
        if self._modeling is None:
            return
        prof = self.profile_path or ""
        gnom = self.gnom_out_for_dammif() or ""
        root = self.output_root
        if root is None:
            root = Path(self._state.watchdir).expanduser().resolve()
        stem = getattr(self._presenter, "sample_stem", "") or ""
        # Disk-backed params (n_runs, …) win over stale slim-pane defaults.
        self._modeling.build_shape_context(
            profile_path=prof,
            gnom_path=gnom,
            output_root=root,
            stem=stem,
        )
        try:
            self._wizard.bind_state(self._state)
        except Exception:
            pass
        self.sync_params_to_state()
        parent = self._wizard.window() if self._wizard is not None else None
        self._modeling.start_shape(
            profile_path=prof,
            gnom_path=gnom,
            output_root=root,
            stem=stem,
            parent_widget=parent,
        )

    def refresh_shape_preview_from_disk(self) -> None:
        # Confirm updates session state; slim pane radios stay "none" until synced.
        mode = self._state.monodisperse_shape_mode
        mode_s = str(getattr(mode, "value", mode) or "none")
        self._wizard.shape_pane.set_shape_mode(mode_s)
        self._presenter.refresh_shape_view_for_current_mode()

    def _on_auto_toggle(self) -> None:
        if self._wizard.auto_processing_paused():
            self.resume_auto_processing_requested.emit()
        else:
            self.stop_auto_processing_requested.emit()

    def clear_views(self) -> None:
        self._presenter.clear_views()

    def ingest_skill_result(self, result: dict, *, skill_name: str = "") -> None:
        self._presenter.ingest_skill_result(result, skill_name=skill_name)
        if self._gnom_adjust is not None and self._gnom_adjust.isVisible():
            gnom_out = self._presenter.last_gnom_out
            if gnom_out:
                self._gnom_adjust.show_from_gnom_out(gnom_out)
            quality = getattr(self._presenter, "last_gnom_result", None) or {}
            if quality:
                self._gnom_adjust.set_passport_from_quality(quality)
        sn = (skill_name or result.get("skill_name") or "").strip()
        if sn == "fit_distances" or result.get("best_gnom_out_path"):
            self._push_gnom_to_shape_child()

    def _push_gnom_to_shape_child(self) -> None:
        if self._modeling is None:
            return
        root = self.output_root
        if root is None:
            root = Path(self._state.watchdir).expanduser().resolve()
        self._modeling.push_shape_context(
            profile_path=self.profile_path or "",
            gnom_path=self.gnom_out_for_dammif() or "",
            output_root=root,
            stem=getattr(self._presenter, "sample_stem", "") or "",
        )

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

    def apply_bundle(self, bundle: object) -> None:
        self._presenter.apply_bundle(bundle)

    def set_running(self, running: bool) -> None:
        if self._gnom_adjust is not None:
            self._gnom_adjust.set_running(bool(running))
        if self._guinier_adjust is not None:
            self._guinier_adjust.set_running(bool(running))

    def store_gnom_auto_snapshot(self, params: dict) -> None:
        self._config.store_gnom_auto_snapshot(params)

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

    @property
    def config_sync(self) -> MonodisperseConfigSync:
        return self._config
