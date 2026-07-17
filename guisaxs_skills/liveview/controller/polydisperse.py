from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from PyQt5.QtWidgets import QMessageBox

from ..pipeline.polydisperse_pipeline import PolydispersePipelineParts, build_polydisperse_steps
from ..session.output_paths import tiff_output_root
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
        self._c.processing_mode.resume()

    def _profile_root_and_tiff(self) -> tuple[Optional[str], Optional[Path], str]:
        prof = self._c.state.default_fit_distances_profile_path()
        if prof is None or not prof.is_file():
            return None, None, ""
        hist = list(self._c.executor.session_processed_tiffs)
        tiff_path = ""
        if hist:
            idx = max(0, min(self._c.history._index, len(hist) - 1))
            tiff_path = hist[idx]
        root = tiff_output_root(
            watchdir=self._c.state.watchdir,
            tiff_path=tiff_path,
            mode=self._c.state.watch_mode,
        )
        right = self._c.right
        if right is not None:
            right.polydisperse_coordinator.set_context(
                profile_path=str(prof.resolve()),
                output_root=root,
                tiff_path=tiff_path,
                watch_mode=self._c.state.watch_mode,
            )
        return str(prof.resolve()), root, tiff_path

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
            root = tiff_output_root(
                watchdir=self._c.state.watchdir,
                tiff_path=tiff_path,
                mode=self._c.state.watch_mode,
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
            root = tiff_output_root(
                watchdir=self._c.state.watchdir,
                tiff_path=tiff_path,
                mode=self._c.state.watch_mode,
            )
        job = self._c.executor.current_job_output_root
        if job is not None:
            root = job
        if not prof:
            p = self._c.state.default_fit_distances_profile_path()
            if p is not None and p.is_file():
                prof = str(p.resolve())
        if prof:
            right.polydisperse_coordinator.set_context(
                profile_path=prof,
                output_root=root,
                tiff_path=tiff_path,
                watch_mode=self._c.state.watch_mode,
            )
