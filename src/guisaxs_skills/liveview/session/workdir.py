from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from ...logic.workdir import select_workdir


def _dir_is_writable(path: str) -> bool:
    """Return True if we can create and remove a temp file in ``path``.

    Prefer a real write probe over ``os.access(..., W_OK)``, which is unreliable
    for directories on Windows (e.g. the user home folder used by Anaconda Prompt).
    """
    try:
        if not os.path.isdir(path):
            return False
        fd, probe = tempfile.mkstemp(prefix=".autosaxs_write_", dir=path)
        os.close(fd)
        os.remove(probe)
        return True
    except OSError:
        return False


def _validated_watchdir(path: str) -> Optional[str]:
    try:
        resolved = os.path.abspath(path.strip())
        if not resolved or not os.path.isdir(resolved):
            return None
        if not _dir_is_writable(resolved):
            return None
        return resolved
    except Exception:
        return None


def default_watchdir() -> Optional[str]:
    """Current working directory when it is a usable watch folder."""
    try:
        return _validated_watchdir(os.getcwd())
    except Exception:
        return None


def select_watchdir(parent=None, *, initial_directory: Optional[str] = None) -> Optional[str]:
    """Pick a watch folder (same dialog UX as guisaxs_skills working directory)."""
    start = initial_directory or str(Path.cwd())
    return select_workdir(parent=parent, initial_directory=start)
