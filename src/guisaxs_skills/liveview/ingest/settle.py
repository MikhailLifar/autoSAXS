"""Shared settle stage: detectors observe changes; only stable revisions leave."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from PyQt5.QtCore import QObject, QTimer

from .sample_revision import SampleRevision, SampleRevisionSource, is_newer_than, normalize_sample_path
from .stability import FileStatSnapshot, StabilityConfig, StabilityTracker, _try_stat

# One settle policy for all detectors (fast poll, short unchanged streak).
SETTLE_CONFIG = StabilityConfig(
    poll_interval_s=0.1,
    required_unchanged_polls=3,
    timeout_s=30.0,
)


@dataclass
class _SettleEntry:
    source: SampleRevisionSource
    detected_at: float
    observed_stat: FileStatSnapshot
    tracker: StabilityTracker


class RevisionSettler(QObject):
    """
    Path → in-flight stability. Detectors call ``observe``; when a path's snap is
    unchanged for ``SETTLE_CONFIG``, emit one ``SampleRevision`` with the final snap.
    """

    def __init__(
        self,
        *,
        on_stable: Callable[[SampleRevision], None],
        on_timeout: Optional[Callable[[str], None]] = None,
        cfg: Optional[StabilityConfig] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._on_stable = on_stable
        self._on_timeout = on_timeout
        self._cfg = cfg or SETTLE_CONFIG
        self._entries: Dict[str, _SettleEntry] = {}
        self._timer = QTimer(self)
        interval_ms = max(50, int(float(self._cfg.poll_interval_s) * 1000))
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self._entries.clear()

    def clear(self) -> None:
        self._entries.clear()

    def observe(self, revision: SampleRevision) -> None:
        """Note a detected change; newer snaps reset settle for that path."""
        key = normalize_sample_path(revision.path)
        if not key:
            return
        cur = self._entries.get(key)
        if cur is not None:
            if cur.observed_stat == revision.stat:
                return
            if not is_newer_than(revision.stat, cur.observed_stat):
                return
        self._entries[key] = _SettleEntry(
            source=revision.source,
            detected_at=float(revision.detected_at),
            observed_stat=revision.stat,
            tracker=StabilityTracker(path=key, cfg=self._cfg),
        )
        self.start()

    def discard(self, path: str) -> None:
        key = normalize_sample_path(path)
        self._entries.pop(key, None)

    def _tick(self) -> None:
        if not self._entries:
            return
        now = time.monotonic()
        done: list[str] = []
        for key, entry in list(self._entries.items()):
            result = entry.tracker.tick()
            if result is None:
                done.append(key)
                if self._on_timeout is not None:
                    self._on_timeout(key)
                continue
            if result is False:
                continue
            snap = _try_stat(key)
            if snap is None:
                done.append(key)
                if self._on_timeout is not None:
                    self._on_timeout(key)
                continue
            done.append(key)
            self._on_stable(
                SampleRevision(
                    path=key,
                    stat=snap,
                    detected_at=entry.detected_at if entry.detected_at > 0 else now,
                    source=entry.source,
                )
            )
        for key in done:
            self._entries.pop(key, None)
