from __future__ import annotations

from PyQt5.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPalette, QPolygon
from PyQt5.QtWidgets import QApplication, QLabel, QProxyStyle, QStyle, QStyleFactory

COLOR_MUTED_TEXT = "#728195"
COLOR_REQUIRED_STAR = "#ff4d4f"
# Same red as required-field star — poor fit / data-quality hints in analysis panes.
COLOR_QUALITY_POOR = COLOR_REQUIRED_STAR
# Caution / mid-tier quality (readable amber on light UI backgrounds).
COLOR_QUALITY_WARN = "#b45309"

_SELECTABLE_LABELS_FILTER_ATTR = "_autosaxs_selectable_labels_filter"
_SPIN_ARROW = QColor("#e7eef6")


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
    Also enables select-copy on all QLabels app-wide (no per-label flags needed).
    """
    # Fusion respects palette + stylesheet on all platforms; the Windows native style
    # often keeps pale widget backgrounds while still using our light Text color.
    base = QStyleFactory.create("Fusion") if "Fusion" in QStyleFactory.keys() else app.style()
    app.setStyle(_BrightSpinArrowStyle(base))

    _enable_selectable_labels(app)

    font = QFont()
    font.setPointSize(11)
    app.setFont(font)

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
