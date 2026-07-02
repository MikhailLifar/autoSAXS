from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from PyQt5.QtCore import QObject, QTimer

from .poll_watcher import POLL_TRIGGERED_STABILITY
from .stability import FileStatSnapshot, _try_stat

TREE_STABILITY = POLL_TRIGGERED_STABILITY

_CACHE_VERSION = 1


def _is_tif(path: str) -> bool:
    p = path.lower()
    return p.endswith(".tif") or p.endswith(".tiff")


def _try_stat_dir(path: Path) -> Optional[FileStatSnapshot]:
    try:
        st = os.stat(path)
        return FileStatSnapshot(size=int(st.st_size), mtime_ns=int(st.st_mtime_ns))
    except Exception:
        return None


@dataclass(frozen=True)
class TreeObserverConfig:
    slow_interval_s: float = 10.0
    fast_interval_s: float = 0.25
    hot_idle_s: float = 10.0
    cache_dir_name: str = ".dir_cache"
    cache_file_name: str = "tree_cache.json"


@dataclass
class TreeCache:
    watchdir: Path
    dirs: Dict[str, FileStatSnapshot] = field(default_factory=dict)
    files: Dict[str, FileStatSnapshot] = field(default_factory=dict)
    dirty: bool = False

    @staticmethod
    def _snap_to_json(s: FileStatSnapshot) -> dict:
        return {"mtime_ns": s.mtime_ns, "size": s.size}

    @staticmethod
    def _snap_from_json(raw: object) -> Optional[FileStatSnapshot]:
        if not isinstance(raw, dict):
            return None
        try:
            return FileStatSnapshot(size=int(raw["size"]), mtime_ns=int(raw["mtime_ns"]))
        except (KeyError, TypeError, ValueError):
            return None

    def load(self, path: Path) -> None:
        if not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            return
        if not isinstance(raw, dict):
            return
        if int(raw.get("version", 0)) != _CACHE_VERSION:
            return
        saved_wd = raw.get("watchdir")
        if isinstance(saved_wd, str) and saved_wd:
            try:
                if Path(saved_wd).resolve() != self.watchdir.resolve():
                    return
            except OSError:
                return
        dirs_raw = raw.get("dirs")
        if isinstance(dirs_raw, dict):
            for k, v in dirs_raw.items():
                snap = self._snap_from_json(v)
                if snap is not None and isinstance(k, str):
                    self.dirs[k] = snap
        files_raw = raw.get("files")
        if isinstance(files_raw, dict):
            for k, v in files_raw.items():
                snap = self._snap_from_json(v)
                if snap is not None and isinstance(k, str):
                    self.files[k] = snap

    def save(self, path: Path) -> None:
        if not self.dirty:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _CACHE_VERSION,
            "watchdir": str(self.watchdir.resolve()),
            "dirs": {k: self._snap_to_json(v) for k, v in self.dirs.items()},
            "files": {k: self._snap_to_json(v) for k, v in self.files.items()},
        }
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        os.replace(str(tmp), str(path))
        self.dirty = False


class TreeScanEngine:
    """Qt-free pruned directory scan with TIFF discovery."""

    def __init__(
        self,
        *,
        watchdir: Path,
        cache: TreeCache,
        cache_dir_name: str = ".dir_cache",
    ) -> None:
        self._watchdir = watchdir.expanduser().resolve()
        self._cache = cache
        self._cache_dir_name = cache_dir_name
        self._hot_dirs: Dict[str, float] = {}
        self._baselined = False

    @property
    def hot_dirs(self) -> Dict[str, float]:
        return self._hot_dirs

    @property
    def baselined(self) -> bool:
        return self._baselined

    def mark_baselined(self) -> None:
        self._baselined = True

    def clear_hot_dirs(self) -> None:
        self._hot_dirs.clear()

    def expire_hot_dirs(self, *, hot_idle_s: float, now: Optional[float] = None) -> None:
        t = time.monotonic() if now is None else now
        idle = max(0.0, float(hot_idle_s))
        stale = [k for k, last in self._hot_dirs.items() if (t - last) > idle]
        for k in stale:
            self._hot_dirs.pop(k, None)

    def _is_ignored(self, path: Path) -> bool:
        name = path.name
        if name == self._cache_dir_name:
            return True
        try:
            for part in path.resolve().relative_to(self._watchdir).parts:
                if part == self._cache_dir_name:
                    return True
        except ValueError:
            pass
        return False

    def _touch_hot(self, dir_key: str) -> None:
        self._hot_dirs[dir_key] = time.monotonic()

    def _record_tiff(self, file_key: str, snap: FileStatSnapshot) -> bool:
        """Update file cache; return True if this is a post-baseline candidate."""
        prev = self._cache.files.get(file_key)
        self._cache.files[file_key] = snap
        if prev != snap:
            self._cache.dirty = True
        if not self._baselined:
            return False
        return prev is None or prev != snap

    def _scan_tiffs_in_dir(self, dir_path: Path) -> List[str]:
        found: List[str] = []
        for pattern in ("*.tif", "*.tiff"):
            try:
                entries = list(dir_path.glob(pattern))
            except OSError:
                continue
            for f in entries:
                if not f.is_file():
                    continue
                file_key = str(f.resolve())
                snap = _try_stat(file_key)
                if snap is None:
                    continue
                if self._record_tiff(file_key, snap):
                    found.append(file_key)
        return found

    def _scan_directory(
        self,
        dir_path: Path,
        *,
        recurse_all: bool,
        force_tif_scan: bool = False,
    ) -> List[str]:
        candidates: List[str] = []
        if self._is_ignored(dir_path):
            return candidates

        dir_key = str(dir_path.resolve())
        st = _try_stat_dir(dir_path)
        if st is None:
            return candidates

        cached = self._cache.dirs.get(dir_key)
        dir_changed = cached != st
        if dir_changed:
            self._cache.dirs[dir_key] = st
            self._cache.dirty = True
            self._touch_hot(dir_key)

        if dir_changed or force_tif_scan:
            candidates.extend(self._scan_tiffs_in_dir(dir_path))

        try:
            children = sorted(dir_path.iterdir())
        except OSError:
            return candidates

        for child in children:
            if not child.is_dir():
                continue
            if self._is_ignored(child):
                continue
            child_key = str(child.resolve())
            child_st = _try_stat_dir(child)
            if child_st is None:
                continue
            child_cached = self._cache.dirs.get(child_key)
            child_changed = child_cached != child_st
            if recurse_all:
                if child_changed:
                    self._cache.dirs[child_key] = child_st
                    self._cache.dirty = True
                    self._touch_hot(child_key)
                candidates.extend(self._scan_directory(child, recurse_all=True))
            elif child_changed:
                self._cache.dirs[child_key] = child_st
                self._cache.dirty = True
                self._touch_hot(child_key)
                candidates.extend(self._scan_directory(child, recurse_all=False))

        return candidates

    def baseline(self) -> None:
        self._baselined = False
        self._scan_directory(self._watchdir, recurse_all=True)
        self._baselined = True

    def slow_scan(self) -> List[str]:
        return self._scan_directory(self._watchdir, recurse_all=True)

    def fast_scan(self) -> List[str]:
        candidates: List[str] = []
        for dir_key in list(self._hot_dirs.keys()):
            try:
                candidates.extend(
                    self._scan_directory(Path(dir_key), recurse_all=False, force_tif_scan=True)
                )
            except OSError:
                continue
        return candidates


