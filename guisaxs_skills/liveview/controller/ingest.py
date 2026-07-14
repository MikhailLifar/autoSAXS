from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt5.QtWidgets import QMessageBox

from ...core.paths import runs_dir
from ..ingest.dir_tree_observer import TREE_STABILITY, TreeDirObserver, TreeObserverConfig
from ..pipeline import LiveviewJobExecutor
from ..ingest.poll_watcher import POLL_TRIGGERED_STABILITY, ProcessedTiffPoller, PollWatcherConfig
from ..session import load_liveview_session_settings, save_liveview_session_settings
from ..ingest.stability import StabilityConfig
from ..session.state import LiveviewWatchMode
from ..ingest.tiff_revision import TiffRevision, TiffRevisionSource, make_revision
from ..ingest.watcher import DirectoryWatcher, WatcherConfig
from ..session.workdir import save_last_watchdir

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

    def reconnect_idle_checks(self, executor: LiveviewJobExecutor) -> None:
        self._poll_watcher.set_idle_check(executor.is_idle)
        self._tree_observer.set_idle_check(executor.is_idle)
        executor.session_file_completed.connect(self._poll_watcher.track_processed_path)

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


class LiveviewWatchdirHandler:
    def __init__(self, controller: LiveviewController) -> None:
        self._c = controller

    def switch(self, new_p: Path) -> bool:
        if not self._c.require_idle(
            "Watch folder",
            "A skill is still running. Wait for it to finish, then change the watch folder.",
        ):
            return False
        self._c.persist_session_settings()
        self._c.runner.cancel()
        if not self._c.runner.wait_until_idle():
            parent = self._c.parent_widget
            if parent is not None:
                QMessageBox.warning(
                    parent,
                    "Watch folder",
                    "The running subprocess did not stop in time. Try again after it finishes.",
                )
            return False

        self._c.ingest.stop_all()
        try:
            self._c.executor.stop()
        except Exception:
            pass

        self._c.state.reset_for_new_watchdir(new_p)
        load_liveview_session_settings(self._c.state)
        if self._c.right is not None:
            self._c.right.reload_configs_from_watchdir()
        try:
            self._c.runner.set_workdir(new_p)
        except RuntimeError:
            parent = self._c.parent_widget
            if parent is not None:
                QMessageBox.warning(parent, "Watch folder", "Cannot switch while a skill is running.")
            return False

        runs_dir(self._c.state.watchdir).mkdir(parents=True, exist_ok=True)
        self._c.ingest._poll_watcher.clear()
        self._c.ingest._tree_observer.clear()
        try:
            self._c.executor.deleteLater()
        except Exception:
            pass
        self._c._executor = LiveviewJobExecutor(state=self._c.state, runner=self._c.runner)
        self._c._connect_executor(self._c._executor)
        self._c._executor.start()
        self._c._watchdir = new_p.resolve()
        save_last_watchdir(str(new_p))
        self._c.history.clear_2d_cache()
        self._c.history.reset_index()
        self._c.ingest.apply_watch_mode_watchers()
        self._c.ingest.reconnect_idle_checks(self._c.executor)
        if self._c.right is not None:
            self._c.right.clear_output_previews()
            self._c.right.sync_modeling_ui_to_session_state()
        self._c.session.apply_loaded_to_ui()
        self._c.history.refresh_chrome()
        return True
