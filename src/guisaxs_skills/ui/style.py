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
    QWidget,
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
_FONT_ROLE_FILTER_ATTR = "_autosaxs_font_role_filter"
_COPYABLE_TABLE_ATTR = "_autosaxs_copyable_table"
_SPIN_ARROW = QColor("#e7eef6")

# Widget properties for relative / special fonts under app-wide scale.
PROP_FONT_DELTA_PT = "_autosaxs_font_delta_pt"
PROP_FONT_BOLD = "_autosaxs_font_bold"
PROP_FONT_MONO = "_autosaxs_font_mono"
# Absolute override (Settings Appearance live preview): wins over app base + delta.
PROP_FONT_ABSOLUTE_PT = "_autosaxs_font_absolute_pt"
# Skip forcing font on these Qt class name prefixes (matplotlib dig deep).
_FONT_WALK_SKIP_PREFIXES = (
    "FigureCanvas",
    "NavigationToolbar",
    "MplCanvas",
)


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


def ui_scale_factor(point_size: int | None = None) -> float:
    """Scale factor vs design baseline (DEFAULT_FONT_POINT_SIZE)."""
    size = clamp_font_point_size(point_size if point_size is not None else load_font_point_size())
    return size / float(DEFAULT_FONT_POINT_SIZE)


def scaled_px(base_px: int, point_size: int | None = None) -> int:
    """Scale a design-time pixel length with the current UI font size."""
    return max(1, int(round(base_px * ui_scale_factor(point_size))))


def mark_font_role(
    widget,
    *,
    delta_pt: int = 0,
    bold: bool = False,
    mono: bool = False,
) -> None:
    """
    Mark a widget for relative sizing when Apply re-walks the tree.

    Use instead of absolute ``setPointSize`` / ``font-size: Npx`` so Settings →
    Appearance can scale *all* chrome consistently.
    """
    widget.setProperty(PROP_FONT_ABSOLUTE_PT, None)
    widget.setProperty(PROP_FONT_DELTA_PT, int(delta_pt))
    widget.setProperty(PROP_FONT_BOLD, bool(bold))
    widget.setProperty(PROP_FONT_MONO, bool(mono))
    app = QApplication.instance()
    if app is not None:
        size = clamp_font_point_size(app.font().pointSize())
        widget.setFont(_widget_font_for_size(app.font(), widget, size))


def mark_font_absolute(widget, point_size: int) -> int:
    """
    Pin a widget to an absolute point size (Appearance live preview).

    Survives Apply force-walk. Pair with a widget stylesheet ``font-size`` so
    app QSS ``QWidget { font-size: …pt }`` cannot hide the selection.
    """
    size = clamp_font_point_size(point_size)
    widget.setProperty(PROP_FONT_ABSOLUTE_PT, size)
    app = QApplication.instance()
    if app is not None:
        widget.setFont(_widget_font_for_size(app.font(), widget, size))
    else:
        font = QFont(widget.font())
        font.setPointSize(size)
        widget.setFont(font)
    return size


def _absolute_font_pt(widget) -> int | None:
    raw = widget.property(PROP_FONT_ABSOLUTE_PT)
    if raw is None:
        return None
    try:
        return clamp_font_point_size(raw)
    except (TypeError, ValueError):
        return None


def _widget_font_for_size(base: QFont, widget, size: int) -> QFont:
    font = QFont(base)
    absolute = _absolute_font_pt(widget)
    if absolute is not None:
        font.setPointSize(absolute)
    else:
        delta = widget.property(PROP_FONT_DELTA_PT)
        try:
            delta_i = int(delta) if delta is not None else 0
        except (TypeError, ValueError):
            delta_i = 0
        font.setPointSize(clamp_font_point_size(size + delta_i))
    if bool(widget.property(PROP_FONT_BOLD)):
        font.setBold(True)
    if bool(widget.property(PROP_FONT_MONO)):
        font.setFamily("monospace")
    return font


def _should_skip_font_walk(widget) -> bool:
    name = type(widget).__name__
    return any(name.startswith(p) for p in _FONT_WALK_SKIP_PREFIXES)


def _force_fonts_on_all_widgets(app: QApplication, size: int) -> None:
    """Push the resolved font onto every live widget (explicit local fonts stick otherwise)."""
    base = QFont(app.font())
    base.setPointSize(size)
    for widget in app.allWidgets():
        if widget is None or _should_skip_font_walk(widget):
            continue
        widget.setFont(_widget_font_for_size(base, widget, size))


