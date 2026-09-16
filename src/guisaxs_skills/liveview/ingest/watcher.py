from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Tuple

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from .stability import FileStatSnapshot, _try_stat
from .sample_revision import (
    SampleRevision,
    SampleRevisionSource,
    is_sample_dat_path,
    is_tiff_path,
    make_revision,
    normalize_sample_path,
)


@dataclass(frozen=True)
class WatcherConfig:
    recursive: bool = False
    patterns: Tuple[str, ...] = ("*.tif", "*.tiff")
    allow_dat: bool = False
    # Optional extra filter after extension match (e.g. only root + averaged/).
    path_ok: Optional[Callable[[str], bool]] = None


def _path_matches_watch(path: str, *, allow_dat: bool) -> bool:
    if is_tiff_path(path):
        return not allow_dat  # TIFF watcher: tiffs only; dat watcher: dats only
    if allow_dat and is_sample_dat_path(path):
        return True
    return False


def _baseline_known_files(
    directory: Path,
    *,
    recursive: bool,
    patterns: Sequence[str],
) -> Dict[str, FileStatSnapshot]:
    """Snapshot existing matching files so startup noise is ignored without an mtime gate."""
    known: Dict[str, FileStatSnapshot] = {}
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
                key = normalize_sample_path(str(f))
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
        on_revision: Callable[[SampleRevision], None],
        known: Dict[str, FileStatSnapshot],
        lock: threading.Lock,
        allow_dat: bool,
        path_ok: Optional[Callable[[str], bool]] = None,
    ) -> None:
        super().__init__()
        self._on_revision = on_revision
        self._known = known
        self._lock = lock
        self._allow_dat = bool(allow_dat)
        self._path_ok = path_ok

    def _forget(self, path: str) -> None:
        raw = (path or "").strip()
        if not raw or not _path_matches_watch(raw, allow_dat=self._allow_dat):
            return
        key = normalize_sample_path(raw)
        with self._lock:
            self._known.pop(key, None)

    def _maybe_notify(self, path: str) -> None:
        rev = make_revision(
            path=path,
            detected_at=time.monotonic(),
            source=SampleRevisionSource.INOTIFY,
            allow_dat=self._allow_dat,
        )
        if rev is None:
            return
        if not _path_matches_watch(rev.path, allow_dat=self._allow_dat):
            return
        if self._path_ok is not None and not self._path_ok(rev.path):
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
        on_revision: Callable[[SampleRevision], None],
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
            try:
                root = self._directory.expanduser().resolve()
            except OSError:
                root = self._directory
            if not root.is_dir():
                try:
                    root.mkdir(parents=True, exist_ok=True)
                except OSError:
                    pass
            self._directory = root
            self._known = _baseline_known_files(
                self._directory,
                recursive=bool(self._cfg.recursive),
                patterns=tuple(self._cfg.patterns),
            )
            handler = _Handler(
                on_revision=self._on_revision,
                known=self._known,
                lock=self._lock,
                allow_dat=bool(self._cfg.allow_dat),
                path_ok=self._cfg.path_ok,
            )
            obs = Observer()
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

    def restart_at(self, directory: Path, *, cfg: Optional[WatcherConfig] = None) -> None:
        """Stop (if running), point at ``directory``, reset known baseline, and watch again."""
        self.stop()
        if cfg is not None:
            self._cfg = cfg
        self._directory = directory.expanduser().resolve()
        self.start()

    def note_path_stat(self, path: str, snap: Optional[FileStatSnapshot] = None) -> None:
        """Align watcher baseline with an already-handled revision (drop / poll / manual)."""
        raw = (path or "").strip()
        if not raw:
            return
        key = normalize_sample_path(raw)
        if snap is None:
            snap = _try_stat(key)
        if snap is None:
            return
        with self._lock:
            self._known[key] = snap
