from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer


def _is_tif(path: str) -> bool:
    p = path.lower()
    return p.endswith(".tif") or p.endswith(".tiff")


@dataclass(frozen=True)
class WatcherConfig:
    recursive: bool = False


class _Handler(FileSystemEventHandler):
    def __init__(self, *, on_new_file: Callable[[str, float], None], started_at: float) -> None:
        super().__init__()
        self._on_new_file = on_new_file
        self._started_at = started_at

    def on_created(self, event) -> None:  # type: ignore[override]
        try:
            if getattr(event, "is_directory", False):
                return
            src = getattr(event, "src_path", "")
            if not src or not _is_tif(src):
                return
            # Best-effort: ignore pre-existing files (watcher started after they existed).
            try:
                mtime = os.path.getmtime(src)
                if mtime < self._started_at:
                    return
            except Exception:
                pass
            self._on_new_file(src, time.monotonic())
        except Exception:
            return

    def on_moved(self, event) -> None:  # type: ignore[override]
        # Treat atomic moves into directory as new arrivals.
        try:
            if getattr(event, "is_directory", False):
                return
            dest = getattr(event, "dest_path", "")
            if not dest or not _is_tif(dest):
                return
            self._on_new_file(dest, time.monotonic())
        except Exception:
            return


class DirectoryWatcher:
    def __init__(
        self,
        *,
        directory: Path,
        cfg: WatcherConfig,
        on_new_file: Callable[[str, float], None],
    ) -> None:
        self._directory = directory
        self._cfg = cfg
        self._on_new_file = on_new_file
        self._observer: Optional[Observer] = None
        self._lock = threading.Lock()
        self._started_at_epoch = time.time()

    def start(self) -> None:
        with self._lock:
            if self._observer is not None:
                return
            handler = _Handler(on_new_file=self._on_new_file, started_at=self._started_at_epoch)
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

