from __future__ import annotations

import html
from typing import Any, Mapping

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ......ui.style import COLOR_QUALITY_POOR
from .format_display import format_sizes_passport_html
from .plots import DrPlot, GnomFitPlot


class SizesPane(QWidget):
    """Minimized GNOM D(R) pane: embedded plots, passport, and Adjust."""

    adjust_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        plots_row = QHBoxLayout()
        plots_row.setSpacing(8)
        self._fit_plot = GnomFitPlot()
        self._dr_plot = DrPlot()
        for p in (self._fit_plot, self._dr_plot):
            p.setMinimumHeight(120)
            p.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        iq_box = QGroupBox("I(q)")
        iq_lay = QVBoxLayout(iq_box)
        iq_lay.setContentsMargins(6, 8, 6, 6)
        iq_lay.addWidget(self._fit_plot, 1)
        dr_box = QGroupBox("D(R)")
        dr_lay = QVBoxLayout(dr_box)
        dr_lay.setContentsMargins(6, 8, 6, 6)
        dr_lay.addWidget(self._dr_plot, 1)
        plots_row.addWidget(iq_box, 1)
        plots_row.addWidget(dr_box, 1)

        self._lbl_diagnostics = QLabel("—")
        self._lbl_diagnostics.setWordWrap(True)
        self._lbl_diagnostics.setTextFormat(Qt.RichText)
        self._lbl_diagnostics.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._btn_adjust = QPushButton("Adjust")
        self._btn_adjust.clicked.connect(self.adjust_requested.emit)

        right = QVBoxLayout()
        right.setSpacing(0)
        right.addWidget(QLabel("Passport"), 0)
        right.addWidget(self._lbl_diagnostics, 1)
        right.addSpacing(6)
        right.addWidget(self._btn_adjust, 0)
        right.addStretch(0)

        body = QHBoxLayout()
        body.setSpacing(10)
        body.addLayout(plots_row, 3)
        body.addLayout(right, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addLayout(body, 1)

    def set_running(self, running: bool) -> None:
        self._btn_adjust.setEnabled(not running)

    def set_diagnostics(
        self,
        *,
        text: str = "",
        poor: bool = False,
        html_text: str = "",
        quality: Mapping[str, Any] | None = None,
    ) -> None:
        self._lbl_diagnostics.setStyleSheet("")
        if quality is not None:
            body = format_sizes_passport_html(quality, poor_color=COLOR_QUALITY_POOR)
            self._lbl_diagnostics.setTextFormat(Qt.RichText)
            self._lbl_diagnostics.setText(body)
            return
        if html_text:
            self._lbl_diagnostics.setTextFormat(Qt.RichText)
            self._lbl_diagnostics.setText(html_text)
            return
        msg = text or "—"
        if poor and text:
            esc = html.escape(text)
            self._lbl_diagnostics.setTextFormat(Qt.RichText)
            self._lbl_diagnostics.setText(f'<span style="color:{COLOR_QUALITY_POOR}">{esc}</span>')
            return
        self._lbl_diagnostics.setTextFormat(Qt.PlainText)
        self._lbl_diagnostics.setText(msg)

    def show_sizes(self, profile_path: str, gnom_out_path: str) -> None:
        self._fit_plot.plot_from_dat_and_gnom_out(profile_path, gnom_out_path)
        self._dr_plot.plot_from_gnom_out(gnom_out_path)

    def clear_view(self) -> None:
        self._fit_plot.clear_plot()
        self._dr_plot.clear_plot()
        self.set_diagnostics()

    @property
    def fit_plot(self) -> GnomFitPlot:
        return self._fit_plot

    @property
    def dr_plot(self) -> DrPlot:
        return self._dr_plot
