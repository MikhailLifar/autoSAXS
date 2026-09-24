from __future__ import annotations

from typing import Any, Mapping, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .format_display import format_guinier_passport_html
from .plots import GuinierCurvePlot


class GuinierPane(QWidget):
    """Minimized Guinier pane: plot on top, passport + Adjust below (controls live in the wizard)."""

    adjust_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._first: Optional[int] = None
        self._last: Optional[int] = None
        self._summary_rg: str = "—"
        self._plot = GuinierCurvePlot(figsize=(2.14, 1.61))
        self._plot.setMinimumHeight(94)
        self._plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._lbl_passport = QLabel("—")
        self._lbl_passport.setWordWrap(True)
        self._lbl_passport.setTextFormat(Qt.RichText)
        self._lbl_passport.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._btn_adjust = QPushButton("Adjust")
        self._btn_adjust.clicked.connect(self.adjust_requested.emit)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._plot, 1)
        lay.addWidget(QLabel("Passport"), 0)
        lay.addWidget(self._lbl_passport, 0)
        lay.addWidget(self._btn_adjust, 0)

    @property
    def plot_widget(self) -> GuinierCurvePlot:
        return self._plot

    def set_running(self, running: bool) -> None:
        self._btn_adjust.setEnabled(not running)

    def set_range(self, first: int, last: int, *, emit: bool = False) -> None:
        _ = emit
        f = max(1, int(first))
        self._first = f
        self._last = max(f, int(last))

    def clear_interval(self) -> None:
        self._first = None
        self._last = None

    def first_last(self) -> tuple[Optional[int], Optional[int]]:
        return self._first, self._last

    def set_diagnostics(
        self,
        *,
        text: str = "",
        html_text: str = "",
        result: Optional[Mapping[str, Any]] = None,
        **_legacy: Any,
    ) -> None:
        """Show full passport. Accepts legacy kwargs for compatibility (ignored)."""
        _ = _legacy
        if result is not None:
            from .format_display import format_display_number, scalar_value

            rg = scalar_value(result.get("rg"))
            if rg is None:
                rg = scalar_value(result.get("Rg"))
            self._summary_rg = f"{format_display_number(rg)} nm" if rg is not None else "—"
            self._lbl_passport.setTextFormat(Qt.RichText)
            self._lbl_passport.setText(format_guinier_passport_html(result))
            return
        if html_text:
            self._lbl_passport.setTextFormat(Qt.RichText)
            self._lbl_passport.setText(html_text)
            return
        if text:
            self._lbl_passport.setTextFormat(Qt.PlainText)
            self._lbl_passport.setText(text)
            return
        self._summary_rg = "—"
        self._lbl_passport.setTextFormat(Qt.RichText)
        self._lbl_passport.setText("—")

    def summary_rg(self) -> str:
        return self._summary_rg or "—"

    def show_guinier(self, profile_path: str, results_txt_path: str) -> None:
        self._plot.plot_from_profile_and_results(profile_path, results_txt_path)

    def clear_view(self) -> None:
        self._plot.clear_plot()
        self.set_diagnostics()
        self.clear_interval()
