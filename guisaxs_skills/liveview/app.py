from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtWidgets import QApplication

from ..ui.style import apply_style
from .session.workdir import default_watchdir, load_last_watchdir, save_last_watchdir
from .window import LiveviewMainWindow


def run_liveview_app() -> None:
    app = QApplication(sys.argv)
    apply_style(app)

    watchdir = load_last_watchdir() or default_watchdir()
    if watchdir is None:
        return
    save_last_watchdir(watchdir)

    window = LiveviewMainWindow(watchdir=Path(watchdir))
    window.showMaximized()
    app.exec_()

