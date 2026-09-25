"""Liveview-like status line + indeterminate progress for long-running work."""

from __future__ import annotations

from PyQt5.QtWidgets import QFrame, QLabel, QProgressBar, QVBoxLayout, QWidget


class RunStatusBar(QWidget):
    """Status text with an indeterminate bar while ``running`` (same pattern as liveview middle)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        frame = QFrame()
        frame.setFrameShape(QFrame.StyledPanel)
        self._status = QLabel("Idle")
        self._status.setWordWrap(True)
        self._bar = QProgressBar()
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._bar.setRange(0, 1)
        self._bar.setValue(0)
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(4)
        lay.addWidget(self._status)
        lay.addWidget(self._bar)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)

    def set_status(self, text: str, *, running: bool | None = None) -> None:
        """Update label; pass ``running`` to toggle the indeterminate bar."""
        self._status.setText(str(text or "").strip() or "—")
        if running is None:
            return
        if running:
            self._bar.setRange(0, 0)
        else:
            self._bar.setRange(0, 1)
            self._bar.setValue(0)

    def set_running(self, running: bool, *, text: str | None = None) -> None:
        if text is not None:
            self.set_status(text, running=running)
        else:
            self.set_status(self._status.text(), running=running)

    def text(self) -> str:
        return self._status.text()
