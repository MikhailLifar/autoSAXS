from __future__ import annotations

from typing import Callable, Dict, List, Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QListWidget, QListWidgetItem, QHBoxLayout, QWidget

from ..core.models import SkillMeta


class CatalogTabs(QWidget):
    skill_selected = pyqtSignal(object)  # SkillMeta

    def __init__(
        self,
        *,
        skill_names: List[str],
        load_meta: Callable[[str], SkillMeta],
    ) -> None:
        super().__init__()
        self._names = list(skill_names)
        self._load_meta = load_meta
        self._metas: Dict[str, SkillMeta] = {}
        self._list = QListWidget()

        for name in self._names:
            item = QListWidgetItem(name, self._list)
            # Full docstring loads with the skill body; name-only until first select.
            item.setToolTip(name)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._list, 0)

        self._list.currentRowChanged.connect(self._on_changed)
        if self._names:
            # Default skill on launch.
            default_skill = "calibrate"
            idx = next((i for i, n in enumerate(self._names) if n == default_skill), 0)
            self._list.setCurrentRow(idx)

    def _meta_for_index(self, idx: int) -> Optional[SkillMeta]:
        if not (0 <= idx < len(self._names)):
            return None
        name = self._names[idx]
        meta = self._metas.get(name)
        if meta is None:
            meta = self._load_meta(name)
            self._metas[name] = meta
            item = self._list.item(idx)
            if item is not None:
                tip = meta.doc or meta.summary or meta.name
                item.setToolTip(tip)
        return meta

    def select_skill(self, name: str) -> None:
        idx = next((i for i, n in enumerate(self._names) if n == name), None)
        if idx is None:
            idx = 0 if self._names else -1
        if idx >= 0:
            self._list.setCurrentRow(idx)

    def current_skill(self) -> SkillMeta | None:
        return self._meta_for_index(self._list.currentRow())

    def _on_changed(self, idx: int) -> None:
        meta = self._meta_for_index(idx)
        if meta is not None:
            self.skill_selected.emit(meta)
