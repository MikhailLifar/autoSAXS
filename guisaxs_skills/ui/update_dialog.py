from __future__ import annotations

import os

from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..logic.package_update import (
    LIVEVIEW_UPDATE_SPEC,
    is_editable_install,
    launch_deferred_pip_upgrade,
)


class UpdateDialog(QDialog):
    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Update autosaxs")
        self.setMinimumWidth(520)
        self._update_scheduled = False

        intro = QLabel(
            "This will upgrade <b>autosaxs[gui]</b> in the current Python environment "
            "from the project git <code>main</code> branch.<br><br>"
            f"<code>{LIVEVIEW_UPDATE_SPEC}</code><br><br>"
            "The application will <b>close</b> and a small updater window will run "
            "<code>pip</code> after it exits. When the update finishes, a notification "
            "with the log and an <b>OK</b> button will appear, and "
            "<b>guisaxs-liveview</b> will start again automatically."
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)

        self._btn_run = QPushButton("Update and quit…")
        self._btn_run.clicked.connect(self._start_update)
        self._btn_close = QPushButton("Cancel")
        self._btn_close.clicked.connect(self.reject)

        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self._btn_run)
        row.addWidget(self._btn_close)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(row)

        if is_editable_install():
            QMessageBox.warning(
                self,
                "Editable install",
                "autosaxs appears to be installed in editable mode. "
                "In-app pip upgrade may not replace your working copy. "
                "Consider pulling the git repo and reinstalling manually.",
            )

    def update_scheduled(self) -> bool:
        return self._update_scheduled

    def _start_update(self) -> None:
        answer = QMessageBox.question(
            self,
            "Update autosaxs",
            "guisaxs-liveview will close and install the latest autosaxs[gui] "
            "in the background.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return

        try:
            log_path = launch_deferred_pip_upgrade(parent_pid=os.getpid(), force=False)
        except OSError as exc:
            QMessageBox.critical(
                self,
                "Update failed",
                f"Could not start the background updater:\n{exc}",
            )
            return

        self._update_scheduled = True
        self._btn_run.setEnabled(False)
        QMessageBox.information(
            self,
            "Update scheduled",
            "The application will now close.\n\n"
            "An update window will appear shortly with pip output. "
            "guisaxs-liveview will restart automatically when the update finishes.",
        )
        self.accept()
        app = QApplication.instance()
        if app is not None:
            QTimer.singleShot(0, app.quit)
