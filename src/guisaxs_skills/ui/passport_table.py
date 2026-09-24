"""Shared quality-passport table (Metric / Value) with per-row severity coloring."""

from __future__ import annotations

import html as html_mod
from typing import Iterable, Optional, Sequence, Union

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QAbstractItemView, QHeaderView, QLabel, QTableWidget, QTableWidgetItem

from .style import COLOR_QUALITY_POOR, COLOR_QUALITY_WARN

# Third field: False/"ok" = default, True/"poor" = red, "warn" = amber.
PassportSeverity = Union[bool, str]
PassportRow = tuple[str, str, PassportSeverity]  # metric, value, severity


def _normalize_severity(flag: PassportSeverity) -> str:
    if flag is True or flag == "poor":
        return "poor"
    if flag == "warn":
        return "warn"
    return "ok"


def _severity_color(
    flag: PassportSeverity,
    *,
    poor_color: str,
    warn_color: str,
) -> Optional[str]:
    sev = _normalize_severity(flag)
    if sev == "poor":
        return poor_color
    if sev == "warn":
        return warn_color
    return None


def format_passport_table_html(
    rows: Sequence[PassportRow],
    *,
    poor_color: str = COLOR_QUALITY_POOR,
    warn_color: str = COLOR_QUALITY_WARN,
) -> str:
    """Compact HTML table for embedded analysis panes (QLabel rich text)."""
    if not rows:
        return "—"
    parts = [
        '<table cellspacing="0" cellpadding="2" style="border-collapse:collapse;width:100%;">'
        "<tr>"
        '<th align="left" style="padding:2px 8px 2px 0;border-bottom:1px solid #c8d0da;">Metric</th>'
        '<th align="left" style="padding:2px 0;border-bottom:1px solid #c8d0da;">Value</th>'
        "</tr>",
    ]
    for metric, value, flag in rows:
        color_hex = _severity_color(flag, poor_color=poor_color, warn_color=warn_color)
        color = f' style="color:{color_hex};"' if color_hex else ""
        parts.append(
            "<tr>"
            f"<td{color} style=\"padding:2px 8px 2px 0;vertical-align:top;\">"
            f"{html_mod.escape(metric)}</td>"
            f"<td{color} style=\"padding:2px 0;vertical-align:top;\">"
            f"{html_mod.escape(value)}</td>"
            "</tr>"
        )
    parts.append("</table>")
    return "".join(parts)


class PassportTableWidget(QTableWidget):
    """Two-column Metric / Value table for adjust wizards."""

    def __init__(self, parent=None) -> None:
        super().__init__(0, 2, parent)
        self.setHorizontalHeaderLabels(["Metric", "Value"])
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setFocusPolicy(Qt.NoFocus)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        hdr = self.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.Stretch)
        hdr.setHighlightSections(False)
        self.setWordWrap(True)
        self.setMinimumHeight(120)

    def set_rows(
        self,
        rows: Iterable[PassportRow],
        *,
        poor_color: str = COLOR_QUALITY_POOR,
        warn_color: str = COLOR_QUALITY_WARN,
    ) -> None:
        row_list = list(rows)
        self.setRowCount(0)
        if not row_list:
            self.setRowCount(1)
            empty = QTableWidgetItem("—")
            empty.setFlags(Qt.ItemIsEnabled)
            self.setItem(0, 0, empty)
            self.setSpan(0, 0, 1, 2)
            return
        self.setRowCount(len(row_list))
        for i, (metric, value, flag) in enumerate(row_list):
            m_item = QTableWidgetItem(metric)
            v_item = QTableWidgetItem(value)
            color_hex = _severity_color(flag, poor_color=poor_color, warn_color=warn_color)
            for it in (m_item, v_item):
                it.setFlags(Qt.ItemIsEnabled)
                if color_hex:
                    it.setForeground(QColor(color_hex))
            self.setItem(i, 0, m_item)
            self.setItem(i, 1, v_item)
        self.resizeRowsToContents()

    def set_message(
        self,
        text: str,
        *,
        poor: bool = False,
        poor_color: str = COLOR_QUALITY_POOR,
    ) -> None:
        """Single full-width status cell (errors / placeholders)."""
        self.clearSpans()
        self.setRowCount(1)
        item = QTableWidgetItem(text or "—")
        item.setFlags(Qt.ItemIsEnabled)
        if poor:
            item.setForeground(QColor(poor_color))
        self.setItem(0, 0, item)
        self.setSpan(0, 0, 1, 2)


def apply_passport_html_to_label(
    label: QLabel,
    rows: Sequence[PassportRow],
    *,
    poor_color: str = COLOR_QUALITY_POOR,
    warn_color: str = COLOR_QUALITY_WARN,
) -> None:
    label.setTextFormat(Qt.RichText)
    label.setStyleSheet("")
    label.setText(
        format_passport_table_html(rows, poor_color=poor_color, warn_color=warn_color)
    )
