from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from ..logic.workdir import select_workdir


def _last_watchdir_path() -> Path:
    # Store state alongside guisaxs_skills package state, but separate key.
    return Path(__file__).resolve().parents[1] / ".last_watchdir_liveview.txt"


def load_last_watchdir() -> Optional[str]:
    try:
        p = _last_watchdir_path()
        if not p.exists():
            return None
        text = p.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return None
        path = os.path.abspath(text)
        if not os.path.isdir(path):
            return None
        if not os.access(path, os.W_OK):
            return None
        return path
    except Exception:
        return None


def save_last_watchdir(path: str) -> None:
    try:
        p = _last_watchdir_path()
        p.write_text(str(path).strip() + "\n", encoding="utf-8")
    except Exception:
        return


def select_watchdir(parent=None, *, initial_directory: Optional[str] = None) -> Optional[str]:
    """Pick a watch folder (same dialog UX as guisaxs_skills working directory)."""
    return select_workdir(parent=parent, initial_directory=initial_directory)

