"""Liveview dual-tab log: Full (skill + app) and App-only."""

from __future__ import annotations

from datetime import datetime

from PyQt5.QtWidgets import QLabel, QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget


class LiveviewLogPanel(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full = QPlainTextEdit()
        self._app = QPlainTextEdit()
        for w in (self._full, self._app):
            w.setReadOnly(True)
            w.setLineWrapMode(QPlainTextEdit.NoWrap)
            f = w.font()
            f.setFamily("monospace")
            w.setFont(f)

        self._tabs = QTabWidget()
        self._tabs.addTab(self._full, "Full")
        self._tabs.addTab(self._app, "App")

        title = QLabel("Logs")
        title.setStyleSheet("font-weight: 600;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(title)
        lay.addWidget(self._tabs, 1)

    def append_skill_stdout(self, text: str, *, skill: str = "") -> None:
        self._append_full(text, prefix=f"[{skill} out] " if skill else "[out] ")

    def append_skill_stderr(self, text: str, *, skill: str = "") -> None:
        self._append_full(text, prefix=f"[{skill} err] " if skill else "[err] ")

    def append_app(self, text: str) -> None:
        line = self._stamp(text)
        self._append_widget(self._full, line)
        self._append_widget(self._app, line)

    def _append_full(self, text: str, *, prefix: str) -> None:
        body = (text or "").rstrip("\n")
        if not body:
            return
        for chunk in body.splitlines() or [""]:
            self._append_widget(self._full, self._stamp(f"{prefix}{chunk}"))

    @staticmethod
    def _stamp(text: str) -> str:
        ts = datetime.now().strftime("%H:%M:%S")
        return f"{ts} {text.rstrip()}"

    @staticmethod
    def _append_widget(widget: QPlainTextEdit, line: str) -> None:
        bar = widget.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        widget.appendPlainText(line)
        if at_bottom:
            bar.setValue(bar.maximum())
