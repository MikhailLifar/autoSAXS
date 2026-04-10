from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import Deque, Optional


@dataclass(frozen=True)
class QueueItem:
    path: str
    detected_at_monotonic: float


class FIFOQueue:
    def __init__(self) -> None:
        self._q: Deque[QueueItem] = collections.deque()

    def clear(self) -> None:
        self._q.clear()

    def put(self, item: QueueItem) -> None:
        self._q.append(item)

    def get_nowait(self) -> Optional[QueueItem]:
        if not self._q:
            return None
        return self._q.popleft()

    def __len__(self) -> int:
        return len(self._q)

