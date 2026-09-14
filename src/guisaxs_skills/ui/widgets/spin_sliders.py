"""Shared spinbox + slider controls for GNOM adjust wizards."""

from __future__ import annotations

import math

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QDoubleSpinBox, QHBoxLayout, QSlider, QWidget


class LengthNmSpinSlider(QWidget):
    """Length (nm) spinbox with arrows + horizontal slider (0.01 nm ticks)."""

    valueChanged = pyqtSignal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._block = False
        self._spin = QDoubleSpinBox()
        self._spin.setDecimals(4)
        self._spin.setRange(0.01, 1e6)
        self._spin.setValue(1.0)
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setMinimum(1)  # 0.01 nm
        self._slider.setMaximum(100_000)  # 1000 nm at 0.01
        self._slider.setValue(100)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._slider, 1)
        lay.addWidget(self._spin, 0)
        self._spin.valueChanged.connect(self._on_spin)
        self._slider.valueChanged.connect(self._on_slider)

    def spin(self) -> QDoubleSpinBox:
        return self._spin

    def value(self) -> float:
        return float(self._spin.value())

    def setValue(self, v: float) -> None:  # noqa: N802
        self._block = True
        try:
            self._spin.setValue(float(v))
            self._sync_slider_from_spin()
        finally:
            self._block = False

    def blockSignals(self, block: bool) -> None:  # noqa: N802
        self._spin.blockSignals(block)
        self._slider.blockSignals(block)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        self._spin.setEnabled(enabled)
        self._slider.setEnabled(enabled)
        super().setEnabled(enabled)

    def _sync_slider_from_spin(self) -> None:
        ticks = int(round(float(self._spin.value()) * 100.0))
        ticks = max(self._slider.minimum(), min(self._slider.maximum(), ticks))
        self._slider.blockSignals(True)
        self._slider.setValue(ticks)
        self._slider.blockSignals(False)

    def _on_spin(self, *_a) -> None:
        if self._block:
            return
        self._block = True
        try:
            self._sync_slider_from_spin()
        finally:
            self._block = False
        self.valueChanged.emit(self.value())

    def _on_slider(self, ticks: int) -> None:
        if self._block:
            return
        self._block = True
        try:
            self._spin.setValue(float(ticks) / 100.0)
        finally:
            self._block = False
        self.valueChanged.emit(self.value())


class AlphaSpinSlider(QWidget):
    """Alpha spinbox with (auto)=0 plus slider (0 = auto)."""

    valueChanged = pyqtSignal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._block = False
        self._spin = QDoubleSpinBox()
        self._spin.setDecimals(6)
        self._spin.setRange(0.0, 1e6)
        self._spin.setSpecialValueText("(auto)")
        self._spin.setValue(0.0)
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(10_000)
        self._slider.setValue(0)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._slider, 1)
        lay.addWidget(self._spin, 0)
        self._spin.valueChanged.connect(self._on_spin)
        self._slider.valueChanged.connect(self._on_slider)

    def spin(self) -> QDoubleSpinBox:
        return self._spin

    def value(self) -> float:
        return float(self._spin.value())

    def setValue(self, v: float) -> None:  # noqa: N802
        self._block = True
        try:
            self._spin.setValue(float(v))
            self._sync_slider_from_spin()
        finally:
            self._block = False

    def blockSignals(self, block: bool) -> None:  # noqa: N802
        self._spin.blockSignals(block)
        self._slider.blockSignals(block)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        self._spin.setEnabled(enabled)
        self._slider.setEnabled(enabled)
        super().setEnabled(enabled)

    def _alpha_to_ticks(self, alpha: float) -> int:
        if alpha <= 0.0:
            return 0
        t = 1.0 + 9999.0 * (math.log10(1.0 + alpha) / math.log10(1.0 + 1e6))
        return max(1, min(10_000, int(round(t))))

    def _ticks_to_alpha(self, ticks: int) -> float:
        if ticks <= 0:
            return 0.0
        frac = (float(ticks) - 1.0) / 9999.0
        return max(0.0, (10.0 ** (frac * math.log10(1.0 + 1e6))) - 1.0)

    def _sync_slider_from_spin(self) -> None:
        self._slider.blockSignals(True)
        self._slider.setValue(self._alpha_to_ticks(float(self._spin.value())))
        self._slider.blockSignals(False)

    def _on_spin(self, *_a) -> None:
        if self._block:
            return
        self._block = True
        try:
            self._sync_slider_from_spin()
        finally:
            self._block = False
        self.valueChanged.emit(self.value())

    def _on_slider(self, ticks: int) -> None:
        if self._block:
            return
        self._block = True
        try:
            self._spin.setValue(self._ticks_to_alpha(int(ticks)))
        finally:
            self._block = False
        self.valueChanged.emit(self.value())
