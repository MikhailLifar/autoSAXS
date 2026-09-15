from __future__ import annotations

import os
import sys
from pathlib import Path

from PyQt5.QtWidgets import QApplication, QMessageBox

from ..ui.style import apply_style
from .session.workdir import default_watchdir, select_watchdir
from .window import LiveviewMainWindow


def _warn_unusable_cwd(cwd: str) -> None:
    desktop = Path.home() / "Desktop"
    msg = (
        "GUISAXS-LiveView needs a writable working directory for data and outputs.\n"
        f"Current directory is not usable:\n  {cwd}\n\n"
        "Choose another folder in the dialog, or relaunch from a writable directory "
        f"(for example {desktop}):\n"
        f"  cd \"{desktop}\"\n"
        "  guisaxs-liveview"
    )
    print(msg, file=sys.stderr)
    try:
        QMessageBox.warning(None, "Choose a working directory", msg)
    except Exception:
        pass


def run_liveview_app() -> None:
    app = QApplication(sys.argv)
    apply_style(app)

    cwd = os.getcwd()
    watchdir = default_watchdir()
    if watchdir is None:
        _warn_unusable_cwd(cwd)
        watchdir = select_watchdir(parent=None, initial_directory=str(Path.home()))
        if watchdir is None:
            print(
                "No working directory selected; exiting. "
                "cd to a writable folder and run guisaxs-liveview again.",
                file=sys.stderr,
            )
            return

    window = LiveviewMainWindow(watchdir=Path(watchdir))
    window.showMaximized()
    app.exec_()
