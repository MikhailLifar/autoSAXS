from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtWidgets import QApplication

from .core.event_bus import EventBus
from .ui.main_window import MainWindow
from .ui.style import apply_style


def run_app() -> None:
    app = QApplication(sys.argv)
    apply_style(app)

    bus = EventBus()
    workdir = Path.cwd().resolve()

    window = MainWindow(bus=bus, workdir=workdir)
    window.show()
    app.exec_()
