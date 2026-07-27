from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ..ingest.dir_tree_observer import TREE_STABILITY, TreeDirObserver, TreeObserverConfig
from ..ingest.poll_watcher import POLL_TRIGGERED_STABILITY, ProcessedTiffPoller, PollWatcherConfig
from ..ingest.stability import StabilityConfig
from ..session.state import LiveviewWatchMode
from ..ingest.tiff_revision import TiffRevision, TiffRevisionSource, make_revision
from ..ingest.watcher import DirectoryWatcher, WatcherConfig

if TYPE_CHECKING:
    from .controller import LiveviewController


class LiveviewIngestHandler:
    """Watchers, watch mode, TIFF ingest, dropped files."""

    def __init__(self, controller: LiveviewController) -> None:
        self._c = controller
        wd = controller.watchdir
        self._watcher = DirectoryWatcher(
            directory=wd,
            cfg=WatcherConfig(recursive=False),
            on_revision=self._on_revision,
        )
        self._poll_watcher = ProcessedTiffPoller(
            cfg=PollWatcherConfig(),
            on_revision=self._on_revision_from_poll,
        )
        self._tree_observer = TreeDirObserver(
            cfg=TreeObserverConfig(),
            watchdir=wd,
            on_revision=self._on_revision_from_tree,
        )
        self._poll_watcher.set_idle_check(controller.executor.is_idle)
        self._tree_observer.set_idle_check(controller.executor.is_idle)
        controller.executor.session_file_completed.connect(self._poll_watcher.track_processed_path)
        self.apply_watch_mode_watchers()

    def stop_all(self) -> None:
        for stop in (self._watcher.stop, self._poll_watcher.stop, self._tree_observer.stop):
            try:
                stop()
            except Exception:
                pass

    def set_watch_mode(self, new_mode: LiveviewWatchMode) -> None:
        if new_mode == self._c.state.watch_mode:
            return
        if not self._c.require_idle(
            "Watch mode",
            "A skill is still running. Wait for it to finish, then switch watch mode.",
        ):
            return
        self._c.state.watch_mode = new_mode
        self._c.persist_session_settings()
        self.apply_watch_mode_watchers()
        self._c.history.refresh_chrome()
        if self._c.executor.session_processed_tiffs:
            self._c.history.reload_view()

    def apply_watch_mode_watchers(self) -> None:
        wd = self._c.watchdir
        if self._c.state.watch_mode == LiveviewWatchMode.TREE:
            try:
                self._watcher.stop()
            except Exception:
                pass
            try:
                self._poll_watcher.stop()
            except Exception:
                pass
            self._tree_observer.restart_at(wd)
        else:
            try:
                self._tree_observer.stop()
            except Exception:
                pass
            self._tree_observer.clear()
            try:
                self._watcher.restart_at(wd)
            except Exception:
                self._watcher.start()
            self._poll_watcher.start()

    def enqueue_manual_tiff(self, path: str) -> None:
        rev = make_revision(
            path=path,
            detected_at=time.monotonic(),
            source=TiffRevisionSource.MANUAL,
        )
        if rev is not None:
            self._enqueue_revision(rev)

    def ingest_dropped_tiffs(self, paths: list[str]) -> None:
        wd = self._c.watchdir
        for raw in paths:
            p = Path(raw)
            if not p.is_file():
                continue
            src_r = p.resolve()
            if self._path_under_watchdir(src_r):
                self.enqueue_manual_tiff(str(src_r))
                continue
            dest = wd / src_r.name
            shutil.copy2(src_r, dest)
            self.enqueue_manual_tiff(str(dest))

    def _path_under_watchdir(self, path: Path) -> bool:
        try:
            path.resolve().relative_to(self._c.watchdir)
            return True
        except ValueError:
            return False

    def _enqueue_revision(self, revision: TiffRevision, *, stability_cfg: StabilityConfig | None = None) -> None:
        self._c.executor.enqueue_revision(revision, stability_cfg=stability_cfg)

    def _on_revision(self, revision: TiffRevision, *, stability_cfg: object = None) -> None:
        cfg = stability_cfg if isinstance(stability_cfg, StabilityConfig) else None
        self._enqueue_revision(revision, stability_cfg=cfg)

    def _on_revision_from_poll(self, revision: TiffRevision) -> None:
        self._enqueue_revision(revision, stability_cfg=POLL_TRIGGERED_STABILITY)

    def _on_revision_from_tree(self, revision: TiffRevision) -> None:
        self._enqueue_revision(revision, stability_cfg=TREE_STABILITY)