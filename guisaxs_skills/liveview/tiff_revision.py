from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from .stability import FileStatSnapshot, _try_stat


class TiffRevisionSource(str, Enum):
    INOTIFY = "inotify"
    POLL = "poll"
    TREE = "tree"
    MANUAL = "manual"


@dataclass(frozen=True)
class TiffRevision:
    """A concrete on-disk TIFF version: normalized path + stat identity."""

    path: str
    stat: FileStatSnapshot
    detected_at: float
    source: TiffRevisionSource = TiffRevisionSource.MANUAL


def is_tiff_path(path: str) -> bool:
    p = (path or "").lower()
    return p.endswith(".tif") or p.endswith(".tiff")


def normalize_tiff_path(path: str) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except Exception:
        return os.path.normcase(os.path.abspath(path))


def stat_snapshot(path: str) -> Optional[FileStatSnapshot]:
    return _try_stat(path)


def revision_changed(prev: Optional[FileStatSnapshot], cur: FileStatSnapshot) -> bool:
    if prev is None:
        return True
    return prev != cur


def is_newer_than(candidate: FileStatSnapshot, than: FileStatSnapshot) -> bool:
    """True when ``candidate`` is a strictly newer on-disk version than ``than``."""
    if candidate.mtime_ns != than.mtime_ns:
        return candidate.mtime_ns > than.mtime_ns
    return candidate.size > than.size


def make_revision(
    *,
    path: str,
    detected_at: float,
    source: TiffRevisionSource,
    stat: Optional[FileStatSnapshot] = None,
) -> Optional[TiffRevision]:
    raw = (path or "").strip()
    if not raw or not is_tiff_path(raw):
        return None
    norm = normalize_tiff_path(raw)
    snap = stat if stat is not None else stat_snapshot(norm)
    if snap is None:
        return None
    return TiffRevision(path=norm, stat=snap, detected_at=float(detected_at), source=source)