class TreeDirObserver(QObject):
    """Hierarchical mtime observer for tree watch mode (NFS-friendly)."""

    def __init__(
        self,
        *,
        watchdir: Path,
        cfg: Optional[TreeObserverConfig] = None,
        on_update: Callable[[str, float], None],
    ) -> None:
        super().__init__()
        self._cfg = cfg or TreeObserverConfig()
        self._watchdir = watchdir.expanduser().resolve()
        self._on_update = on_update
        self._idle_check: Callable[[], bool] = lambda: True
        self._cache = TreeCache(watchdir=self._watchdir)
        self._cache_path = self._watchdir / self._cfg.cache_dir_name / self._cfg.cache_file_name
        self._engine = TreeScanEngine(
            watchdir=self._watchdir,
            cache=self._cache,
            cache_dir_name=self._cfg.cache_dir_name,
        )
        self._pending: Dict[str, float] = {}
        self._slow_timer = QTimer(self)
        self._slow_timer.setInterval(max(1000, int(float(self._cfg.slow_interval_s) * 1000)))
        self._slow_timer.timeout.connect(self._on_slow_timer)
        self._fast_timer = QTimer(self)
        self._fast_timer.setInterval(max(100, int(float(self._cfg.fast_interval_s) * 1000)))
        self._fast_timer.timeout.connect(self._on_fast_timer)
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(100)
        self._flush_timer.timeout.connect(self._try_flush_pending)
        self._running = False

    def set_idle_check(self, fn: Callable[[], bool]) -> None:
        self._idle_check = fn

    def restart_at(self, watchdir: Path) -> None:
        self.stop()
        self._watchdir = watchdir.expanduser().resolve()
        self._cache = TreeCache(watchdir=self._watchdir)
        self._cache_path = self._watchdir / self._cfg.cache_dir_name / self._cfg.cache_file_name
        self._engine = TreeScanEngine(
            watchdir=self._watchdir,
            cache=self._cache,
            cache_dir_name=self._cfg.cache_dir_name,
        )
        self._pending.clear()
        self.start()

    def start(self) -> None:
        if self._running:
            return
        self._cache.load(self._cache_path)
        self._engine.baseline()
        self._cache.save(self._cache_path)
        self._running = True
        self._slow_timer.start()
        self._fast_timer.start()
        self._flush_timer.start()

    def stop(self) -> None:
        self._running = False
        self._slow_timer.stop()
        self._fast_timer.stop()
        self._flush_timer.stop()
        try:
            self._cache.save(self._cache_path)
        except Exception:
            pass

    def clear(self) -> None:
        self._pending.clear()
        self._engine.clear_hot_dirs()

    def pending_paths(self) -> Tuple[str, ...]:
        return tuple(self._pending.keys())

    def _collect(self, paths: List[str]) -> None:
        now = time.monotonic()
        for p in paths:
            if p and _is_tif(p):
                self._pending[p] = now

    def _on_slow_timer(self) -> None:
        if not self._running:
            return
        self._engine.expire_hot_dirs(hot_idle_s=self._cfg.hot_idle_s)
        self._collect(self._engine.slow_scan())
        self._cache.save(self._cache_path)
        self._try_flush_pending()

    def _on_fast_timer(self) -> None:
        if not self._running:
            return
        if not self._engine.hot_dirs:
            return
        self._collect(self._engine.fast_scan())
        self._cache.save(self._cache_path)
        self._try_flush_pending()

    def _try_flush_pending(self) -> None:
        if not self._pending:
            return
        if not self._idle_check():
            return
        items = sorted(self._pending.items(), key=lambda kv: kv[1])
        self._pending.clear()
        for path, detected_at in items:
            self._on_update(path, detected_at)

    # Test hooks
    def scan_slow_once(self) -> List[str]:
        self._engine.expire_hot_dirs(hot_idle_s=self._cfg.hot_idle_s)
        paths = self._engine.slow_scan()
        self._cache.save(self._cache_path)
        return paths

    def flush_pending_for_tests(self) -> None:
        self._try_flush_pending()
