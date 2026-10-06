from __future__ import annotations

from PyQt5.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PyQt5.QtGui import QColor, QFont, QKeySequence, QPainter, QPalette, QPolygon
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QLabel,
    QMenu,
    QProxyStyle,
    QStyle,
    QStyleFactory,
    QTableWidget,
)

from ..core.settings import (
    DEFAULT_FONT_POINT_SIZE,
    FONT_POINT_SIZE_MAX,
    FONT_POINT_SIZE_MIN,
    KEY_FONT_POINT_SIZE,
    settings,
)

COLOR_MUTED_TEXT = "#728195"
COLOR_REQUIRED_STAR = "#ff4d4f"
# Same red as required-field star — poor fit / data-quality hints in analysis panes.
COLOR_QUALITY_POOR = COLOR_REQUIRED_STAR
# Caution / mid-tier quality (readable amber on light UI backgrounds).
COLOR_QUALITY_WARN = "#b45309"
# Primary accent (Highlight / AttentionPulse / in-progress sweep).
COLOR_ACCENT = "#4c8dff"

_SELECTABLE_LABELS_FILTER_ATTR = "_autosaxs_selectable_labels_filter"
_COPYABLE_TABLES_FILTER_ATTR = "_autosaxs_copyable_tables_filter"
_COPYABLE_TABLE_ATTR = "_autosaxs_copyable_table"
_SPIN_ARROW = QColor("#e7eef6")


def clamp_font_point_size(point_size: int | float | str | None) -> int:
    """Clamp a raw settings/UI value to the supported font point-size range."""
    try:
        size = int(float(point_size))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        size = DEFAULT_FONT_POINT_SIZE
    return max(FONT_POINT_SIZE_MIN, min(FONT_POINT_SIZE_MAX, size))


def load_font_point_size() -> int:
    """Read persisted font size from the shared settings store."""
    raw = settings().value(KEY_FONT_POINT_SIZE, DEFAULT_FONT_POINT_SIZE)
    return clamp_font_point_size(raw)


def save_font_point_size(point_size: int) -> int:
    """Persist font size and return the clamped value that was stored."""
    size = clamp_font_point_size(point_size)
    s = settings()
    s.setValue(KEY_FONT_POINT_SIZE, size)
    s.sync()
    return size


def apply_font_point_size(
    app: QApplication | None = None,
    point_size: int | None = None,
    *,
    persist: bool = False,
) -> int:
    """
    Apply app-wide Qt font size (menus, labels, buttons, forms, panels).

    This is the SSOT entry point for appearance font size. Matplotlib/custom
    painters that hardcode point sizes are out of scope.
    """
    size = clamp_font_point_size(point_size if point_size is not None else load_font_point_size())
    if persist:
        size = save_font_point_size(size)
    target = app if app is not None else QApplication.instance()
    if target is not None:
        font = QFont(target.font())
        font.setPointSize(size)
        target.setFont(font)
    return size


