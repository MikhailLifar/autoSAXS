from __future__ import annotations

import os
from pathlib import Path

from PyQt5.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QPushButton, QWidget
from PyQt5.QtWidgets import QTreeView


class PathField(QWidget):
    def __init__(self, *, mode: str = "any", allow_multiple: bool = False) -> None:
        super().__init__()
        self._mode = mode  # any|file|dir
        self._allow_multiple = bool(allow_multiple)
        self._dropped_paths: list[str] = []
        self._edit = QLineEdit()
        self._browse = QPushButton("Browse")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._edit, 1)
        lay.addWidget(self._browse, 0)

        self.setAcceptDrops(True)
        self._browse.clicked.connect(self._on_browse)
        self._edit.textEdited.connect(self._on_text_edited)

    def text(self) -> str:
        return self._edit.text().strip()

    def paths(self) -> list[str]:
        """
        Return the exact file paths captured by multi-file drag-and-drop.
        If the user typed manually or selected via Browse, this returns [text] (if non-empty).
        """
        if self._dropped_paths:
            return list(self._dropped_paths)
        t = self.text()
        if not t:
            return []
        split = self._split_multi_path_text(t)
        return split if split else [t]

    @staticmethod
    def _split_multi_path_text(text: str) -> list[str]:
        """
        Some drag sources / clipboard pastes can yield multiple file URIs/paths in one string.
        Try to split them into individual items.

        Supported inputs:
        - Standard 'text/uri-list' style: one URI/path per line.
        - Concatenated URIs like: '/a.datfile:///b.datfile:///c.dat'
        """
        s = (text or "").strip()
        if not s:
            return []

        # First handle common newline/whitespace separated lists.
        # (QLineEdit won't accept newlines, but pastes/drag text can still include them transiently.)
        if any(ch in s for ch in ("\n", "\r", "\t")):
            parts = [p.strip() for p in s.replace("\r", "\n").replace("\t", "\n").split("\n")]
            parts = [p for p in parts if p]
            return parts

        # Handle concatenated file URIs: "...file:///...file:///..."
        if "file://" in s[1:]:
            out: list[str] = []
            # Split but keep markers by re-attaching 'file://' to subsequent segments.
            chunks = s.split("file://")
            first = chunks[0].strip()
            if first:
                out.append(first)
            for c in chunks[1:]:
                c = c.strip()
                if not c:
                    continue
                out.append("file://" + c)
            return out

        return []

    def state(self) -> dict:
        return {"text": self.text(), "dropped_paths": list(self._dropped_paths), "mode": self._mode}

    def set_state(self, state: dict) -> None:
        self._dropped_paths = list(state.get("dropped_paths") or [])
        t = (state.get("text") or "").strip()
        if self._dropped_paths:
            if len(self._dropped_paths) == 1:
                self._edit.setText(self._dropped_paths[0])
            else:
                first = Path(self._dropped_paths[0]).name
                self._edit.setText(f"{first} + {len(self._dropped_paths) - 1} more")
        else:
            self._edit.setText(t)

    def set_text(self, value: str) -> None:
        self._dropped_paths = []
        self._edit.setText(value)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        urls = event.mimeData().urls()
        if not urls:
            return
        paths = [u.toLocalFile() for u in urls if u.toLocalFile()]
        if not paths:
            return

        self._dropped_paths = list(paths)
        self._sync_display_from_dropped()

    def _on_browse(self) -> None:
        start = self.text() or os.getcwd()
        if self._mode == "dir":
            path = QFileDialog.getExistingDirectory(self, "Select directory", start)
        else:
            dlg = QFileDialog(self, "Select file", start)
            dlg.setFileMode(QFileDialog.ExistingFiles if self._allow_multiple else QFileDialog.ExistingFile)
            dlg.setOption(QFileDialog.DontUseNativeDialog, True)
            # Force details view so the header exists
            dlg.setViewMode(QFileDialog.Detail)
            dlg.setMinimumSize(980, 720)
            dlg.resize(1100, 760)
            # Make columns adjustable
            view = dlg.findChild(QTreeView)
            if view is not None and view.header() is not None:
                view.header().setStretchLastSection(False)
                view.header().setSectionResizeMode(view.header().Interactive)
                view.header().resizeSection(0, 520)  # Name
                view.header().resizeSection(1, 70)   # Size/Ext (varies)
                view.header().resizeSection(2, 120)  # Type/Ext (varies)
                view.header().resizeSection(3, 140)  # Modified
            path = ""
            if dlg.exec_():
                selected = dlg.selectedFiles()
                if self._allow_multiple and len(selected) > 1:
                    self._dropped_paths = list(selected)
                    self._sync_display_from_dropped()
                    return
                path = selected[0] if selected else ""
        if path:
            self._dropped_paths = []
            self._edit.setText(path)

    def _on_text_edited(self, _text: str) -> None:
        # If the user starts typing, treat it as manual entry and drop any stored multi-drop list.
        if self._dropped_paths:
            self._dropped_paths = []

    def _sync_display_from_dropped(self) -> None:
        if not self._dropped_paths:
            return
        if len(self._dropped_paths) == 1:
            self._edit.setText(self._dropped_paths[0])
            return
        first = Path(self._dropped_paths[0]).name
        self._edit.setText(f"{first} + {len(self._dropped_paths) - 1} more")
