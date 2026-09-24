"""Qt application helpers."""

from __future__ import annotations

import signal

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication


def install_sigint_quit(app: QApplication) -> QTimer:
    """
    Make Ctrl+C quit the Qt event loop cleanly (no traceback / core dump).

    Python only delivers SIGINT between bytecode instructions; a short timer
    keeps the interpreter waking so the handler can run while ``exec_`` blocks.
    """
    signal.signal(signal.SIGINT, lambda *_args: app.quit())
    timer = QTimer(app)
    timer.setInterval(200)
    timer.timeout.connect(lambda: None)
    timer.start()
    # Keep a strong ref on the app so the timer is not GC'd.
    app._autosaxs_sigint_timer = timer  # type: ignore[attr-defined]
    return timer
