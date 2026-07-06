from __future__ import annotations

import os

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QApplication, QMessageBox, QWidget

from ..logic.package_update import launch_deferred_pip_upgrade
from .toast import ConfirmToast


def request_app_update(*, parent: QWidget) -> None:
    """Ask once, then close the app and run the deferred pip upgrade."""
    toast = ConfirmToast(
        text="Updating the app requires closing it. Continue?",
        parent=parent,
    )

    def on_accept() -> None:
        try:
            launch_deferred_pip_upgrade(parent_pid=os.getpid(), force=False)
        except OSError as exc:
            QMessageBox.critical(
                parent,
                "Update failed",
                f"Could not start the background updater:\n{exc}",
            )
            return
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)

    toast.accepted.connect(on_accept)
    toast.show_centered()
