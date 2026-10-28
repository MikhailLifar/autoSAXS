"""Buffer rules editor widget + dialogs (including ApplicationModal conflict)."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from autosaxs.core.buffer_rules import (
    MatchResult,
    validate_rule_pair,
)


def _fmt_buffer_expr(expr: str) -> str:
    return "(none)" if not str(expr).strip() else str(expr)


class BufferRulesEditorWidget(QWidget):
    """Editable ordered table: sample expr | buffer expr."""

    rules_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Sample expression", "Buffer expression"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.verticalHeader().setVisible(False)

        btn_add = QPushButton("Add")
        btn_rm = QPushButton("Remove")
        btn_up = QPushButton("Up")
        btn_dn = QPushButton("Down")
        btn_add.clicked.connect(self._add_row)
        btn_rm.clicked.connect(self._remove_row)
        btn_up.clicked.connect(lambda: self._move_row(-1))
        btn_dn.clicked.connect(lambda: self._move_row(1))

        btns = QHBoxLayout()
        btns.addWidget(btn_add)
        btns.addWidget(btn_rm)
        btns.addWidget(btn_up)
        btns.addWidget(btn_dn)
        btns.addStretch(1)

        hint = QLabel(
            "Use * wildcards and {capture} tokens. Empty buffer expression = "
            "explicit no-buffer sample (skip subtract, still analyze)."
        )
        hint.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(hint)
        lay.addWidget(self._table, 1)
        lay.addLayout(btns)

    def set_rules(self, rules: Sequence[Tuple[str, str]]) -> None:
        self._table.setRowCount(0)
        for lhs, rhs in rules:
            self._append_row(str(lhs), str(rhs))

    def rules(self) -> List[Tuple[str, str]]:
        out: List[Tuple[str, str]] = []
        for row in range(self._table.rowCount()):
            lhs_item = self._table.item(row, 0)
            rhs_item = self._table.item(row, 1)
            lhs = (lhs_item.text() if lhs_item else "").strip()
            rhs = (rhs_item.text() if rhs_item else "").strip()
            if lhs_item is None and rhs_item is None:
                continue
            out.append((lhs, rhs))
        return out

    def validate(self) -> Optional[str]:
        rules = self.rules()
        if not rules:
            return "At least one buffer rule is required"
        for i, (lhs, rhs) in enumerate(rules):
            err = validate_rule_pair(lhs, rhs)
            if err:
                return f"Row {i + 1}: {err}"
        return None

    def _append_row(self, lhs: str = "", rhs: str = "") -> None:
        r = self._table.rowCount()
        self._table.insertRow(r)
        self._table.setItem(r, 0, QTableWidgetItem(lhs))
        self._table.setItem(r, 1, QTableWidgetItem(rhs))
        self.rules_changed.emit()

    def _add_row(self) -> None:
        self._append_row("*{stem}_sample*", "*{stem}_buffer*")

    def _remove_row(self) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        self._table.removeRow(row)
        self.rules_changed.emit()

    def _move_row(self, delta: int) -> None:
        row = self._table.currentRow()
        if row < 0:
            return
        dest = row + delta
        if dest < 0 or dest >= self._table.rowCount():
            return
        lhs = self._table.item(row, 0).text() if self._table.item(row, 0) else ""
        rhs = self._table.item(row, 1).text() if self._table.item(row, 1) else ""
        self._table.removeRow(row)
        self._table.insertRow(dest)
        self._table.setItem(dest, 0, QTableWidgetItem(lhs))
        self._table.setItem(dest, 1, QTableWidgetItem(rhs))
        self._table.setCurrentCell(dest, 0)
        self.rules_changed.emit()


class BufferRulesEditorDialog(QDialog):
    """Modeless/modal editor: Apply persists rules via callback."""

    applied = pyqtSignal(object)  # list[tuple[str,str]]

    def __init__(
        self,
        *,
        rules: Sequence[Tuple[str, str]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Buffer rules")
        self.setModal(False)
        self._editor = BufferRulesEditorWidget(self)
        self._editor.set_rules(rules)
        buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Apply).clicked.connect(self._on_apply)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.addWidget(self._editor, 1)
        lay.addWidget(buttons)
        self.resize(560, 360)

    def _on_apply(self) -> None:
        err = self._editor.validate()
        if err:
            QMessageBox.warning(self, "Invalid buffer rules", err)
            return
        self.applied.emit(self._editor.rules())


class BufferRulesConflictDialog(QDialog):
    """
    ApplicationModal conflict loop: edit rules → Retry match.

    No Esc/Cancel dismiss while conflict remains. Caller supplies
    ``retry_check(rules) -> MatchResult | None`` returning a still-conflicting
    result or ``None`` when clean.
    """

    def __init__(
        self,
        *,
        diagnostics: str,
        rules: Sequence[Tuple[str, str]],
        retry_check,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Buffer rules conflict")
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowFlags(
            (self.windowFlags() | Qt.WindowTitleHint)
            & ~Qt.WindowCloseButtonHint
            & ~Qt.WindowContextHelpButtonHint
        )
        self._retry_check = retry_check
        self._resolved_rules: Optional[List[Tuple[str, str]]] = None

        self._diag = QLabel(diagnostics or "Buffer rules conflict.")
        self._diag.setWordWrap(True)
        self._diag.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self._editor = BufferRulesEditorWidget(self)
        self._editor.set_rules(rules)

        self._btn_retry = QPushButton("Retry match")
        self._btn_retry.clicked.connect(self._on_retry)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Processing is paused until buffer rules resolve:"))
        lay.addWidget(self._diag)
        lay.addWidget(self._editor, 1)
        lay.addWidget(self._btn_retry)
        self.resize(640, 420)

    def resolved_rules(self) -> Optional[List[Tuple[str, str]]]:
        return self._resolved_rules

    def reject(self) -> None:  # noqa: D401 — block Esc dismiss
        """Ignore reject while unresolved (ApplicationModal hard block)."""
        return

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if self._resolved_rules is None:
            event.ignore()
            return
        super().closeEvent(event)

    def _on_retry(self) -> None:
        err = self._editor.validate()
        if err:
            QMessageBox.warning(self, "Invalid buffer rules", err)
            return
        rules = self._editor.rules()
        still = self._retry_check(rules)
        if still is None:
            self._resolved_rules = rules
            self.done(QDialog.Accepted)
            return
        if isinstance(still, MatchResult):
            self._diag.setText(still.diagnostics or "Buffer rules conflict.")
        else:
            self._diag.setText(str(still))


class BufferRulesStrip(QWidget):
    """Compact read-only strip + Edit rules… for the buffer wizard session settings."""

    edit_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Sample", "Buffer"])
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setFocusPolicy(Qt.NoFocus)
        self._table.setMaximumHeight(90)
        self._edit = QPushButton("Edit rules…")
        self._edit.clicked.connect(self.edit_requested.emit)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._table)
        lay.addWidget(self._edit)

    def set_rules(self, rules: Sequence[Tuple[str, str]]) -> None:
        self._table.setRowCount(0)
        for lhs, rhs in list(rules)[:8]:
            r = self._table.rowCount()
            self._table.insertRow(r)
            self._table.setItem(r, 0, QTableWidgetItem(str(lhs)))
            self._table.setItem(r, 1, QTableWidgetItem(_fmt_buffer_expr(rhs)))
