from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt5.QtWidgets import QFileDialog, QMessageBox, QWidget
from PyQt5.QtWidgets import QTreeView


ENABLE_NONEMPTY_WORKDIR_WARNING = False


def _last_workdir_path() -> Path:
    # Store state straight inside the installed package directory.
    # This is intentionally "local temp state" rather than user config.
    return Path(__file__).resolve().parents[1] / ".last_workdir.txt"


def load_last_workdir() -> Optional[str]:
    try:
        p = _last_workdir_path()
        if not p.exists():
            return None
        text = p.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return None
        path = os.path.abspath(text)
        if not os.path.isdir(path):
            return None
        if not os.access(path, os.W_OK):
            return None
        return path
    except Exception:
        return None


def save_last_workdir(path: str) -> None:
    try:
        p = _last_workdir_path()
        p.write_text(str(path).strip() + "\n", encoding="utf-8")
    except Exception:
        # Best-effort persistence; never fail app flow.
        return


def select_workdir(parent: Optional[QWidget], *, initial_directory: Optional[str] = None) -> Optional[str]:
    dlg = QFileDialog(parent, "Select working directory")
    dlg.setFileMode(QFileDialog.Directory)
    dlg.setOption(QFileDialog.ShowDirsOnly, True)
    dlg.setOption(QFileDialog.DontUseNativeDialog, True)
    dlg.setViewMode(QFileDialog.Detail)
    dlg.setMinimumSize(980, 720)
    dlg.resize(1100, 760)
    if initial_directory:
        d = os.path.abspath(initial_directory.strip())
        if os.path.isdir(d):
            dlg.setDirectory(d)

    view = dlg.findChild(QTreeView)
    if view is not None and view.header() is not None:
        view.header().setStretchLastSection(False)
        view.header().setSectionResizeMode(view.header().Interactive)
        view.header().resizeSection(0, 520)  # Name
        view.header().resizeSection(1, 70)
        view.header().resizeSection(2, 120)
        view.header().resizeSection(3, 140)  # Modified

    path = ""
    if dlg.exec_():
        selected = dlg.selectedFiles()
        path = selected[0] if selected else ""
    if not path:
        return None
    path = os.path.abspath(path)
    if not os.path.isdir(path):
        QMessageBox.critical(parent, "Invalid directory", f"Not a directory:\n{path}")
        return None
    if not os.access(path, os.W_OK):
        QMessageBox.critical(parent, "Not writable", f"Directory is not writable:\n{path}")
        return None

    if ENABLE_NONEMPTY_WORKDIR_WARNING and os.listdir(path):
        QMessageBox.warning(
            parent,
            "Non-empty working directory",
            "The selected working directory is not empty.\n\n"
            "When `use_cache=False`, some skills may overwrite existing outputs.\n"
            "When `use_cache=True`, cache hits may avoid recomputation.\n",
        )
    return path

