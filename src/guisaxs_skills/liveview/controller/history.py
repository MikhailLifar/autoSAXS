from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from ..services.history.middle_from_stem import sync_middle_view
from ..services.history.right_from_stem import apply_right_outputs_from_disk
from ..session.output_paths import tiff_history_label
from ..ingest.stability import FileStatSnapshot
from ..ingest.sample_revision import SampleRevision, SampleRevisionSource, make_revision

if TYPE_CHECKING:
    from .controller import LiveviewController


class LiveviewHistoryHandler:
    def __init__(self, controller: LiveviewController) -> None:
        self._c = controller
        self._last_2d_shown: Optional[Tuple[str, FileStatSnapshot]] = None

    @property
    def _index(self) -> int:
        return self._c.samples.index

    @_index.setter
    def _index(self, value: int) -> None:
        self._c.samples.index = int(value)

    def reset_index(self) -> None:
        self._c.samples.index = 0

    def clear_2d_cache(self) -> None:
        self._last_2d_shown = None

    def middle_updates_follow_pipeline(self) -> bool:
        n = len(self._c.samples)
        if n == 0:
            return True
        return self._index == n - 1

    def refresh_chrome(self) -> None:
        middle = self._c.middle
        if middle is None:
            return
        hist = list(self._c.samples.paths())
        n = len(hist)
        if n == 0:
            middle.set_history_nav_visible(False)
            left = self._c.left
            if left is not None:
                left.refresh_attention_coach()
            return
        middle.set_history_nav_visible(True)
        if self._index >= n:
            self._index = n - 1
        if self._index < 0:
            self._index = 0
        name = tiff_history_label(
            watchdir=self._c.watchdir,
            tiff_path=hist[self._index],
            mode=self._c.state.watch_mode,
        )
        middle.set_history_label(f"{self._index + 1} / {n} · {name}")
        middle.set_history_prev_enabled(self._index > 0)
        middle.set_history_next_enabled(self._index < n - 1)
        middle.set_process_enabled(True)
        left = self._c.left
        if left is not None:
            left.refresh_attention_coach()

    def on_session_file_completed(self, _path: str = "") -> None:
        """
        Refresh chrome after a successful job.

        ``SampleStore.append_history`` moves the index to the new tail only when
        the path is newly appended. Deduped re-completions must not bump the
        index (avoids off-by-one jumps while browsing).
        """
        hist = list(self._c.samples.paths())
        if not hist:
            self.refresh_chrome()
            self._c.persist_history()
            return
        self.refresh_chrome()
        # Same disk path as history < / > — curve jobs never emit integrate/subtract keys.
        if self.middle_updates_follow_pipeline():
            self.refresh_middle_for_sample(hist[self._index])
        self._c.persist_history()

    def on_pipeline_job_started(self, sample_path: str) -> None:
        """Show middle views for the boarded sample as soon as the job starts."""
        self.refresh_middle_for_sample(sample_path)

    def sync_middle(self, *, sample: Any = None, force: bool = False) -> None:
        """Sole controller entry for middle layout + content."""
        middle = self._c.middle
        if middle is None:
            return
        if sample is None:
            sample = self._c.samples.current()
        sync_middle_view(
            middle,
            state=self._c.state,
            sample=sample,
            subtract_options=self.subtract_options(),
            force=force,
        )

    def refresh_middle_for_sample(self, sample_path: str) -> None:
        """Middle content for a sample path (pipeline / job start)."""
        middle = self._c.middle
        path = (sample_path or "").strip()
        if middle is None or not path:
            return
        if not self.middle_updates_follow_pipeline():
            return
        sample = self._c.samples.get(path)
        if sample is None:
            boarding = self._c.samples.boarding_for(path) or self._c.state.intake_mode
            from ..session.sample import Sample

            sample = Sample.from_path(path, boarding=boarding)
        self.sync_middle(sample=sample, force=True)
        if sample.path.lower().endswith((".tif", ".tiff")):
            self._record_2d_shown(sample.path)

    def process_current_file(self) -> None:
        hist = list(self._c.samples.paths())
        if not hist:
            return
        idx = max(0, min(self._index, len(hist) - 1))
        path = hist[idx]
        try:
            key = str(Path(path).resolve())
        except Exception:
            key = path.strip()
        if not key:
            return
        sample = self._c.samples.get(key) or self._c.samples.at(idx)
        boarding = sample.boarding if sample is not None else self._c.samples.boarding_for(key)
        self._c.ingest.enqueue_manual_sample(key, boarding=boarding)

    def on_sample_revision_pending(self, revision: object) -> None:
        if not isinstance(revision, SampleRevision):
            return
        if not self.middle_updates_follow_pipeline():
            return
        path = revision.path
        if path.lower().endswith((".tif", ".tiff")):
            if self._last_2d_shown is not None:
                prev_path, prev_snap = self._last_2d_shown
                if prev_path == revision.path and prev_snap == revision.stat:
                    return
            self._last_2d_shown = (revision.path, revision.stat)
            boarding = self._c.samples.boarding_for(path) or self._c.state.intake_mode
            from ..session.sample import Sample

            sample = self._c.samples.get(path) or Sample.from_path(path, boarding=boarding)
            self.sync_middle(sample=sample, force=True)

    def reload_view(self) -> None:
        hist = list(self._c.samples.paths())
        if self._c.middle is None:
            return
        if hist:
            self._index = max(0, min(self._index, len(hist) - 1))
        self.sync_middle(force=True)
        sample = self._c.samples.current()
        if sample is not None and sample.path.lower().endswith((".tif", ".tiff")):
            self._record_2d_shown(sample.path)
        self.refresh_right_outputs()

    def step(self, delta: int) -> None:
        hist = list(self._c.samples.paths())
        n = len(hist)
        if n == 0 or delta == 0 or self._c.middle is None:
            return
        self._index = max(0, min(n - 1, self._index + int(delta)))
        self.refresh_chrome()
        self.sync_middle(force=True)
        sample = self._c.samples.current()
        if sample is not None and sample.path.lower().endswith((".tif", ".tiff")):
            self._record_2d_shown(sample.path)
        self.refresh_right_outputs()
        self._c.persist_history()
    def refresh_right_outputs(self) -> None:
        right = self._c.right
        if right is None:
            return
        hist = list(self._c.samples.paths())
        if not hist:
            right.sync_modeling_ui_to_session_state()
            return
        idx = max(0, min(self._index, len(hist) - 1))
        from ..ingest.sample_revision import sample_stem_from_path

        sample = self._c.samples.at(idx)
        path = sample.path if sample is not None else hist[idx]
        stem = sample.stem if sample is not None else sample_stem_from_path(path)
        apply_right_outputs_from_disk(
            right,
            watchdir=self._c.watchdir,
            tiff_stem=stem,
            monodisperse_armed=self._c.state.monodisperse_armed,
            polydisperse_armed=self._c.state.polydisperse_armed,
            tiff_path=path,
            watch_mode=self._c.state.watch_mode,
            state=self._c.state,
            session=self._c.session,
        )
        right.sync_modeling_ui_to_session_state()

    def _record_2d_shown(self, path: str) -> None:
        rev = make_revision(
            path=path,
            detected_at=time.monotonic(),
            source=SampleRevisionSource.MANUAL,
        )
        if rev is not None:
            self._last_2d_shown = (rev.path, rev.stat)

    def subtract_options(self) -> Dict[str, Any]:
        try:
            data = self._c.state.subtract_options
            if isinstance(data, dict):
                return {str(k): v for k, v in data.items()}
        except Exception:
            pass
        return {}
