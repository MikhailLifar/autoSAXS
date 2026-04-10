from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from .queue import FIFOQueue, QueueItem


@dataclass(frozen=True)
class QueueStatus:
    queue_size: int
    current_path: str
    last_processed_path: str
    avg_seconds_per_item: float


class SequentialQueueWorker(QObject):
    status = pyqtSignal(object)  # QueueStatus
    item_started = pyqtSignal(str)  # path
    item_finished = pyqtSignal(str, bool)  # path, success

    def __init__(
        self,
        *,
        queue: FIFOQueue,
        process_item: Callable[[QueueItem], bool],
        status_interval_s: float = 0.5,
    ) -> None:
        super().__init__()
        self._queue = queue
        self._process_item = process_item
        self._status_interval_s = status_interval_s
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._current: str = ""
        self._last: str = ""
        self._durations: list[float] = []

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.is_running():
            return
        self._stop.clear()
        t = threading.Thread(target=self._run, name="LiveviewQueueWorker", daemon=True)
        self._thread = t
        t.start()

    def stop(self) -> None:
        self._stop.set()

    def clear_stats(self) -> None:
        self._current = ""
        self._last = ""
        self._durations.clear()

    def _emit_status(self) -> None:
        avg = (sum(self._durations) / len(self._durations)) if self._durations else 0.0
        self.status.emit(
            QueueStatus(
                queue_size=len(self._queue),
                current_path=self._current,
                last_processed_path=self._last,
                avg_seconds_per_item=avg,
            )
        )

    def _run(self) -> None:
        next_status = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_status:
                self._emit_status()
                next_status = now + float(self._status_interval_s)

            item = self._queue.get_nowait()
            if item is None:
                time.sleep(0.05)
                continue

            self._current = item.path
            self.item_started.emit(item.path)
            t0 = time.monotonic()
            ok = False
            try:
                ok = bool(self._process_item(item))
            except Exception:
                ok = False
            dt = max(0.0, time.monotonic() - t0)
            self._durations.append(dt)
            if len(self._durations) > 50:
                self._durations = self._durations[-50:]
            self._last = item.path
            self._current = ""
            self.item_finished.emit(item.path, ok)
            self._emit_status()

