from __future__ import annotations

from PyQt5.QtWidgets import QPlainTextEdit, QTabWidget, QWidget, QVBoxLayout


class LogView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._tabs = QTabWidget()
        self._stdout = QPlainTextEdit()
        self._stderr = QPlainTextEdit()
        for w in (self._stdout, self._stderr):
            w.setReadOnly(True)

        self._tabs.addTab(self._stdout, "stdout")
        self._tabs.addTab(self._stderr, "stderr")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._tabs)

    def clear(self) -> None:
        self._stdout.clear()
        self._stderr.clear()

    def append_stdout(self, text: str) -> None:
        self._stdout.appendPlainText(text.rstrip("\n"))

    def append_stderr(self, text: str) -> None:
        self._stderr.appendPlainText(text.rstrip("\n"))

