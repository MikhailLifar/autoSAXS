"""Configuration editor dialog for watchdir hierarchical ``config.conf``."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat
from PyQt5.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from guisaxs_skills.ui.style import COLOR_ACCENT, COLOR_MUTED_TEXT, COLOR_QUALITY_WARN

from ...session.watchdir_config import (
    DEFAULT_CONFIG_SEED_TEXT,
    effective_config_path,
    root_config_path,
    seed_root_config_conf,
    validate_config_text,
    write_config_text,
)


class _YamlHighlighter(QSyntaxHighlighter):
    """Lightweight YAML highlighter: keys, comments, quoted strings."""

    _RE_COMMENT = re.compile(r"#.*$")
    _RE_KEY = re.compile(
        r'^(\s*(?:-\s+)?)("[^"]*"|\'[^\']*\'|[^:#\s][^:#]*?)\s*(:)'
    )
    _RE_STRING = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\'')

    def __init__(self, document) -> None:
        super().__init__(document)
        self._fmt_comment = QTextCharFormat()
        self._fmt_comment.setForeground(QColor(COLOR_MUTED_TEXT))
        self._fmt_key = QTextCharFormat()
        self._fmt_key.setForeground(QColor(COLOR_ACCENT))
        self._fmt_string = QTextCharFormat()
        self._fmt_string.setForeground(QColor(COLOR_QUALITY_WARN))

    def highlightBlock(self, text: str) -> None:  # noqa: N802 - Qt API
        key_m = self._RE_KEY.match(text)
        if key_m is not None:
            self.setFormat(key_m.start(2), key_m.end(2) - key_m.start(2), self._fmt_key)

        for m in self._RE_STRING.finditer(text):
            # Do not restyle the key token itself when it is a quoted key.
            if key_m is not None and m.start() == key_m.start(2) and m.end() == key_m.end(2):
                continue
            self.setFormat(m.start(), m.end() - m.start(), self._fmt_string)

        cm = self._RE_COMMENT.search(text)
        if cm is not None:
            # Skip # inside an earlier quoted string on the same line.
            in_string = False
            for m in self._RE_STRING.finditer(text):
                if m.start() <= cm.start() < m.end():
                    in_string = True
                    break
            if not in_string:
                self.setFormat(cm.start(), cm.end() - cm.start(), self._fmt_comment)


def _monospace_editor() -> QPlainTextEdit:
    editor = QPlainTextEdit()
    editor.setLineWrapMode(QPlainTextEdit.NoWrap)
    font = editor.font()
    font.setFamily("monospace")
    font.setStyleHint(font.Monospace)
    editor.setFont(font)
    _YamlHighlighter(editor.document())
    return editor


class ConfigurationDialog(QDialog):
    """
    Comment-preserving text editor for ``config.conf``.

    Loads raw file text; Save validates YAML + ``buffer_rules`` schema, then
    writes the edited text unchanged (comments/order preserved).
    """

    def __init__(
        self,
        *,
        watchdir: Path,
        config_path: Optional[Path] = None,
        parent: QWidget | None = None,
        on_saved: Optional[Callable[[Path, str], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set configuration")
        self.setModal(True)
        self._watchdir = watchdir.expanduser().resolve()
        seed_root_config_conf(self._watchdir)
        self._path = (config_path or root_config_path(self._watchdir)).expanduser().resolve()
        self._on_saved = on_saved
        self._saved = False

        self._path_label = QLabel()
        self._path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._path_label.setWordWrap(True)

        self._editor = _monospace_editor()

        self._error = QLabel("")
        self._error.setWordWrap(True)
        self._error.setStyleSheet("color: #b00020;")
        self._error.setVisible(False)

        btns = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        btns.accepted.connect(self._on_save)
        btns.rejected.connect(self.reject)

        open_root = QPushButton("Open root config.conf")
        open_root.clicked.connect(self._open_root)

        top = QHBoxLayout()
        top.addWidget(self._path_label, 1)
        top.addWidget(open_root, 0)

        lay = QVBoxLayout(self)
        hint = QLabel(
            "Map sample filename patterns to buffer patterns under buffer_rules. "
            "Empty right-hand side means no buffer. First matching rule wins. "
            "Comments are kept — Save writes your text as typed."
        )
        hint.setWordWrap(True)
        lay.addWidget(hint)
        lay.addLayout(top)
        lay.addWidget(self._editor, 1)
        lay.addWidget(self._error)
        lay.addWidget(btns)

        self.resize(720, 520)
        self._load_path(self._path)

    @property
    def config_path(self) -> Path:
        return self._path

    def saved(self) -> bool:
        return self._saved

    def _rel_label(self, path: Path) -> str:
        try:
            return path.relative_to(self._watchdir).as_posix()
        except ValueError:
            return str(path)

    def _load_path(self, path: Path) -> None:
        self._path = path.expanduser().resolve()
        self._path_label.setText(f"File: {self._rel_label(self._path)}")
        if self._path.is_file():
            text = self._path.read_text(encoding="utf-8", errors="replace")
        else:
            text = DEFAULT_CONFIG_SEED_TEXT
        self._editor.setPlainText(text)
        self._error.setVisible(False)

    def _open_root(self) -> None:
        self._load_path(root_config_path(self._watchdir))

    def load_for_sample(self, sample_path: str | Path) -> None:
        path = effective_config_path(sample_path, self._watchdir)
        if path is not None:
            self._load_path(path)

    def _on_save(self) -> None:
        text = self._editor.toPlainText()
        err = validate_config_text(text)
        if err:
            self._error.setText(err)
            self._error.setVisible(True)
            QMessageBox.warning(self, "Invalid configuration", err)
            return
        self._error.setVisible(False)
        if self._on_saved is not None:
            self._on_saved(self._path, text)
        else:
            write_config_text(self._path, text)
        self._saved = True
        self.accept()


class ConfigurationConflictDialog(QDialog):
    """ApplicationModal conflict loop: diagnostics + Configuration editor + Retry."""

    def __init__(
        self,
        *,
        diagnostics: str,
        watchdir: Path,
        config_path: Optional[Path] = None,
        retry_check: Callable[[], object | None],
        on_saved: Optional[Callable[[Path, str], None]] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Buffer rules conflict")
        self.setWindowModality(Qt.ApplicationModal)
        self.setModal(True)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowCloseButtonHint)
        self._retry_check = retry_check
        self._resolved = False
        self._on_saved = on_saved

        self._watchdir = watchdir.expanduser().resolve()
        seed_root_config_conf(self._watchdir)
        self._path = (config_path or root_config_path(self._watchdir)).resolve()

        self._diag = QLabel(diagnostics or "Buffer rules conflict.")
        self._diag.setWordWrap(True)
        self._diag.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self._editor_widget = _monospace_editor()
        if self._path.is_file():
            self._editor_widget.setPlainText(
                self._path.read_text(encoding="utf-8", errors="replace")
            )
        else:
            self._editor_widget.setPlainText(DEFAULT_CONFIG_SEED_TEXT)

        self._error = QLabel("")
        self._error.setStyleSheet("color: #b00020;")
        self._error.setWordWrap(True)
        self._error.setVisible(False)

        retry = QPushButton("Save & Retry")
        retry.clicked.connect(self._on_retry)

        lay = QVBoxLayout(self)
        lay.addWidget(self._diag)
        lay.addWidget(QLabel(f"Editing: {self._path.name}"))
        lay.addWidget(self._editor_widget, 1)
        lay.addWidget(self._error)
        lay.addWidget(retry)
        self.resize(720, 560)

    def resolved(self) -> bool:
        return self._resolved

    def _on_retry(self) -> None:
        text = self._editor_widget.toPlainText()
        err = validate_config_text(text)
        if err:
            self._error.setText(err)
            self._error.setVisible(True)
            return
        self._error.setVisible(False)
        if self._on_saved is not None:
            self._on_saved(self._path, text)
        else:
            write_config_text(self._path, text)
        conflict = self._retry_check()
        if conflict is not None:
            diagnostics = getattr(conflict, "diagnostics", None) or str(conflict)
            self._diag.setText(str(diagnostics))
            return
        self._resolved = True
        self.accept()

    def reject(self) -> None:
        if not self._resolved:
            return
        super().reject()
