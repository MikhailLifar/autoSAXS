from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from ...core.settings import KEY_LIVEVIEW_LAST_WATCHDIR, liveview_settings
from ...logic.workdir import select_workdir


def _legacy_last_watchdir_path() -> Path:
    # Older builds stored state inside the installed package (often not writable).
    return Path(__file__).resolve().parents[1] / ".last_watchdir_liveview.txt"


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


def _load_legacy_last_watchdir() -> Optional[str]:
    try:
        p = _legacy_last_watchdir_path()
        if not p.exists():
            return None
        text = p.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return None
        return _validated_watchdir(text)
    except Exception:
        return None


def load_last_watchdir() -> Optional[str]:
    try:
        raw = liveview_settings().value(KEY_LIVEVIEW_LAST_WATCHDIR, "")
        text = str(raw).strip() if raw else ""
        if text:
            path = _validated_watchdir(text)
            if path is not None:
                return path
    except Exception:
        pass

    legacy = _load_legacy_last_watchdir()
    if legacy is not None:
        save_last_watchdir(legacy)
    return legacy


def save_last_watchdir(path: str) -> None:
    validated = _validated_watchdir(path)
    if validated is None:
        return
    try:
        s = liveview_settings()
        s.setValue(KEY_LIVEVIEW_LAST_WATCHDIR, validated)
        s.sync()
    except Exception:
        return


def select_watchdir(parent=None, *, initial_directory: Optional[str] = None) -> Optional[str]:
    """Pick a watch folder (same dialog UX as guisaxs_skills working directory)."""
    return select_workdir(parent=parent, initial_directory=initial_directory)
