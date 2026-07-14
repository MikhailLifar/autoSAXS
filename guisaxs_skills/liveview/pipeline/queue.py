from __future__ import annotations

import collections
import heapq
import itertools
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Deque, List, Optional, Tuple

from ..ingest.stability import FileStatSnapshot, StabilityConfig
from ..ingest.tiff_revision import TiffRevision, is_newer_than, normalize_tiff_path
from .jobs import Job, is_manual_job


class RevisionEnqueueResult(str, Enum):
    ADDED = "added"
    REPLACED = "replaced"
    UNCHANGED = "unchanged"
    IGNORED_STALE = "ignored_stale"


@dataclass(frozen=True)
class QueueItem:
    path: str
    detected_at_monotonic: float
    observed_stat: FileStatSnapshot
    stability_cfg: Optional[StabilityConfig] = field(default=None)

    @staticmethod
    def from_revision(rev: TiffRevision, *, stability_cfg: Optional[StabilityConfig] = None) -> QueueItem:
        return QueueItem(
            path=rev.path,
            detected_at_monotonic=float(rev.detected_at),
            observed_stat=rev.stat,
            stability_cfg=stability_cfg,
        )


class FIFOQueue:
    def __init__(self) -> None:
        self._q: Deque[QueueItem] = collections.deque()

    def clear(self) -> None:
        self._q.clear()

    @staticmethod
    def _norm_key(path: str) -> str:
        return normalize_tiff_path(path)

    def contains_path(self, path: str) -> bool:
        k = self._norm_key(path)
        return any(self._norm_key(it.path) == k for it in self._q)

    def put(self, item: QueueItem) -> None:
        self._q.append(item)

    def put_revision(self, item: QueueItem) -> RevisionEnqueueResult:
        """
        Append a TIFF revision, or replace an existing queued entry for the same path in place.

        Replacement keeps FIFO position. Identical stats are ignored; older stats are dropped.
        """
        k = self._norm_key(item.path)
        for i, it in enumerate(self._q):
            if self._norm_key(it.path) != k:
                continue
            existing = it.observed_stat
            if existing == item.observed_stat:
                return RevisionEnqueueResult.UNCHANGED
            if not is_newer_than(item.observed_stat, existing):
                return RevisionEnqueueResult.IGNORED_STALE
            self._q[i] = item
            return RevisionEnqueueResult.REPLACED
        self._q.append(item)
        return RevisionEnqueueResult.ADDED

    def put_if_absent(self, item: QueueItem, *, current_path: Optional[str] = None) -> bool:
        """Legacy helper: append only when path is not already queued or current."""
        k = self._norm_key(item.path)
        if current_path is not None and self._norm_key(current_path) == k:
            return False
        if any(self._norm_key(it.path) == k for it in self._q):
            return False
        self._q.append(item)
        return True

    def get_nowait(self) -> Optional[QueueItem]:
        if not self._q:
            return None
        return self._q.popleft()

    def __len__(self) -> int:
        return len(self._q)

    def __iter__(self):
        return iter(self._q)


class JobQueue:
    """
    Priority-aware queue for `Job`s.

    Higher `job.priority` runs first. FIFO ordering is preserved among jobs with equal priority.
    """

    def __init__(self) -> None:
        self._seq = itertools.count()
        self._heap: List[Tuple[int, int, Job]] = []

    def clear(self) -> None:
        self._heap.clear()

    def put(self, job: Job) -> None:
        pri = int(getattr(job, "priority", 0))
        heapq.heappush(self._heap, (-pri, next(self._seq), job))

    def drop_jobs_for_tiff_path(self, path: str) -> int:
        """Remove not-yet-started jobs targeting ``path`` (superseded by a newer revision)."""
        k = normalize_tiff_path(path)
        if not self._heap:
            return 0
        kept: List[Tuple[int, int, Job]] = []
        dropped = 0
        for entry in self._heap:
            job = entry[2]
            tp = str(job.context.get("tiff_path") or "").strip()
            if tp and normalize_tiff_path(tp) == k:
                dropped += 1
                continue
            kept.append(entry)
        if dropped:
            self._heap = kept
            heapq.heapify(self._heap)
        return dropped

    def get_nowait(self) -> Optional[Job]:
        if not self._heap:
            return None
        _pri, _seq, job = heapq.heappop(self._heap)
        return job

    def get_nowait_manual(self) -> Optional[Job]:
        """Pop the highest-priority manual job, leaving auto/TIFF jobs queued."""
        if not self._heap:
            return None
        best_idx: Optional[int] = None
        for i, entry in enumerate(self._heap):
            if not is_manual_job(entry[2]):
                continue
            if best_idx is None or entry < self._heap[best_idx]:
                best_idx = i
        if best_idx is None:
            return None
        _pri, _seq, job = self._heap.pop(best_idx)
        heapq.heapify(self._heap)
        return job

    def __len__(self) -> int:
        return len(self._heap)
