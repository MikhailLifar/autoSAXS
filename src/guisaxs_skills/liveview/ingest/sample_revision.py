from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional

from .stability import FileStatSnapshot, _try_stat


class SampleRevisionSource(str, Enum):
    INOTIFY = "inotify"
    POLL = "poll"
    TREE = "tree"
    MANUAL = "manual"


@dataclass(frozen=True)
class SampleRevision:
    """On-disk sample version: normalized path + stat identity (frame or curve)."""

    path: str
    stat: FileStatSnapshot
    detected_at: float
    source: SampleRevisionSource = SampleRevisionSource.MANUAL


def is_tiff_path(path: str) -> bool:
    p = (path or "").lower()
    return p.endswith(".tif") or p.endswith(".tiff")


def is_dat_path(path: str) -> bool:
    return (path or "").lower().endswith(".dat")


def is_subtract_plot_dat(path: str) -> bool:
    """
    True for subtract skill plot-data ``.dat`` artifacts that are not sample curves.

    Explicit: ``diff_log_*.dat`` (multi-curve log-diff table without ``intensity``).
    """
    name = Path(path or "").name.lower()
    return name.startswith("diff_log_")


def is_sample_dat_path(path: str) -> bool:
    """True for ingestible 1D sample curves (not subtract plot-data artifacts)."""
    return is_dat_path(path) and not is_subtract_plot_dat(path)


def is_sample_path(path: str) -> bool:
    """Frame TIFF or 1D curve path that liveview may ingest."""
    return is_tiff_path(path) or is_sample_dat_path(path)


def normalize_sample_path(path: str) -> str:
    """Absolute path key for ingest caches / settle / owned-output sets.

    On Windows, ``normcase`` so TREE scan keys and acknowledge keys cannot diverge
    by drive/letter casing (case-insensitive FS, case-sensitive dicts).
    """
    try:
        resolved = str(Path(path).expanduser().resolve())
    except Exception:
        resolved = os.path.abspath(path)
    if os.name == "nt":
        return os.path.normcase(resolved)
    return resolved


def sample_stem_from_path(path: str) -> str:
    """Stem for outputs: strip leading ``int_`` / ``sub_`` from curve basenames."""
    stem = Path(path).stem
    low = stem.lower()
    if low.startswith("int_"):
        return stem[4:]
    if low.startswith("sub_"):
        return stem[4:]
    return stem


def stat_snapshot(path: str) -> Optional[FileStatSnapshot]:
    return _try_stat(path)


def revision_changed(prev: Optional[FileStatSnapshot], cur: FileStatSnapshot) -> bool:
    if prev is None:
        return True
    return prev != cur


def is_newer_than(candidate: FileStatSnapshot, than: FileStatSnapshot) -> bool:
    """True when ``candidate`` is a strictly newer on-disk version than ``than``."""
    return candidate.is_newer_than(than)


def make_revision(
    *,
    path: str,
    detected_at: float,
    source: SampleRevisionSource,
    stat: Optional[FileStatSnapshot] = None,
    allow_dat: bool = False,
) -> Optional[SampleRevision]:
    raw = (path or "").strip()
    if not raw:
        return None
    if is_tiff_path(raw):
        pass
    elif allow_dat and is_sample_dat_path(raw):
        pass
    else:
        return None
    norm = normalize_sample_path(raw)
    snap = stat if stat is not None else stat_snapshot(norm)
    if snap is None:
        return None
    return SampleRevision(path=norm, stat=snap, detected_at=float(detected_at), source=source)
