from __future__ import annotations

from PyQt5.QtCore import QTimer, pyqtSignal
from PyQt5.QtWidgets import QFormLayout, QLabel, QSizePolicy, QSpinBox, QVBoxLayout, QWidget

from .plots import GuinierCurvePlot


class GuinierPane(QWidget):
    range_changed = pyqtSignal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plot = GuinierCurvePlot(figsize=(2.14, 1.61))
        self._plot.setMinimumHeight(94)
        self._plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._lbl_quality = QLabel("—")
        self._lbl_quality.setWordWrap(True)
        self._lbl_class = QLabel("—")
        self._lbl_class.setWordWrap(True)
        self._lbl_rg = QLabel("—")
        self._first = QSpinBox()
        self._first.setMinimum(1)
        self._first.setMaximum(99999)
        self._last = QSpinBox()
        self._last.setMinimum(1)
        self._last.setMaximum(99999)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(300)
        self._debounce.timeout.connect(self._emit_range)
        self._block_range = False

        form = QFormLayout()
        form.addRow("first", self._first)
        form.addRow("last", self._last)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._plot, 1)
        lay.addWidget(QLabel("Quality"))
        lay.addWidget(self._lbl_quality)
        lay.addWidget(QLabel("Classification"))
        lay.addWidget(self._lbl_class)
        lay.addWidget(QLabel("Rg"))
        lay.addWidget(self._lbl_rg)
        lay.addLayout(form)
        self._first.valueChanged.connect(self._on_range_spin)
        self._last.valueChanged.connect(self._on_range_spin)

    @property
    def plot_widget(self) -> GuinierCurvePlot:
        return self._plot

    def set_running(self, running: bool) -> None:
        if running:
            self._debounce.stop()
        self._first.setEnabled(not running)
        self._last.setEnabled(not running)

    def set_range(self, first: int, last: int, *, emit: bool = False) -> None:
        self._block_range = True
        try:
            self._first.setValue(max(1, int(first)))
            self._last.setValue(max(int(first), int(last)))
        finally:
            self._block_range = False
        if emit:
            self._emit_range()

    def first_last(self) -> tuple[int, int]:
        return int(self._first.value()), int(self._last.value())

    def set_diagnostics(
        self,
        *,
        quality_class: str = "",
        classification: str = "",
        rg_nm: str = "",
        interval_r2: str = "",
    ) -> None:
        self._lbl_quality.setText(quality_class or interval_r2 or "—")
        self._lbl_class.setText(classification or "—")
        self._lbl_rg.setText(rg_nm or "—")

    def show_guinier(self, profile_path: str, region_yaml_path: str) -> None:
        self._plot.plot_from_profile_and_region(profile_path, region_yaml_path)

    def clear_view(self) -> None:
        self._plot.clear_plot()
        self.set_diagnostics()

    def _on_range_spin(self, _v: int) -> None:
        if self._block_range:
            return
        if self._last.value() < self._first.value():
            self._block_range = True
            try:
                self._last.setValue(self._first.value())
            finally:
                self._block_range = False
        self._debounce.start()

    def _emit_range(self) -> None:
        if self._block_range:
            return
        self.range_changed.emit(int(self._first.value()), int(self._last.value()))
