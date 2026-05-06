from __future__ import annotations

import collections
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Deque, Optional

import heapq
import itertools
from typing import List, Tuple

from .jobs import Job


@dataclass(frozen=True)
class QueueItem:
    path: str
    detected_at_monotonic: float


class FIFOQueue:
    def __init__(self) -> None:
        self._q: Deque[QueueItem] = collections.deque()

    def clear(self) -> None:
        self._q.clear()

    @staticmethod
    def _norm_key(path: str) -> str:
        try:
            return str(Path(path).resolve())
        except Exception:
            return os.path.normcase(os.path.abspath(path))

    def contains_path(self, path: str) -> bool:
        k = self._norm_key(path)
        return any(self._norm_key(it.path) == k for it in self._q)

    def put(self, item: QueueItem) -> None:
        self._q.append(item)

    def put_if_absent(self, item: QueueItem, *, current_path: Optional[str] = None) -> bool:
        """Append item only if the same path is not already queued or currently processing. Preserves FIFO order."""
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

    def get_nowait(self) -> Optional[Job]:
        if not self._heap:
            return None
        _pri, _seq, job = heapq.heappop(self._heap)
        return job

    def __len__(self) -> int:
        return len(self._heap)

