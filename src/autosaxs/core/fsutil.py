"""Portable filesystem helpers (symlink privilege gaps, etc.)."""

from __future__ import annotations

import os
import shutil


def replace_with_relative_symlink_or_copy(link_path: str, target_path: str) -> str:
    """
    Create ``link_path`` as a relative symlink to ``target_path``.

    On platforms where creating symlinks needs elevation (notably Windows without
    Developer Mode), fall back to a byte-identical file copy so callers still get
    a stable convenience path.
    """
    target_abs = os.path.normpath(os.path.abspath(os.path.expanduser(target_path)))
    if not os.path.isfile(target_abs):
        raise FileNotFoundError(f"cannot link/copy; missing target: {target_abs}")
    link_abs = os.path.normpath(os.path.abspath(os.path.expanduser(link_path)))
    link_dir = os.path.dirname(link_abs) or "."
    os.makedirs(link_dir, exist_ok=True)
    rel_target = os.path.relpath(target_abs, start=os.path.abspath(link_dir))
    if os.path.lexists(link_abs):
        os.unlink(link_abs)
    try:
        os.symlink(rel_target, link_abs)
    except OSError:
        shutil.copy2(target_abs, link_abs)
    return link_abs
