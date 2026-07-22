from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class StabilityConfig:
    poll_interval_s: float = 0.25
    required_unchanged_polls: int = 3
    timeout_s: float = 30.0


@dataclass(frozen=True)
class FileStatSnapshot:
    size: int
    mtime_ns: int


def _try_stat(path: str) -> Optional[FileStatSnapshot]:
    try:
        st = os.stat(path)
        return FileStatSnapshot(size=int(st.st_size), mtime_ns=int(st.st_mtime_ns))
    except Exception:
        return None


def wait_until_stable(path: str, *, cfg: StabilityConfig) -> bool:
    """
    Return True when file is considered stable, False on timeout.

    Stability heuristic: size + mtime_ns unchanged for N consecutive polls.
    """
    deadline = time.monotonic() + max(0.0, float(cfg.timeout_s))
    unchanged = 0
    prev = _try_stat(path)
    if prev is None:
        return False

    while time.monotonic() < deadline:
        time.sleep(max(0.01, float(cfg.poll_interval_s)))
        cur = _try_stat(path)
        if cur is None:
            unchanged = 0
            prev = None
            continue
        if prev is not None and (cur.size == prev.size and cur.mtime_ns == prev.mtime_ns):
            unchanged += 1
            if unchanged >= int(cfg.required_unchanged_polls):
                return True
        else:
            unchanged = 0
        prev = cur
    return False


class StabilityTracker:
    """
    Non-blocking stability tracker for a single file.

    Call `tick()` periodically; it returns:
    - True  -> stable
    - False -> not stable yet
    - None  -> gave up (timeout or cannot stat)
    """

    def __init__(self, *, path: str, cfg: StabilityConfig) -> None:
        self._path = path
        self._cfg = cfg
        self._deadline = time.monotonic() + max(0.0, float(cfg.timeout_s))
        self._prev: Optional[FileStatSnapshot] = _try_stat(path)
        self._unchanged = 0

    def tick(self) -> Optional[bool]:
        if time.monotonic() >= self._deadline:
            return None
        cur = _try_stat(self._path)
        if cur is None:
            self._unchanged = 0
            self._prev = None
            return False
        if self._prev is not None and (cur.size == self._prev.size and cur.mtime_ns == self._prev.mtime_ns):
            self._unchanged += 1
            if self._unchanged >= int(self._cfg.required_unchanged_polls):
                return True
        else:
            self._unchanged = 0
        self._prev = cur
        return False

