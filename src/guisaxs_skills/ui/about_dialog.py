from __future__ import annotations

import sys

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from ..logic.package_update import environment_summary


class AboutDialog(QDialog):
    def __init__(self, *, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("About guisaxs-liveview")
        self.setMinimumWidth(480)

        version, py_exe, location = environment_summary()
        text = (
            "<h2>guisaxs-liveview</h2>"
            "<p>Live SAXS directory watcher and processing dashboard.</p>"
            f"<p><b>autosaxs version:</b> {version}<br>"
            f"<b>Python:</b> {py_exe}<br>"
            f"<b>Install location:</b> {location}</p>"
            "<p>Use <b>Update → Update to latest version…</b> to upgrade "
            "<code>autosaxs[gui]</code> in this environment.</p>"
        )
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(Qt.RichText)
        label.setOpenExternalLinks(False)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(label)
        lay.addWidget(buttons)
