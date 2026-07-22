from __future__ import annotations

from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class SkillHeader(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._name = QLabel("No skill selected")
        self._help = QPushButton("?")
        self._help.setObjectName("helpButton")
        self._help.setFixedSize(22, 22)
        self._help.setToolTip("Show skill help")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._name, 1)
        lay.addWidget(self._help, 0)

    @property
    def help_button(self) -> QPushButton:
        return self._help

    def set_skill_name(self, name: str) -> None:
        self._name.setText(name)

