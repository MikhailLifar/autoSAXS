from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple

from ..services.history.middle_from_stem import apply_middle_view_from_disk
from ..services.history.right_from_stem import apply_right_outputs_from_disk
from ..session.output_paths import tiff_history_label
from ..ingest.stability import FileStatSnapshot
from ..ingest.tiff_revision import TiffRevision, TiffRevisionSource, make_revision

if TYPE_CHECKING:
    from .controller import LiveviewController


class LiveviewHistoryHandler:
    def __init__(self, controller: LiveviewController) -> None:
        self._c = controller
        self._index: int = 0
        self._last_2d_shown: Optional[Tuple[str, FileStatSnapshot]] = None

    def reset_index(self) -> None:
        self._index = 0

    def clear_2d_cache(self) -> None:
        self._last_2d_shown = None

    def middle_updates_follow_pipeline(self) -> bool:
        hist = self._c.executor.session_processed_tiffs
        n = len(hist)
        if n == 0:
            return True
        return self._index == n - 1

    def reload_view(self) -> None:
        hist = list(self._c.executor.session_processed_tiffs)
        middle = self._c.middle
        if not hist or middle is None:
            return
        self._index = max(0, min(self._index, len(hist) - 1))
        tiff_path = hist[self._index]
        apply_middle_view_from_disk(
            middle,
            watchdir=self._c.watchdir,
            tiff_path=tiff_path,
            state=self._c.state,
            subtract_options=self._c.history.subtract_options(),
        )
        if tiff_path.lower().endswith((".tif", ".tiff")):
            self._record_2d_shown(tiff_path)
        self.refresh_right_outputs()

    def refresh_chrome(self) -> None:
        middle = self._c.middle
        if middle is None:
            return
        hist = list(self._c.executor.session_processed_tiffs)
        n = len(hist)
        if n == 0:
            middle.set_history_nav_visible(False)
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

    def on_session_file_completed(self) -> None:
        hist = self._c.executor.session_processed_tiffs
        n = len(hist)
        if n == 0:
            self.refresh_chrome()
            return
        was_at_previous_tail = n == 1 or self._index == n - 2
        if was_at_previous_tail:
            self._index = n - 1
        self.refresh_chrome()

    def step(self, delta: int) -> None:
        hist = list(self._c.executor.session_processed_tiffs)
        n = len(hist)
        middle = self._c.middle
        if n == 0 or delta == 0 or middle is None:
            return
        self._index = max(0, min(n - 1, self._index + int(delta)))
        self.refresh_chrome()
        tiff_path = hist[self._index]
        apply_middle_view_from_disk(
            middle,
            watchdir=self._c.watchdir,
            tiff_path=tiff_path,
            state=self._c.state,
            subtract_options=self._c.history.subtract_options(),
        )
        if tiff_path.lower().endswith((".tif", ".tiff")):
            self._record_2d_shown(tiff_path)
        self.refresh_right_outputs()

    def process_current_file(self) -> None:
        hist = list(self._c.executor.session_processed_tiffs)
        if not hist:
            return
        idx = max(0, min(self._index, len(hist) - 1))
        path = hist[idx]
        try:
            key = str(Path(path).resolve())
        except Exception:
            key = path.strip()
        if key:
            self._c.ingest.enqueue_manual_tiff(key)

    def on_tiff_revision_pending(self, revision: object) -> None:
        middle = self._c.middle
        if not isinstance(revision, TiffRevision) or middle is None:
            return
        if not self.middle_updates_follow_pipeline():
            return
        if self._last_2d_shown is not None:
            prev_path, prev_snap = self._last_2d_shown
            if prev_path == revision.path and prev_snap == revision.stat:
                return
        self._last_2d_shown = (revision.path, revision.stat)
        middle.show_image(revision.path)

    def refresh_right_outputs(self) -> None:
        right = self._c.right
        if right is None:
            return
        hist = list(self._c.executor.session_processed_tiffs)
        if not hist:
            right.sync_modeling_ui_to_session_state()
            return
        idx = max(0, min(self._index, len(hist) - 1))
        stem = Path(hist[idx]).stem
        apply_right_outputs_from_disk(
            right,
            watchdir=self._c.watchdir,
            tiff_stem=stem,
            monodisperse_armed=self._c.state.monodisperse_armed,
            polydisperse_armed=self._c.state.polydisperse_armed,
            tiff_path=hist[idx],
            watch_mode=self._c.state.watch_mode,
        )
        right.sync_modeling_ui_to_session_state()

    def _record_2d_shown(self, path: str) -> None:
        rev = make_revision(
            path=path,
            detected_at=time.monotonic(),
            source=TiffRevisionSource.MANUAL,
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
