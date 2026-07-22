from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtWidgets import QApplication

from ..ui.style import apply_style
from .session.workdir import default_watchdir
from .window import LiveviewMainWindow


def run_liveview_app() -> None:
    app = QApplication(sys.argv)
    apply_style(app)

    watchdir = default_watchdir()
    if watchdir is None:
        return

    window = LiveviewMainWindow(watchdir=Path(watchdir))
    window.showMaximized()
    app.exec_()
