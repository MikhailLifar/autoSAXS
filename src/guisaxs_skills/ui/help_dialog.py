from __future__ import annotations

from PyQt5.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout


class HelpDialog(QDialog):
    def __init__(self, *, title: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(640, 480)

        self._edit = QPlainTextEdit()
        self._edit.setReadOnly(True)
        self._edit.setPlainText(text or "")

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        lay = QVBoxLayout(self)
        lay.addWidget(self._edit, 1)
        lay.addWidget(buttons, 0)

