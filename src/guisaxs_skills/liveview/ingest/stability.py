from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Optional

_IS_WINDOWS = os.name == "nt"


@dataclass(frozen=True)
class StabilityConfig:
    poll_interval_s: float = 0.25
    required_unchanged_polls: int = 3
    timeout_s: float = 30.0


@dataclass(frozen=True, eq=False)
class FileStatSnapshot:
    """On-disk identity for change detection.

    Equality is platform-aware (this type is the sole owner of “same revision?”):

    - Always: ``size`` + ``mtime_ns`` + ``ctime_ns``.
    - POSIX: also ``dev``/``ino`` when both sides have a non-zero inode (catches
      replaces; ``ctime`` catches delete+recreate when inode/size/mtime are reused
      after ``copy2`` / F5 + ``utime``).
    - Windows: **ignore** ``dev``/``ino``. File indexes are often 0 or unstable
      (SMB/network volumes), which otherwise makes every TREE/poll re-stat look
      like a change → hot dirs never cool → detection/reprocess loops.
      ``st_ctime`` is creation time and stays stable for untouched files.
    """

    size: int
    mtime_ns: int
    dev: int = 0
    ino: int = 0
    ctime_ns: int = 0

    def _identity_tuple(self) -> tuple:
        base = (self.size, self.mtime_ns, self.ctime_ns)
        if _IS_WINDOWS:
            return base
        if self.ino:
            return base + (self.dev, self.ino)
        return base

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FileStatSnapshot):
            return NotImplemented
        return self._identity_tuple() == other._identity_tuple()

    def __hash__(self) -> int:
        return hash(self._identity_tuple())

    def is_newer_than(self, than: "FileStatSnapshot") -> bool:
        """True when ``self`` is a strictly newer on-disk version than ``than``."""
        if not _IS_WINDOWS:
            if self.ino and than.ino and (self.dev, self.ino) != (than.dev, than.ino):
                return True
        if self.ctime_ns != than.ctime_ns:
            return self.ctime_ns > than.ctime_ns
        if self.mtime_ns != than.mtime_ns:
            return self.mtime_ns > than.mtime_ns
        return self.size > than.size


def _try_stat(path: str) -> Optional[FileStatSnapshot]:
    try:
        st = os.stat(path)
        return FileStatSnapshot(
            size=int(st.st_size),
            mtime_ns=int(st.st_mtime_ns),
            dev=int(getattr(st, "st_dev", 0) or 0),
            ino=int(getattr(st, "st_ino", 0) or 0),
            ctime_ns=int(getattr(st, "st_ctime_ns", 0) or 0),
        )
    except Exception:
        return None


def wait_until_stable(path: str, *, cfg: StabilityConfig) -> bool:
    """
    Return True when file is considered stable, False on timeout.

    Stability heuristic: full ``FileStatSnapshot`` unchanged for N consecutive polls.
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
        if prev is not None and cur == prev:
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
        if self._prev is not None and cur == self._prev:
            self._unchanged += 1
            if self._unchanged >= int(self._cfg.required_unchanged_polls):
                return True
        else:
            self._unchanged = 0
        self._prev = cur
        return False

