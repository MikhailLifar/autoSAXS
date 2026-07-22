from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from ...logic.workdir import select_workdir


def _validated_watchdir(path: str) -> Optional[str]:
    try:
        resolved = os.path.abspath(path.strip())
        if not resolved or not os.path.isdir(resolved):
            return None
        if not os.access(resolved, os.W_OK):
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
