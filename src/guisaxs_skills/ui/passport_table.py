"""Shared quality-passport table (Metric / Value) with per-row severity coloring."""

from __future__ import annotations

import html as html_mod
import re
from typing import Iterable, Optional, Sequence, Union

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import QHeaderView, QLabel, QTableWidget, QTableWidgetItem

from .passport_format import format_metric_label, format_metric_label_html
from .style import COLOR_QUALITY_POOR, COLOR_QUALITY_WARN, configure_readonly_copyable_table

# Third field: False/"ok" = default, True/"poor" = red, "warn" = amber.
PassportSeverity = Union[bool, str]
PassportRow = tuple[str, str, PassportSeverity]  # metric, value, severity

_TAG_RE = re.compile(r"<[^>]+>")
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_TD_RE = re.compile(r"<td([^>]*)>(.*?)</td>", re.IGNORECASE | re.DOTALL)


def _escape_passport_text(text: str) -> str:
    """HTML-escape passport cell text (metric values; metrics use format_metric_label_html)."""
    return html_mod.escape(str(text))


def _strip_html_tags(fragment: str) -> str:
    return html_mod.unescape(_TAG_RE.sub("", str(fragment or ""))).strip()


def rows_from_passport_html(
    html_text: str,
    *,
    poor_color: str = COLOR_QUALITY_POOR,
    warn_color: str = COLOR_QUALITY_WARN,
) -> list[PassportRow]:
    """
    Recover Metric/Value rows from ``format_passport_table_html`` output.

    Prefer passing a quality dict into ``set_rows`` / ``set_passport_from_quality``.
    This exists so legacy HTML handoffs never collapse into one full-width status cell.
    """
    text = str(html_text or "")
    if "<table" not in text.lower():
        return []
    rows: list[PassportRow] = []
    for tr_body in _TR_RE.findall(text):
        if "<th" in tr_body.lower():
            continue
        cells = _TD_RE.findall(tr_body)
        if len(cells) < 2:
            continue
        attrs0, metric_html = cells[0]
        attrs1, value_html = cells[1]
        metric = _strip_html_tags(metric_html)
        value = _strip_html_tags(value_html)
        if not metric:
            continue
        style = f"{attrs0} {attrs1}"
        severity: PassportSeverity = "ok"
        if poor_color and poor_color in style:
            severity = "poor"
        elif warn_color and warn_color in style:
            severity = "warn"
        rows.append((metric, value, severity))
    return rows


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
            f"{format_metric_label_html(metric)}</td>"
            f"<td{color} style=\"padding:2px 0;vertical-align:top;\">"
            f"{_escape_passport_text(value)}</td>"
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
        configure_readonly_copyable_table(self)
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
            empty.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            self.setItem(0, 0, empty)
            self.setSpan(0, 0, 1, 2)
            return
        self.setRowCount(len(row_list))
        for i, (metric, value, flag) in enumerate(row_list):
            m_item = QTableWidgetItem(format_metric_label(metric))
            v_item = QTableWidgetItem(value)
            color_hex = _severity_color(flag, poor_color=poor_color, warn_color=warn_color)
            for it in (m_item, v_item):
                it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
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
        """
        Single full-width status cell (short errors / placeholders only).

        Do not use this for quality-gate results (FAILED / ACCEPTABLE / …): those
        must go through ``set_rows`` so Metric/Value structure is preserved.
        """
        self.clearSpans()
        self.setRowCount(1)
        item = QTableWidgetItem(text or "—")
        item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        if poor:
            item.setForeground(QColor(poor_color))
        self.setItem(0, 0, item)
        self.setSpan(0, 0, 1, 2)

    def set_from_html_or_message(
        self,
        html_text: str = "",
        *,
        text: str = "",
        poor: bool = False,
        poor_color: str = COLOR_QUALITY_POOR,
        warn_color: str = COLOR_QUALITY_WARN,
    ) -> None:
        """Prefer HTML table → rows; otherwise a short plain ``text`` message."""
        rows = rows_from_passport_html(html_text, poor_color=poor_color, warn_color=warn_color)
        if rows:
            self.set_rows(rows, poor_color=poor_color, warn_color=warn_color)
            return
        if text:
            self.set_message(text, poor=poor, poor_color=poor_color)
            return
        if html_text:
            # Non-table HTML: keep a short plain hint, never a Metric/Value blob.
            plain = " ".join(_strip_html_tags(html_text).split())
            if plain and plain != "—" and len(plain) <= 120 and "Metric" not in plain:
                self.set_message(plain, poor=poor or (poor_color in html_text), poor_color=poor_color)
                return
        self.set_rows([])


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
