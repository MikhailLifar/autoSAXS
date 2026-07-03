from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional

from PyQt5.QtCore import QObject, QTimer

from .stability import FileStatSnapshot, StabilityConfig, _try_stat
from .tiff_revision import TiffRevision, TiffRevisionSource, is_tiff_path, normalize_tiff_path


# Tuned for NFS atomic overwrite (e.g. Lima temp.tif): fast detect, short settle.
# Executor ticks stability every 100 ms, so required_unchanged_polls=2 ≈ 200 ms.
POLL_TRIGGERED_STABILITY = StabilityConfig(
    poll_interval_s=0.1,
    required_unchanged_polls=2,
    timeout_s=15.0,
)


@dataclass(frozen=True)
class PollWatcherConfig:
    """Targeted stat polling for already-processed TIFFs (NFS / inotify fallback)."""

    poll_interval_s: float = 0.25


class ProcessedTiffPollEngine:
    """Qt-free stat polling for tracked TIFF paths."""

    def __init__(
        self,
        *,
        on_revision: Callable[[TiffRevision], None],
    ) -> None:
        self._on_revision = on_revision
        self._idle_check: Callable[[], bool] = lambda: True
        self._tracked: Dict[str, FileStatSnapshot] = {}

    def set_idle_check(self, fn: Callable[[], bool]) -> None:
        self._idle_check = fn

    def clear(self) -> None:
        self._tracked.clear()

    def track_processed_path(self, path: str) -> None:
        """Remember ``path`` and record its current stat as the poll baseline."""
        if not path or not is_tiff_path(path):
            return
        key = normalize_tiff_path(path)
        snap = _try_stat(key)
        if snap is not None:
            self._tracked[key] = snap

    def poll_once(self) -> None:
        if not self._idle_check():
            return
        now = time.monotonic()
        for path, prev in list(self._tracked.items()):
            cur = _try_stat(path)
            if cur is None or cur == prev:
                continue
            self._tracked[path] = cur
            self._on_revision(
                TiffRevision(
                    path=path,
                    stat=cur,
                    detected_at=now,
                    source=TiffRevisionSource.POLL,
                )
            )


class ProcessedTiffPoller(QObject):
    """
    Stat-poll only TIFF paths that were successfully processed at least once.

    Unlike watchdog's directory-wide ``PollingObserver``, this touches only tracked
    files and runs its timer callback solely while the executor reports idle.
    """

    def __init__(
        self,
        *,
        cfg: Optional[PollWatcherConfig] = None,
        on_revision: Callable[[TiffRevision], None],
    ) -> None:
        super().__init__()
        self._cfg = cfg or PollWatcherConfig()
        self._engine = ProcessedTiffPollEngine(on_revision=on_revision)
        self._timer = QTimer(self)
        self._timer.setInterval(max(100, int(float(self._cfg.poll_interval_s) * 1000)))
        self._timer.timeout.connect(self._engine.poll_once)

    def set_idle_check(self, fn: Callable[[], bool]) -> None:
        self._engine.set_idle_check(fn)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def clear(self) -> None:
        self._engine.clear()

    def track_processed_path(self, path: str) -> None:
        self._engine.track_processed_path(path)

    def poll_once(self) -> None:
        self._engine.poll_once()
