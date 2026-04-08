from __future__ import annotations

from typing import List

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QListWidget, QListWidgetItem, QHBoxLayout, QWidget

from ..core.models import SkillMeta


class CatalogTabs(QWidget):
    skill_selected = pyqtSignal(object)  # SkillMeta

    def __init__(self, *, skills: List[SkillMeta]) -> None:
        super().__init__()
        self._skills = skills
        self._list = QListWidget()

        for meta in skills:
            item = QListWidgetItem(meta.name, self._list)
            tip = meta.doc or meta.summary or meta.name
            # Tooltips should be readable: show the full docstring.
            item.setToolTip(tip)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._list, 0)

        self._list.currentRowChanged.connect(self._on_changed)
        if skills:
            # Default skill on launch.
            default_skill = "calibrate"
            idx = next((i for i, m in enumerate(skills) if m.name == default_skill), 0)
            self._list.setCurrentRow(idx)

    def _on_changed(self, idx: int) -> None:
        if 0 <= idx < len(self._skills):
            self.skill_selected.emit(self._skills[idx])

