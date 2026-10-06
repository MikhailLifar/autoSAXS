from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import (
    FONT_POINT_SIZE_MAX,
    FONT_POINT_SIZE_MIN,
    FONT_PRESET_LARGE,
    FONT_PRESET_MEDIUM,
    FONT_PRESET_SMALL,
)
from .style import (
    COLOR_ACCENT,
    COLOR_MUTED_TEXT,
    apply_font_point_size,
    clamp_font_point_size,
    load_font_point_size,
)


class _AppearancePage(QWidget):
    """Appearance settings: font size with presets, fine control, and live preview."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._updating = False
        self._current = load_font_point_size()

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 12, 18, 12)
        root.setSpacing(14)

        title = QLabel("Appearance")
        title_font = QFont(title.font())
        title_font.setPointSize(max(title_font.pointSize() + 4, 16))
        title_font.setBold(True)
        title.setFont(title_font)
        root.addWidget(title)

        blurb = QLabel("Choose a comfortable reading size for menus, labels, forms, and panels.")
        blurb.setWordWrap(True)
        blurb.setStyleSheet(f"color: {COLOR_MUTED_TEXT};")
        root.addWidget(blurb)

        section = QFrame()
        section.setObjectName("appearanceSection")
        section.setStyleSheet(
            f"""
            QFrame#appearanceSection {{
                background: #0f151d;
                border: 1px solid #2a3646;
                border-radius: 14px;
            }}
            """
        )
        section_lay = QVBoxLayout(section)
        section_lay.setContentsMargins(16, 16, 16, 16)
        section_lay.setSpacing(12)

        heading = QLabel("Font size")
        heading_font = QFont(heading.font())
        heading_font.setBold(True)
        heading.setFont(heading_font)
        section_lay.addWidget(heading)

        hint = QLabel("Applies immediately across the whole app.")
        hint.setStyleSheet(f"color: {COLOR_MUTED_TEXT};")
        section_lay.addWidget(hint)

        presets_row = QHBoxLayout()
        presets_row.setSpacing(8)
        self._preset_group = QButtonGroup(self)
        self._preset_group.setExclusive(True)
        for label, size in (
            ("Small", FONT_PRESET_SMALL),
            ("Medium", FONT_PRESET_MEDIUM),
            ("Large", FONT_PRESET_LARGE),
        ):
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setProperty("fontPreset", size)
            btn.setMinimumHeight(34)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            self._preset_group.addButton(btn)
            presets_row.addWidget(btn)
            btn.clicked.connect(lambda _checked=False, s=size: self._on_preset(s))
        section_lay.addLayout(presets_row)
        self._style_preset_buttons()

        fine_row = QHBoxLayout()
        fine_row.setSpacing(10)
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(FONT_POINT_SIZE_MIN, FONT_POINT_SIZE_MAX)
        self._slider.setTickPosition(QSlider.TicksBelow)
        self._slider.setTickInterval(1)
        self._slider.setSingleStep(1)
        self._slider.setPageStep(1)
        fine_row.addWidget(self._slider, 1)

        self._spin = QSpinBox()
        self._spin.setRange(FONT_POINT_SIZE_MIN, FONT_POINT_SIZE_MAX)
        self._spin.setSuffix(" pt")
        self._spin.setFixedWidth(84)
        fine_row.addWidget(self._spin)
        section_lay.addLayout(fine_row)

        self._preview = QLabel()
        self._preview.setWordWrap(True)
        self._preview.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._preview.setMinimumHeight(88)
        self._preview.setStyleSheet(
            f"""
            QLabel {{
                background: #141c26;
                border: 1px solid #2a3646;
                border-radius: 10px;
                padding: 12px 14px;
                color: #e7eef6;
            }}
            """
        )
        section_lay.addWidget(self._preview)

        root.addWidget(section)
        root.addStretch(1)

        self._slider.valueChanged.connect(self._on_slider)
        self._spin.valueChanged.connect(self._on_spin)
        self._set_size(self._current, persist=False, apply=False)
        self._refresh_preview()

    def _style_preset_buttons(self) -> None:
        for btn in self._preset_group.buttons():
            btn.setStyleSheet(
                f"""
                QPushButton {{
                    background: #182232;
                    border: 1px solid #2a3646;
                    border-radius: 10px;
                    padding: 6px 10px;
                }}
                QPushButton:hover {{ border-color: {COLOR_ACCENT}; }}
                QPushButton:checked {{
                    background: #1b2a3d;
                    border: 1px solid {COLOR_ACCENT};
                    color: #ffffff;
                    font-weight: 600;
                }}
                """
            )

    def _refresh_preview(self) -> None:
        sample = (
            f"Preview at {self._current} pt\n"
            "Menus · Labels · Buttons · Forms · Liveview panels"
        )
        self._preview.setText(sample)
        font = QFont(self._preview.font())
        font.setPointSize(self._current)
        self._preview.setFont(font)

    def _sync_presets(self) -> None:
        for btn in self._preset_group.buttons():
            preset = int(btn.property("fontPreset"))
            btn.setChecked(preset == self._current)

    def _set_size(self, size: int, *, persist: bool, apply: bool) -> None:
        size = clamp_font_point_size(size)
        self._updating = True
        try:
            self._current = size
            self._slider.setValue(size)
            self._spin.setValue(size)
            self._sync_presets()
            self._refresh_preview()
        finally:
            self._updating = False
        if apply:
            apply_font_point_size(point_size=size, persist=persist)

    def _on_preset(self, size: int) -> None:
        if self._updating:
            return
        self._set_size(size, persist=True, apply=True)

    def _on_slider(self, value: int) -> None:
        if self._updating:
            return
        self._set_size(value, persist=True, apply=True)

    def _on_spin(self, value: int) -> None:
        if self._updating:
            return
        self._set_size(value, persist=True, apply=True)

    def current_size(self) -> int:
        return self._current


class SettingsDialog(QDialog):
    """
    App settings with a left category list and stacked pages.

    Hierarchy today: Settings → Appearance → Font size.
    """

    PAGE_APPEARANCE = 0

    def __init__(self, *, parent: QWidget | None = None, initial_page: int = PAGE_APPEARANCE) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(640, 420)
        self.resize(720, 480)

        self._categories = QListWidget()
        self._categories.setFixedWidth(168)
        self._categories.setSpacing(2)
        self._categories.setStyleSheet(
            f"""
            QListWidget {{
                background: #0f151d;
                border: 1px solid #2a3646;
                border-radius: 12px;
                padding: 8px;
                outline: 0;
            }}
            QListWidget::item {{
                padding: 10px 12px;
                border-radius: 8px;
                color: #e7eef6;
            }}
            QListWidget::item:selected {{
                background: #1b2a3d;
                border: 1px solid {COLOR_ACCENT};
            }}
            """
        )
        appearance_item = QListWidgetItem("Appearance")
        self._categories.addItem(appearance_item)

        self._stack = QStackedWidget()
        self._appearance = _AppearancePage(self)
        self._stack.addWidget(self._appearance)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._categories)
        body.addWidget(self._stack, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)
        close_btn = buttons.button(QDialogButtonBox.Close)
        if close_btn is not None:
            close_btn.setDefault(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(12)
        header = QLabel("Settings")
        header_font = QFont(header.font())
        header_font.setPointSize(max(header_font.pointSize() + 2, 14))
        header_font.setBold(True)
        header.setFont(header_font)
        root.addWidget(header)
        root.addLayout(body, 1)
        root.addWidget(buttons)

        self._categories.currentRowChanged.connect(self._stack.setCurrentIndex)
        page = max(0, min(self._stack.count() - 1, int(initial_page)))
        self._categories.setCurrentRow(page)

    def reject(self) -> None:  # type: ignore[override]
        # Esc / window close: keep the live-applied size (already persisted).
        super().reject()


def open_appearance_settings(*, parent: QWidget | None = None) -> None:
    """Open Settings focused on Appearance (Settings → Appearance → Font size)."""
    dlg = SettingsDialog(parent=parent, initial_page=SettingsDialog.PAGE_APPEARANCE)
    dlg.exec_()
