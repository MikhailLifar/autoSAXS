from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtWidgets import QApplication

from ..core.event_bus import EventBus
from ..ui.style import apply_style
from .workdir import default_watchdir, load_last_watchdir, save_last_watchdir
from .window import LiveviewMainWindow


def run_liveview_app() -> None:
    app = QApplication(sys.argv)
    apply_style(app)

    bus = EventBus()

    watchdir = load_last_watchdir() or default_watchdir()
    if watchdir is None:
        return
    save_last_watchdir(watchdir)

    window = LiveviewMainWindow(bus=bus, watchdir=Path(watchdir))
    window.showMaximized()
    app.exec_()

