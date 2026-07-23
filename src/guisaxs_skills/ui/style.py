from __future__ import annotations

from PyQt5.QtGui import QColor, QFont, QPalette
from PyQt5.QtWidgets import QApplication, QStyleFactory

COLOR_MUTED_TEXT = "#728195"
COLOR_REQUIRED_STAR = "#ff4d4f"
# Same red as required-field star — poor fit / data-quality hints in analysis panes.
COLOR_QUALITY_POOR = COLOR_REQUIRED_STAR


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
    """
    # Fusion respects palette + stylesheet on all platforms; the Windows native style
    # often keeps pale widget backgrounds while still using our light Text color.
    if "Fusion" in QStyleFactory.keys():
        app.setStyle("Fusion")

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

        QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
            background: #182232;
            border: 0;
            width: 18px;
        }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover,
        QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {{
            background: #1b2a3d;
        }}

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

