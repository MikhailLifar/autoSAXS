from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget


class ArtifactsPanel(QWidget):
    artifact_selected = pyqtSignal(str)  # path

    def __init__(self) -> None:
        super().__init__()
        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["role", "path"])
        self._tree.itemSelectionChanged.connect(self._on_sel)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._tree)

    def set_result(self, result: Dict[str, Any]) -> None:
        self._tree.clear()
        for role, val in (result or {}).items():
            if isinstance(val, str):
                self._add_item(role, val)
            elif isinstance(val, list) and all(isinstance(x, str) for x in val):
                parent = QTreeWidgetItem([role, ""])
                self._tree.addTopLevelItem(parent)
                for p in val:
                    child = QTreeWidgetItem(["", p])
                    parent.addChild(child)

        self._tree.expandAll()

    def _add_item(self, role: str, path: str) -> None:
        item = QTreeWidgetItem([role, path])
        if path and not os.path.exists(path):
            item.setToolTip(1, "Missing on disk")
        self._tree.addTopLevelItem(item)

    def _on_sel(self) -> None:
        items = self._tree.selectedItems()
        if not items:
            return
        item = items[0]
        path = item.text(1).strip()
        if path:
            self.artifact_selected.emit(path)

