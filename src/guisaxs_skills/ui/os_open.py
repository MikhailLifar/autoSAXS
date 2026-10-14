"""Open a filesystem path in the platform file manager (single owner)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Union


def open_path_in_file_manager(path: Union[str, Path]) -> bool:
    """
    Reveal ``path`` in the system file manager.

    Returns True when a launch was attempted; False if the path is missing.
    """
    p = Path(path).expanduser()
    if not p.exists():
        return False
    folder = p if p.is_dir() else p.parent
    if not folder.is_dir():
        return False
    target = str(folder.resolve())
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", target])
        elif sys.platform.startswith("win"):
            os.startfile(target)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", target])
        return True
    except Exception:
        return False
