from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import pyqtSignal
from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import QFileDialog, QHBoxLayout, QLineEdit, QMessageBox, QPushButton, QWidget
from PyQt5.QtWidgets import QTreeView

from ..logic.autosaxs_cli import run_get_default_config
from ..logic.path_normalize import normalize_pathish
from ..logic.smart_defaults import browse_start_dir_for_resolved_paths


class PathField(QWidget):
    """Emitted when the path text or dropped paths change (not during programmatic set_text / set_state)."""

    path_changed = pyqtSignal()
    load_clicked = pyqtSignal()

    def __init__(
        self,
        *,
        mode: str = "any",
        allow_multiple: bool = False,
        show_get_default: bool = False,
        show_load: bool = False,
        expected_exts: Optional[tuple[str, ...]] = None,
    ) -> None:
        super().__init__()
        self._mode = mode  # any|file|dir
        self._allow_multiple = bool(allow_multiple)
        self._show_get_default = bool(show_get_default)
        self._show_load = bool(show_load)
        self._expected_exts = tuple(x.lower() for x in (expected_exts or ()) if isinstance(x, str) and x.strip())
        self._last_ext_warned_path: Optional[str] = None
        self._last_valid_state: dict = {"text": "", "dropped_paths": []}
        self._browse_start_dir: Optional[str] = None
        self._workdir: Optional[Path] = None
        self._smart_drop_handler: Optional[Callable[[list[str], "PathField"], bool]] = None
        self._dropped_paths: list[str] = []
        self._edit = QLineEdit()
        # Prevent QLineEdit from "inserting text at cursor" on drop; we handle drops at the widget level.
        self._edit.setAcceptDrops(False)
        self._edit.installEventFilter(self)
        self._browse = QPushButton("Browse")
        self._load: Optional[QPushButton] = QPushButton("Load") if self._show_load else None
        self._get_default = QPushButton("Get Default")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._edit, 1)
        lay.addWidget(self._browse, 0)
        if self._load is not None:
            lay.addWidget(self._load, 0)
            self._load.clicked.connect(self.load_clicked.emit)
        if self._show_get_default:
            lay.addWidget(self._get_default, 0)

        self.setAcceptDrops(True)
        self._browse.clicked.connect(self._on_browse)
        self._get_default.clicked.connect(self._on_get_default)
        self._edit.textEdited.connect(self._on_text_edited)
        self._edit.textChanged.connect(lambda _t: self.path_changed.emit())
        self._edit.textChanged.connect(lambda _t: self._maybe_warn_ext_mismatch())

    @property
    def browse_button(self) -> QPushButton:
        return self._browse

    @property
    def load_button(self) -> Optional[QPushButton]:
        return self._load

    def eventFilter(self, obj, event):  # type: ignore[override]
        # Ensure drops on the line edit behave like drops on the whole widget (replace content).
        if obj is self._edit:
            if event.type() in (QEvent.DragEnter, QEvent.DragMove):
                md = event.mimeData()
                if md is not None and md.hasUrls():
                    event.acceptProposedAction()
                    return True
            if event.type() == QEvent.Drop:
                md = event.mimeData()
                if md is not None and md.hasUrls():
                    urls = md.urls()
                    paths = [u.toLocalFile() for u in urls if u.toLocalFile()]
                    if paths:
                        self._set_dropped_paths(paths)
                    event.acceptProposedAction()
                    return True
        return super().eventFilter(obj, event)


    def set_placeholder(self, text: str) -> None:
        self._edit.setPlaceholderText(text)

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
        self._edit.blockSignals(True)
        try:
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
        finally:
            self._edit.blockSignals(False)
        # Do not validate+warn when restoring programmatic state, but keep a baseline "valid" snapshot.
        self._last_valid_state = self.state()

    def set_text(self, value: str) -> None:
        self._dropped_paths = []
        self._edit.blockSignals(True)
        try:
            self._edit.setText(value)
        finally:
            self._edit.blockSignals(False)
        # Programmatic set: treat as baseline (smart defaults / saved state).
        self._last_valid_state = self.state()

    def set_browse_start_dir(self, path: Optional[str]) -> None:
        """Fallback directory for the file dialog when the field has no resolved paths (e.g. session hints)."""
        self._browse_start_dir = path.strip() if path else None

    def set_workdir(self, workdir: Optional[Path]) -> None:
        """Used to resolve relative paths when choosing the dialog start directory."""
        self._workdir = workdir

    def set_smart_drop_handler(
        self, handler: Optional[Callable[[list[str], "PathField"], bool]]
    ) -> None:
        """
        Optional drop router. If the handler returns True, the drop was consumed elsewhere
        (e.g. routed to a sibling field with matching expected extensions).
        """
        self._smart_drop_handler = handler

    def _config_default_dest_path(self) -> Path:
        wd = self._workdir.resolve() if self._workdir is not None else Path.cwd().resolve()
        return wd / "config_base.conf"

    def _on_get_default(self) -> None:
        dest = self._config_default_dest_path()
        if dest.exists():
            resp = QMessageBox.question(
                self,
                "Overwrite config?",
                f"Overwrite existing file?\n\n{str(dest)}",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if resp != QMessageBox.Yes:
                return
        out_dir = dest.parent
        self._get_default.setEnabled(False)
        try:
            written = run_get_default_config(output_dir=out_dir)
        except Exception as e:
            QMessageBox.critical(self, "Cannot write config", str(e))
            return
        finally:
            self._get_default.setEnabled(True)

        self._dropped_paths = []
        self._edit.setText(str(written))
        self._maybe_warn_ext_mismatch(force=True)

    def _dialog_start_directory(self) -> str:
        if self._workdir is not None:
            paths = [normalize_pathish(p) for p in self.paths() if normalize_pathish(p)]
            derived = browse_start_dir_for_resolved_paths(paths, self._workdir)
            if derived:
                return derived
            return str(self._workdir.resolve())
        if self._browse_start_dir:
            return self._browse_start_dir
        return os.getcwd()

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
        self._set_dropped_paths(paths)

    def _set_dropped_paths(self, paths: list[str]) -> None:
        if self._smart_drop_handler is not None and self._smart_drop_handler(paths, self):
            return
        # Replace any existing content with the dropped list.
        self._dropped_paths = list(paths)
        self._sync_display_from_dropped()
        self._maybe_warn_ext_mismatch(force=True)

    def _on_browse(self) -> None:
        start = self._dialog_start_directory()
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
                    self._maybe_warn_ext_mismatch(force=True)
                    return
                path = selected[0] if selected else ""
        if path:
            self._dropped_paths = []
            self._edit.setText(path)
            self._maybe_warn_ext_mismatch(force=True)

    def _on_text_edited(self, _text: str) -> None:
        # If the user starts typing, treat it as manual entry and drop any stored multi-drop list.
        if self._dropped_paths:
            self._dropped_paths = []
        self._maybe_warn_ext_mismatch()

    def _restore_last_valid_state(self) -> None:
        st = dict(self._last_valid_state or {})
        self._edit.blockSignals(True)
        try:
            self._dropped_paths = list(st.get("dropped_paths") or [])
            self._edit.setText((st.get("text") or "").strip())
        finally:
            self._edit.blockSignals(False)

    def _maybe_warn_ext_mismatch(self, *, force: bool = False) -> None:
        if not self._expected_exts:
            return
        # Multi-file: validate dropped paths / multi-browse selection (exact file list only).
        if self._allow_multiple:
            paths = list(self._dropped_paths)
            if not paths:
                return
            bad: Optional[tuple[str, str]] = None  # (path, actual_suffix)
            for raw in paths:
                p = Path(normalize_pathish(raw)).expanduser()
                try:
                    if not p.is_file():
                        continue
                except OSError:
                    continue
                actual = p.suffix.lower()
                if actual not in self._expected_exts:
                    bad = (str(p), actual)
                    break
            if bad is None:
                self._last_ext_warned_path = None
                self._last_valid_state = self.state()
                return
            sp, actual = bad
            if not force and self._last_ext_warned_path == sp:
                return
            self._last_ext_warned_path = sp
            exp = " or ".join(self._expected_exts)
            QMessageBox.warning(self, "Unexpected file type", f"{exp} is accepted, but the uploaded file is {actual}")
            self._restore_last_valid_state()
            return
        t = normalize_pathish(self.text())
        if not t:
            # Empty is always ok; update baseline.
            self._last_valid_state = self.state()
            return
        p = Path(t).expanduser()
        try:
            if not p.is_file():
                return
        except OSError:
            return
        actual = p.suffix.lower()
        if actual in self._expected_exts:
            self._last_ext_warned_path = None
            self._last_valid_state = self.state()
            return
        sp = str(p)
        if not force and self._last_ext_warned_path == sp:
            return
        self._last_ext_warned_path = sp
        exp = " or ".join(self._expected_exts)
        QMessageBox.warning(self, "Unexpected file type", f"{exp} is accepted, but the uploaded file is {actual}")
        # Enforce: revert to the previous valid content (or clear if none).
        self._restore_last_valid_state()

    def _sync_display_from_dropped(self) -> None:
        if not self._dropped_paths:
            return
        if len(self._dropped_paths) == 1:
            self._edit.setText(self._dropped_paths[0])
            return
        first = Path(self._dropped_paths[0]).name
        self._edit.setText(f"{first} + {len(self._dropped_paths) - 1} more")
