from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .tiff_revision import TiffRevision, TiffRevisionSource, make_revision


@dataclass(frozen=True)
class WatcherConfig:
    recursive: bool = False


class _Handler(FileSystemEventHandler):
    def __init__(self, *, on_revision: Callable[[TiffRevision], None], started_at: float) -> None:
        super().__init__()
        self._on_revision = on_revision
        self._started_at = started_at

    def _notify_tif(self, path: str, *, require_mtime_after_start: bool) -> None:
        if require_mtime_after_start:
            try:
                if os.path.getmtime(path) < self._started_at:
                    return
            except Exception:
                return
        rev = make_revision(
            path=path,
            detected_at=time.monotonic(),
            source=TiffRevisionSource.INOTIFY,
        )
        if rev is not None:
            self._on_revision(rev)

    def on_created(self, event) -> None:  # type: ignore[override]
        try:
            if getattr(event, "is_directory", False):
                return
            self._notify_tif(getattr(event, "src_path", ""), require_mtime_after_start=True)
        except Exception:
            return

    def on_modified(self, event) -> None:  # type: ignore[override]
        try:
            if getattr(event, "is_directory", False):
                return
            self._notify_tif(getattr(event, "src_path", ""), require_mtime_after_start=True)
        except Exception:
            return

    def on_moved(self, event) -> None:  # type: ignore[override]
        try:
            if getattr(event, "is_directory", False):
                return
            self._notify_tif(getattr(event, "dest_path", ""), require_mtime_after_start=False)
        except Exception:
            return


class DirectoryWatcher:
    def __init__(
        self,
        *,
        directory: Path,
        cfg: WatcherConfig,
        on_revision: Callable[[TiffRevision], None],
    ) -> None:
        self._directory = directory
        self._cfg = cfg
        self._on_revision = on_revision
        self._observer: Optional[Observer] = None
        self._lock = threading.Lock()
        self._started_at_epoch = time.time()

    def start(self) -> None:
        with self._lock:
            if self._observer is not None:
                return
            handler = _Handler(on_revision=self._on_revision, started_at=self._started_at_epoch)
            obs = Observer()
            obs.schedule(handler, str(self._directory), recursive=bool(self._cfg.recursive))
            obs.start()
            self._observer = obs

    def stop(self) -> None:
        with self._lock:
            obs = self._observer
            self._observer = None
        if obs is None:
            return
        try:
            obs.stop()
        except Exception:
            pass
        try:
            obs.join(timeout=2.0)
        except Exception:
            pass

    def restart_at(self, directory: Path) -> None:
        """Stop (if running), point at ``directory``, reset “new file” baseline, and watch again."""
        self.stop()
        self._directory = directory.expanduser().resolve()
        self._started_at_epoch = time.time()
        self.start()
