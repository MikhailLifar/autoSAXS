from __future__ import annotations

import sys
from pathlib import Path

from PyQt5.QtWidgets import QApplication

from .core.event_bus import EventBus
from .logic.workdir import select_workdir
from .ui.main_window import MainWindow
from .ui.style import apply_style


def run_app() -> None:
    app = QApplication(sys.argv)
    apply_style(app)

    bus = EventBus()

    workdir = select_workdir(parent=None)
    if workdir is None:
        return

    window = MainWindow(bus=bus, workdir=Path(workdir))
    window.show()
    app.exec_()