class _BrightSpinArrowStyle(QProxyStyle):
    """Paint light spin-box chevrons; Fusion stylesheet ``::*-arrow`` images are ignored."""

    def drawPrimitive(self, element, option, painter, widget=None):  # noqa: N802
        if element in (QStyle.PE_IndicatorSpinUp, QStyle.PE_IndicatorSpinDown):
            self._draw_spin_arrow(element == QStyle.PE_IndicatorSpinUp, option, painter)
            return
        super().drawPrimitive(element, option, painter, widget)

    @staticmethod
    def _draw_spin_arrow(up: bool, option, painter: QPainter) -> None:
        r: QRect = option.rect
        if r.width() < 4 or r.height() < 3:
            return
        cx = r.center().x()
        cy = r.center().y()
        half_w = max(3, min(4, r.width() // 2 - 1))
        half_h = max(2, min(3, r.height() // 2 - 1))
        if up:
            pts = [
                QPoint(cx, cy - half_h),
                QPoint(cx - half_w, cy + half_h),
                QPoint(cx + half_w, cy + half_h),
            ]
        else:
            pts = [
                QPoint(cx, cy + half_h),
                QPoint(cx - half_w, cy - half_h),
                QPoint(cx + half_w, cy - half_h),
            ]
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        painter.setBrush(_SPIN_ARROW)
        painter.drawPolygon(QPolygon(pts))
        painter.restore()


class _SelectableLabelsFilter(QObject):
    """Enable mouse select-copy on every QLabel as it is polished by Qt."""

    def eventFilter(self, obj, event):  # noqa: N802 - Qt naming
        if event.type() == QEvent.Polish and isinstance(obj, QLabel):
            flags = obj.textInteractionFlags()
            if not (flags & Qt.TextSelectableByMouse):
                obj.setTextInteractionFlags(flags | Qt.TextSelectableByMouse)
        return False


def _enable_selectable_labels(app: QApplication) -> None:
    """Install once: all current/future QLabels become mouse-selectable."""
    if getattr(app, _SELECTABLE_LABELS_FILTER_ATTR, None) is not None:
        return
    filt = _SelectableLabelsFilter(app)
    app.installEventFilter(filt)
    setattr(app, _SELECTABLE_LABELS_FILTER_ATTR, filt)


def copy_selected_table_text(table: QTableWidget) -> bool:
    """Copy selected cells (TSV) or the current cell to the clipboard."""
    indexes = table.selectedIndexes()
    if not indexes:
        item = table.currentItem()
        if item is None:
            return False
        QApplication.clipboard().setText(item.text())
        return True
    indexes = sorted(indexes, key=lambda idx: (idx.row(), idx.column()))
    by_row: dict[int, dict[int, str]] = {}
    for idx in indexes:
        item = table.item(idx.row(), idx.column())
        by_row.setdefault(idx.row(), {})[idx.column()] = item.text() if item else ""
    lines = [
        "\t".join(cols[c] for c in sorted(cols))
        for _, cols in sorted(by_row.items())
    ]
    QApplication.clipboard().setText("\n".join(lines))
    return True


def _table_copy_context_menu(table: QTableWidget, pos) -> None:
    menu = QMenu(table)
    act = menu.addAction("Copy")
    act.setShortcut(QKeySequence.Copy)
    chosen = menu.exec_(table.viewport().mapToGlobal(pos))
    if chosen == act:
        copy_selected_table_text(table)


def configure_readonly_copyable_table(table: QTableWidget) -> None:
    """Read-only table: cell selection + Ctrl+C / context-menu Copy."""
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.ContiguousSelection)
    table.setSelectionBehavior(QAbstractItemView.SelectItems)
    table.setFocusPolicy(Qt.ClickFocus)
    if getattr(table, _COPYABLE_TABLE_ATTR, False):
        return
    table.setContextMenuPolicy(Qt.CustomContextMenu)
    table.customContextMenuRequested.connect(
        lambda pos, t=table: _table_copy_context_menu(t, pos)
    )
    setattr(table, _COPYABLE_TABLE_ATTR, True)


class _CopyableTablesFilter(QObject):
    """Upgrade read-only NoSelection tables; handle Ctrl+C on focused tables."""

    def eventFilter(self, obj, event):  # noqa: N802 - Qt naming
        if event.type() == QEvent.Polish and isinstance(obj, QTableWidget):
            if (
                obj.selectionMode() == QAbstractItemView.NoSelection
                and obj.editTriggers() == QAbstractItemView.NoEditTriggers
            ):
                configure_readonly_copyable_table(obj)
        elif event.type() == QEvent.KeyPress and isinstance(obj, QTableWidget):
            if event.matches(QKeySequence.Copy) and copy_selected_table_text(obj):
                return True
        return False


def _enable_copyable_tables(app: QApplication) -> None:
    """Install once: read-only tables become selectable/copyable app-wide."""
    if getattr(app, _COPYABLE_TABLES_FILTER_ATTR, None) is not None:
        return
    filt = _CopyableTablesFilter(app)
    app.installEventFilter(filt)
    setattr(app, _COPYABLE_TABLES_FILTER_ATTR, filt)


def apply_quality_hint_style(widget, *, poor: bool) -> None:
    """Color a label (or similar) when quality/fit hints indicate a problem."""
    if poor:
        # Type selector so this beats the app-wide ``QLabel { color: … }`` rule.
        widget.setStyleSheet(f"QLabel {{ color: {COLOR_QUALITY_POOR}; }}")
    else:
        widget.setStyleSheet("")


def apply_style(app: QApplication) -> None:
    """
    Apply a modern, readable theme (dark-ish neutral + blue accent) and a slightly larger font.
    Also enables select-copy on all QLabels and read-only QTableWidgets app-wide.
    """
    # Fusion respects palette + stylesheet on all platforms; the Windows native style
    # often keeps pale widget backgrounds while still using our light Text color.
    base = QStyleFactory.create("Fusion") if "Fusion" in QStyleFactory.keys() else app.style()
    app.setStyle(_BrightSpinArrowStyle(base))

    _enable_selectable_labels(app)
    _enable_copyable_tables(app)

    font = QFont()
    font.setPointSize(11)
    app.setFont(font)
    apply_font_point_size(app)

    # Softer, lower-contrast dark theme: slightly lighter surfaces, gentler borders,
    # and a less saturated accent for comfort.
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor("#121821"))
    pal.setColor(QPalette.WindowText, QColor("#e7eef6"))
    pal.setColor(QPalette.Base, QColor("#0f151d"))
    pal.setColor(QPalette.AlternateBase, QColor("#141c26"))
    pal.setColor(QPalette.Text, QColor("#e7eef6"))
    pal.setColor(QPalette.Button, QColor("#141c26"))
    pal.setColor(QPalette.ButtonText, QColor("#e7eef6"))
    pal.setColor(QPalette.Highlight, QColor("#4c8dff"))
    pal.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ToolTipBase, QColor("#0f151d"))
    pal.setColor(QPalette.ToolTipText, QColor("#e7eef6"))
    app.setPalette(pal)

    app.setStyleSheet(
        f"""
        QMainWindow {{ background: #121821; }}
        QLabel {{ color: #e7eef6; }}

        QSplitter::handle {{ background: #0f151d; }}

        QLineEdit, QTextEdit, QPlainTextEdit,
        QComboBox, QSpinBox, QDoubleSpinBox {{
            background: #0f151d;
            border: 1px solid #2a3646;
            border-radius: 8px;
            padding: 6px;
            color: #e7eef6;
            selection-background-color: #4c8dff;
            selection-color: #ffffff;
        }}
        /* Keep placeholder color muted for general readability. */
        QLineEdit::placeholder {{ color: {COLOR_MUTED_TEXT}; }}

        QComboBox::drop-down {{
            border: 0;
            width: 24px;
        }}
        QComboBox QAbstractItemView {{
            background-color: #0f151d;
            color: #e7eef6;
            border: 1px solid #2a3646;
            selection-background-color: #4c8dff;
            selection-color: #ffffff;
            outline: 0;
        }}

        /* Spin arrows are painted by _BrightSpinArrowStyle (stylesheet ::*-arrow is ignored). */

        QCheckBox {{ color: #e7eef6; spacing: 6px; }}

        QGroupBox {{
            border: 1px solid #2a3646;
            border-radius: 12px;
            margin-top: 10px;
            padding: 8px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 6px 0 6px;
            color: #a7b7c8;
        }}

        QPushButton {{
            background: #182232;
            border: 1px solid #2a3646;
            border-radius: 12px;
            padding: 7px 10px;
        }}
        QPushButton:hover {{ border-color: #4c8dff; }}
        QPushButton:disabled {{ color: {COLOR_MUTED_TEXT}; background: #141c26; }}

        /* High-contrast help button */
        QPushButton#helpButton {{
            background: #4c8dff;
            color: #0b1016;
            border: 0;
            border-radius: 11px;
            font-weight: 700;
        }}
        QPushButton#helpButton:hover {{ background: #6aa0ff; }}

        QTabWidget::pane {{ border: 0; }}

        QListWidget {{
            background: #0f151d;
            border: 1px solid #2a3646;
            border-radius: 12px;
            padding: 6px;
        }}
        QListWidget::item {{
            padding: 8px 10px;
            border-radius: 8px;
        }}
        QListWidget::item:selected {{
            background: #1b2a3d;
            border: 1px solid #4c8dff;
        }}
        """
    )
