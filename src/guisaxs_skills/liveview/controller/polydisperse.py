from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt5.QtWidgets import QMessageBox

from ..pipeline.polydisperse_pipeline import PolydispersePipelineParts, build_polydisperse_steps
from ..ingest.sample_revision import is_dat_path
from ..session.output_paths import analysis_output_root
from ..session.state import PolydisperseMixtureMode
from ...logic.runner_qprocess import RunOutcome

if TYPE_CHECKING:
    from .controller import LiveviewController


class LiveviewPolydisperseHandler:
    def __init__(self, controller: LiveviewController) -> None:
        self._c = controller

    def refresh_queue_ui(self) -> None:
        self._c.processing_mode.sync_ui()

    def on_intervention(self) -> None:
        self._c.processing_mode.stop()
        self._c.executor.cancel_current()

    def on_mixture_config_changed(self) -> None:
        """Mixture mode: configuration only — no pause."""
        right = self._c.right
        if right is not None:
            right.polydisperse_coordinator.sync_params_to_state()
        self.refresh_queue_ui()

    def on_stop_queue(self) -> None:
        self.on_intervention()

    def on_resume_queue(self) -> None:
        self._c.enqueue_report_for_current_sample()
        self._c.processing_mode.resume()

    def _current_sample_path(self) -> str:
        cur = self._c.samples.current()
        return cur.path if cur is not None else ""

    def _resolve_profile_path(self) -> Optional[str]:
        right = self._c.right
        if right is not None:
            cand = (right.polydisperse_coordinator.profile_path or "").strip()
            if cand and os.path.isfile(cand):
                return str(Path(cand).expanduser().resolve())

        sample = self._current_sample_path()
        if sample and is_dat_path(sample) and os.path.isfile(sample):
            return str(Path(sample).expanduser().resolve())

        boarding = self._c.samples.boarding_for(sample) if sample else None
        p = self._c.state.preferred_profile_path(boarding=boarding)
        if p is not None and p.is_file():
            return str(p.resolve())
        return None

    def _profile_root_and_tiff(self) -> tuple[Optional[str], Optional[Path], str]:
        sample_path = self._current_sample_path()
        prof = self._resolve_profile_path()
        if not prof:
            return None, None, sample_path

        right = self._c.right
        root: Optional[Path] = None
        if right is not None and right.polydisperse_coordinator.output_root is not None:
            root = right.polydisperse_coordinator.output_root
        if root is None:
            boarding = self._c.samples.boarding_for(sample_path or prof)
            root = analysis_output_root(
                watchdir=self._c.state.watchdir,
                sample_path=sample_path or prof,
                mode=self._c.state.watch_mode,
                boarding=boarding,
            )
        tiff_path = sample_path if sample_path and not is_dat_path(sample_path) else ""
        if right is not None:
            right.polydisperse_coordinator.set_context(
                profile_path=prof,
                output_root=root,
                tiff_path=tiff_path or sample_path,
                watch_mode=self._c.state.watch_mode,
            )
        return prof, root, tiff_path or sample_path

    def _profile_and_root(self) -> tuple[Optional[str], Optional[Path]]:
        prof, root, _tp = self._profile_root_and_tiff()
        return prof, root

    def on_guinier_rerun(self) -> None:
        prof, root = self._profile_and_root()
        if not prof or root is None:
            return
        right = self._c.right
        first_i: Optional[int] = None
        last_i: Optional[int] = None
        if right is not None:
            right.polydisperse_coordinator.sync_params_to_state()
            first_i, last_i = right.polydisperse_window.guinier_pane.first_last()
        fixed = first_i is not None and last_i is not None
        steps = build_polydisperse_steps(
            prof,
            output_root=root,
            state=self._c.state,
            parts=PolydispersePipelineParts.GUINIER_ONLY,
            load_yaml=self._c.executor._load_yaml_options,
            fixed_guinier_interval=fixed,
            guinier_interval_first=first_i,
            guinier_interval_last=last_i,
        )
        if not fixed:
            parent = self._c.parent_widget
            if parent is not None:
                QMessageBox.warning(
                    parent,
                    "Polydisperse",
                    "Guinier interval is incomplete (need both first and last point indices).",
                )
            return
        g_opts = steps[0].request.options if steps else {}
        if g_opts.get("first") is None or g_opts.get("last") is None:
            parent = self._c.parent_widget
            if parent is not None:
                QMessageBox.warning(
                    parent,
                    "Polydisperse",
                    "Guinier interval is incomplete (need both first and last point indices).",
                )
            return
        job = self._c.executor.build_polydisperse_manual_job(
            profile_abs=prof, steps=steps, output_root=root
        )
        self._c.executor.enqueue_job(job)

    def on_sizes_rerun(self) -> None:
        prof, root = self._profile_and_root()
        if not prof or root is None:
            return
        right = self._c.right
        if right is not None:
            right.polydisperse_coordinator.sync_params_to_state()
        steps = build_polydisperse_steps(
            prof,
            output_root=root,
            state=self._c.state,
            parts=PolydispersePipelineParts.SIZES_ONLY,
            load_yaml=self._c.executor._load_yaml_options,
        )
        job = self._c.executor.build_polydisperse_manual_job(
            profile_abs=prof, steps=steps, output_root=root
        )
        self._c.executor.enqueue_job(job)

    def on_mixture_rerun(self) -> None:
        prof, root = self._profile_and_root()
        if not prof or root is None:
            return
        if self._c.state.polydisperse_mixture_mode == PolydisperseMixtureMode.NONE:
            return
        right = self._c.right
        if right is not None:
            right.polydisperse_coordinator.sync_params_to_state()
        steps = build_polydisperse_steps(
            prof,
            output_root=root,
            state=self._c.state,
            parts=PolydispersePipelineParts.MIXTURE_ONLY,
            load_yaml=self._c.executor._load_yaml_options,
        )
        if not steps:
            return
        job = self._c.executor.build_polydisperse_manual_job(
            profile_abs=prof, steps=steps, output_root=root
        )
        self._c.executor.enqueue_job(job)

    def update_profile_from_artifacts(self, result: dict) -> None:
        if not self._c.state.polydisperse_armed:
            return
        sub = result.get("subtracted_1d")
        integ = result.get("integrated_1d")
        path = ""
        if isinstance(sub, str) and sub.strip() and os.path.isfile(sub):
            path = sub.strip()
        elif isinstance(sub, list) and sub and isinstance(sub[-1], str):
            path = sub[-1].strip()
        if not path:
            if isinstance(integ, str) and integ.strip():
                path = integ.strip()
            elif isinstance(integ, list) and integ and isinstance(integ[-1], str):
                path = integ[-1].strip()
        if not path:
            return
        right = self._c.right
        if right is None:
            return
        _, root, tiff_path = self._profile_root_and_tiff()
        if root is None:
            root = analysis_output_root(
                watchdir=self._c.state.watchdir,
                sample_path=tiff_path or path,
                mode=self._c.state.watch_mode,
                boarding=self._c.samples.boarding_for(tiff_path or path),
            )
        right.polydisperse_coordinator.set_context(
            profile_path=path,
            output_root=root,
            tiff_path=tiff_path,
            watch_mode=self._c.state.watch_mode,
        )

    def sync_window_context_before_ingest(self, outcome: RunOutcome) -> None:
        if not self._c.state.polydisperse_armed:
            return
        right = self._c.right
        if right is None or outcome.request is None:
            return
        prof = ""
        if outcome.request.positional:
            try:
                p = Path(outcome.request.positional[0]).expanduser().resolve()
                if p.is_file():
                    prof = str(p)
            except (OSError, TypeError, ValueError):
                pass
        _, root, tiff_path = self._profile_root_and_tiff()
        if root is None:
            root = analysis_output_root(
                watchdir=self._c.state.watchdir,
                sample_path=tiff_path or prof,
                mode=self._c.state.watch_mode,
                boarding=self._c.samples.boarding_for(tiff_path or prof),
            )
        job = self._c.executor.current_job_output_root
        if job is not None:
            root = job
        if not prof:
            resolved = self._resolve_profile_path()
            if resolved:
                prof = resolved
        if prof:
            right.polydisperse_coordinator.set_context(
                profile_path=prof,
                output_root=root,
                tiff_path=tiff_path,
                watch_mode=self._c.state.watch_mode,
            )
