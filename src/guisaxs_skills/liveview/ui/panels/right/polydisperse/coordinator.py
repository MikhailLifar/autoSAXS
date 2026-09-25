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

    def __init__(
        self,
        *,
        state: LiveviewSessionState,
        window: Any,
        modeling: Any = None,
    ) -> None:
        super().__init__()
        self._state = state
        self._window = window
        self._modeling = modeling
        self._config = PolydisperseConfigSync(state=state, window=window)
        self._presenter = PolydisperseArtifactPresenter(state=state, window=window)
        self._sizes_adjust = None
        self._guinier_adjust = None
        self._connect_window()

    def _connect_window(self) -> None:
        w = self._window
        w.guinier_pane.adjust_requested.connect(self.open_guinier_adjust_wizard)
        w.sizes_pane.adjust_requested.connect(self.open_sizes_adjust_wizard)
        w.mixture_pane.mode_changed.connect(self._on_mixture_mode_changed)
        w.mixture_pane.params_changed.connect(self._on_mixture_params_changed)
        w.mixture_pane.rerun_mixture_requested.connect(self._on_rerun_mixture)
        w.mixture_pane.start_modeling_requested.connect(self._on_start_modeling)
        w.auto_toggle_clicked.connect(self._on_auto_toggle)
        router = getattr(w, "_plot_clicks", None)
        if router is not None and hasattr(router, "set_sizes_open_handler"):
            router.set_sizes_open_handler(self.open_sizes_adjust_wizard)
        if router is not None and hasattr(router, "set_guinier_open_handler"):
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
            self._modeling.push_dr_context(
                profile_path=profile_path,
                output_root=output_root,
                stem=stem or getattr(self._presenter, "sample_stem", "") or "",
                sample_id=sample_id or tiff_path or "",
            )

    def sync_params_to_state(self) -> None:
        self._config.sync_params_to_state()

    def open_guinier_adjust_wizard(self) -> None:
        from ....wizards.guinier_adjust import GuinierAdjustWizardDialog

        if self._guinier_adjust is None:
            parent = self._window.window() if self._window is not None else None
            self._guinier_adjust = GuinierAdjustWizardDialog(parent)
            self._guinier_adjust.editing_started.connect(self.intervention_requested.emit)
            self._guinier_adjust.params_changed.connect(self._on_guinier_adjust_params_changed)
        prof = self._presenter.profile_path
        wp = dict(self._state.polydisperse_window_params or {})
        auto = dict(wp.get("guinier_auto") or {})
        working: dict[str, Any] = {}
        if wp.get("guinier_first") is not None and wp.get("guinier_last") is not None:
            working["first"] = int(wp["guinier_first"])
            working["last"] = int(wp["guinier_last"])
        elif auto.get("first") is not None and auto.get("last") is not None:
            working["first"] = int(auto["first"])
            working["last"] = int(auto["last"])
        else:
            try:
                gf, gl = self._window.guinier_pane.first_last()
                if gf is not None and gl is not None:
                    working["first"] = int(gf)
                    working["last"] = int(gl)
            except Exception:
                pass
        results = str(getattr(self._presenter, "_last_guinier_results", "") or "")
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
            for k in (
                "first",
                "last",
                "q_min",
                "q_max",
                "rmin_nm",
                "rmax_nm",
                "alpha",
                "force_zero_rmin",
                "force_zero_rmax",
            )
            if k in wp
        }
        gnom_out = self._presenter.last_gnom_out
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
            gnom_out_path=gnom_out,
            passport_html=passport_html if passport_html and passport_html != "—" else "",
        )
        self._sizes_adjust.show()
        self._sizes_adjust.raise_()
        self._sizes_adjust.activateWindow()

    def _on_guinier_adjust_params_changed(self) -> None:
        if self._guinier_adjust is not None:
            p = self._guinier_adjust.guinier_params()
            self._config.store_guinier_interval(int(p["first"]), int(p["last"]))
            try:
                self._window.guinier_pane.set_range(int(p["first"]), int(p["last"]))
            except Exception:
                pass
        self.sync_params_to_state()
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

    def _on_start_modeling(self) -> None:
        if self._modeling is None:
            return
        prof = self.profile_path or ""
        root = self.output_root
        if root is None:
            root = Path(self._state.watchdir).expanduser().resolve()
        stem = getattr(self._presenter, "sample_stem", "") or ""
        self._modeling.build_dr_context(profile_path=prof, output_root=root, stem=stem)
        try:
            self._window.bind_state(self._state)
        except Exception:
            pass
        self.sync_params_to_state()
        parent = self._window.window() if self._window is not None else None
        self._modeling.start_dr(
            profile_path=prof,
            output_root=root,
            stem=stem,
            sample_id=prof or stem,
            parent_widget=parent,
        )
        self.refresh_mixture_preview_from_disk()

    def refresh_mixture_preview_from_disk(self) -> None:
        # Session mode is SSOT after Confirm; promote from disk when still "none".
        from .....session.state import PolydisperseMixtureMode

        mode = self._state.polydisperse_mixture_mode
        mode_s = str(getattr(mode, "value", mode) or "none")
        self._window.mixture_pane.set_mixture_mode(mode_s)
        root = self.output_root
        if root is None:
            return
        from guisaxs_skills.modeling.run_params import (
            apply_disk_params_to_session_state,
            resolve_sample_modeling_dir,
        )
        from .....session.output_paths import mixture_dir

        md = resolve_sample_modeling_dir(
            mixture_dir(root),
            profile_path=self.profile_path or "",
            stem=getattr(self._presenter, "sample_stem", "") or "",
        )
        apply_disk_params_to_session_state(
            self._state,
            output_dir=md,
            profile_path=self.profile_path or "",
            stem=getattr(self._presenter, "sample_stem", "") or "",
        )
        try:
            self._window.bind_state(self._state)
        except Exception:
            pass
        csv = md / "mixture_results.csv"
        if not csv.is_file():
            return
        if mode_s == "none":
            self._state.polydisperse_mixture_mode = PolydisperseMixtureMode.MIXTURE
            self._window.mixture_pane.set_mixture_mode("mixture")
        self._presenter.ingest_skill_result(
            {"results_csv_path": str(csv), "output_subdir": str(md)},
            skill_name="model_mixture",
        )

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
        if self._guinier_adjust is not None:
            self._guinier_adjust.set_running(bool(running))

    @property
    def profile_path(self) -> str:
        return self._presenter.profile_path

    @property
    def output_root(self) -> Optional[Path]:
        return self._presenter.output_root

    @property
    def config_sync(self) -> PolydisperseConfigSync:
        return self._config
