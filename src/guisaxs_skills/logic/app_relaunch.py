"""Process-level relaunch of guisaxs-liveview (watchdir change, post-update restart)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Union

PathLike = Union[str, Path]


def guisaxs_liveview_restart_argv() -> List[str]:
    """Return argv to start guisaxs-liveview from the current environment."""
    scripts = Path(sys.executable).resolve().parent
    if sys.platform == "win32":
        exe = scripts / "guisaxs-liveview.exe"
        if exe.is_file():
            return [str(exe)]
    script = scripts / "guisaxs-liveview"
    if script.is_file():
        return [str(script)]
    return [sys.executable, "-m", "guisaxs_liveview"]


def spawn_detached(argv: Sequence[str], *, cwd: Optional[PathLike] = None) -> int:
    """Spawn a detached process; return its PID."""
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if cwd is not None:
        kwargs["cwd"] = str(Path(cwd).expanduser().resolve())
    if sys.platform == "win32":
        CREATE_NO_WINDOW = 0x08000000
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    return int(subprocess.Popen(list(argv), **kwargs).pid)


def launch_guisaxs_liveview(*, cwd: Optional[PathLike] = None) -> int:
    """
    Spawn a detached guisaxs-liveview process; return its PID.

    When ``cwd`` is set, the child starts in that directory. Liveview cold start
    uses the process cwd as the watch directory (``default_watchdir``).
    """
    return spawn_detached(guisaxs_liveview_restart_argv(), cwd=cwd)
