from __future__ import annotations

from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class RunControls(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._run = QPushButton("Run")
        self._cancel = QPushButton("Cancel")
        self._cancel.setEnabled(False)
        self._copy_cli = QPushButton("Copy CLI")
        self._state = QLabel("Idle")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._run)
        lay.addWidget(self._cancel)
        lay.addWidget(self._copy_cli)
        lay.addWidget(self._state, 1)

    @property
    def run_button(self) -> QPushButton:
        return self._run

    @property
    def cancel_button(self) -> QPushButton:
        return self._cancel

    @property
    def copy_cli_button(self) -> QPushButton:
        return self._copy_cli

    def set_running(self, running: bool) -> None:
        self._run.setEnabled(not running)
        self._cancel.setEnabled(running)
        self._state.setText("Running" if running else "Idle")