def _app_stylesheet(point_size: int) -> str:
    """Theme stylesheet with font-size + chrome metrics scaled to ``point_size``."""
    s = ui_scale_factor(point_size)
    pad = max(4, int(round(6 * s)))
    pad_btn_y = max(5, int(round(7 * s)))
    pad_btn_x = max(8, int(round(10 * s)))
    pad_list = max(5, int(round(8 * s)))
    radius = max(6, int(round(8 * s)))
    radius_lg = max(8, int(round(12 * s)))
    combo_w = max(18, int(round(24 * s)))
    group_mt = max(8, int(round(10 * s)))
    return f"""
        QWidget {{ font-size: {point_size}pt; }}
        QMainWindow {{ background: #121821; }}
        QLabel {{ color: #e7eef6; }}

        QSplitter::handle {{ background: #0f151d; }}

        QLineEdit, QTextEdit, QPlainTextEdit,
        QComboBox, QSpinBox, QDoubleSpinBox {{
            background: #0f151d;
            border: 1px solid #2a3646;
            border-radius: {radius}px;
            padding: {pad}px;
            color: #e7eef6;
            selection-background-color: #4c8dff;
            selection-color: #ffffff;
        }}
        /* Keep placeholder color muted for general readability. */
        QLineEdit::placeholder {{ color: {COLOR_MUTED_TEXT}; }}

        QComboBox::drop-down {{
            border: 0;
            width: {combo_w}px;
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

        QCheckBox {{ color: #e7eef6; spacing: {pad}px; }}

        QGroupBox {{
            border: 1px solid #2a3646;
            border-radius: {radius_lg}px;
            margin-top: {group_mt}px;
            padding: {pad}px;
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
            border-radius: {radius_lg}px;
            padding: {pad_btn_y}px {pad_btn_x}px;
        }}
        QPushButton:hover {{ border-color: #4c8dff; }}
        QPushButton:disabled {{ color: {COLOR_MUTED_TEXT}; background: #141c26; }}

        /* High-contrast help button */
        QPushButton#helpButton {{
            background: #4c8dff;
            color: #0b1016;
            border: 0;
            border-radius: {max(8, int(round(11 * s)))}px;
            font-weight: 700;
        }}
        QPushButton#helpButton:hover {{ background: #6aa0ff; }}

        QTabWidget::pane {{ border: 0; }}

        QListWidget {{
            background: #0f151d;
            border: 1px solid #2a3646;
            border-radius: {radius_lg}px;
            padding: {pad}px;
        }}
        QListWidget::item {{
            padding: {pad_list}px {pad_btn_x}px;
            border-radius: {radius}px;
        }}
        QListWidget::item:selected {{
            background: #1b2a3d;
            border: 1px solid #4c8dff;
        }}
        """


def apply_font_point_size(
    app: QApplication | None = None,
    point_size: int | None = None,
    *,
    persist: bool = False,
) -> int:
    """
    Apply app-wide UI scale from the appearance font size.

    Sets QApplication font, rebuilds the themed stylesheet (font-size + scaled
    paddings/radii), and force-walks every live widget so local ``setFont`` /
    sticky sizes cannot keep the old point size. Matplotlib canvas widgets are
    skipped; plot typography is out of scope.
    """
    size = clamp_font_point_size(point_size if point_size is not None else load_font_point_size())
    if persist:
        size = save_font_point_size(size)
    target = app if app is not None else QApplication.instance()
    if target is None:
        return size
    font = QFont(target.font())
    font.setPointSize(size)
    target.setFont(font)
    # Stylesheet ``font-size`` must track Apply; otherwise QSS pins the old size.
    target.setStyleSheet(_app_stylesheet(size))
    _force_fonts_on_all_widgets(target, size)
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


class _FontRoleFilter(QObject):
    """Apply marked font roles when widgets are polished after creation."""

    def eventFilter(self, obj, event):  # noqa: N802 - Qt naming
        if event.type() == QEvent.Polish and isinstance(obj, QWidget):
            if (
                obj.property(PROP_FONT_ABSOLUTE_PT) is not None
                or obj.property(PROP_FONT_DELTA_PT) is not None
                or bool(obj.property(PROP_FONT_BOLD))
                or bool(obj.property(PROP_FONT_MONO))
            ):
                app = QApplication.instance()
                if app is not None and not _should_skip_font_walk(obj):
                    size = clamp_font_point_size(app.font().pointSize())
                    obj.setFont(_widget_font_for_size(app.font(), obj, size))
        return False


def _enable_font_role_filter(app: QApplication) -> None:
    if getattr(app, _FONT_ROLE_FILTER_ATTR, None) is not None:
        return
    filt = _FontRoleFilter(app)
    app.installEventFilter(filt)
    setattr(app, _FONT_ROLE_FILTER_ATTR, filt)


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
    Apply a modern, readable theme (dark-ish neutral + blue accent) and scalable UI chrome.
    Also enables select-copy on all QLabels and read-only QTableWidgets app-wide.
    """
    # Fusion respects palette + stylesheet on all platforms; the Windows native style
    # often keeps pale widget backgrounds while still using our light text color.
    base = QStyleFactory.create("Fusion") if "Fusion" in QStyleFactory.keys() else app.style()
    app.setStyle(_BrightSpinArrowStyle(base))

    _enable_selectable_labels(app)
    _enable_copyable_tables(app)
    _enable_font_role_filter(app)

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

    # Font + scaled stylesheet + force-walk (SSOT for Appearance → Apply).
    apply_font_point_size(app)
