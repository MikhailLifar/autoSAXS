from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .stability import FileStatSnapshot, _try_stat
from .tiff_revision import (
    TiffRevision,
    TiffRevisionSource,
    is_tiff_path,
    make_revision,
    normalize_tiff_path,
)


@dataclass(frozen=True)
class WatcherConfig:
    recursive: bool = False


def _baseline_known_tiffs(directory: Path, *, recursive: bool) -> Dict[str, FileStatSnapshot]:
    """Snapshot existing TIFFs so startup noise is ignored without an mtime gate."""
    known: Dict[str, FileStatSnapshot] = {}
    patterns = ("*.tif", "*.tiff")
    try:
        root = directory.expanduser().resolve()
    except OSError:
        return known
    if not root.is_dir():
        return known
    iterator = root.rglob if recursive else root.glob
    for pattern in patterns:
        try:
            entries = list(iterator(pattern))
        except OSError:
            continue
        for f in entries:
            try:
                if not f.is_file():
                    continue
                key = normalize_tiff_path(str(f))
                snap = _try_stat(key)
                if snap is not None:
                    known[key] = snap
            except OSError:
                continue
    return known


class _Handler(FileSystemEventHandler):
    def __init__(
        self,
        *,
        on_revision: Callable[[TiffRevision], None],
        known: Dict[str, FileStatSnapshot],
        lock: threading.Lock,
    ) -> None:
        super().__init__()
        self._on_revision = on_revision
        self._known = known
        self._lock = lock

    def _forget(self, path: str) -> None:
        raw = (path or "").strip()
        if not raw or not is_tiff_path(raw):
            return
        key = normalize_tiff_path(raw)
        with self._lock:
            self._known.pop(key, None)

    def _maybe_notify(self, path: str) -> None:
        rev = make_revision(
            path=path,
            detected_at=time.monotonic(),
            source=TiffRevisionSource.INOTIFY,
        )
        if rev is None:
            return
        with self._lock:
            prev = self._known.get(rev.path)
            if prev is not None and prev == rev.stat:
                return
            self._known[rev.path] = rev.stat
        self._on_revision(rev)

    def on_created(self, event) -> None:  # type: ignore[override]
        try:
            if getattr(event, "is_directory", False):
                return
            self._maybe_notify(getattr(event, "src_path", ""))
        except Exception:
            return

    def on_modified(self, event) -> None:  # type: ignore[override]
        try:
            if getattr(event, "is_directory", False):
                return
            self._maybe_notify(getattr(event, "src_path", ""))
        except Exception:
            return

    def on_moved(self, event) -> None:  # type: ignore[override]
        try:
            if getattr(event, "is_directory", False):
                return
            self._forget(getattr(event, "src_path", ""))
            self._maybe_notify(getattr(event, "dest_path", ""))
        except Exception:
            return

    def on_deleted(self, event) -> None:  # type: ignore[override]
        try:
            if getattr(event, "is_directory", False):
                return
            self._forget(getattr(event, "src_path", ""))
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
        self._known: Dict[str, FileStatSnapshot] = {}

    def start(self) -> None:
        with self._lock:
            if self._observer is not None:
                return
            self._known = _baseline_known_tiffs(
                self._directory,
                recursive=bool(self._cfg.recursive),
            )
            handler = _Handler(
                on_revision=self._on_revision,
                known=self._known,
                lock=self._lock,
            )
            obs = Observer()
            # Claim the slot before Observer.start() so concurrent start() is a no-op,
            # but release the lock first so inotify callbacks never deadlock on _known.
            self._observer = obs
        try:
            obs.schedule(handler, str(self._directory), recursive=bool(self._cfg.recursive))
            obs.start()
        except Exception:
            with self._lock:
                if self._observer is obs:
                    self._observer = None
            raise

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
        """Stop (if running), point at ``directory``, reset known baseline, and watch again."""
        self.stop()
        self._directory = directory.expanduser().resolve()
        self.start()
