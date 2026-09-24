"""Shared spinbox + slider controls for GNOM adjust wizards."""

from __future__ import annotations

import math

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QDoubleSpinBox, QHBoxLayout, QSlider, QSpinBox, QWidget

_SLIDER_TICKS = 10_000


class LengthNmSpinSlider(QWidget):
    """
    Length (nm) spinbox with arrows + horizontal slider.

    The spin range stays wide so arrows / typed values can exceed the slider span.
    The slider maps ``[slider_min_nm, slider_max_nm]`` (default 0 … 1000 nm).
    """

    valueChanged = pyqtSignal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._block = False
        self._slider_min_nm = 0.0
        self._slider_max_nm = 1000.0
        self._spin = QDoubleSpinBox()
        self._spin.setDecimals(4)
        self._spin.setRange(0.0, 1e6)
        self._spin.setValue(1.0)
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(_SLIDER_TICKS)
        self._slider.setValue(0)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._slider, 1)
        lay.addWidget(self._spin, 0)
        self._spin.valueChanged.connect(self._on_spin)
        self._slider.valueChanged.connect(self._on_slider)
        self._sync_slider_from_spin()

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

    def set_slider_span(self, *, min_nm: float = 0.0, max_nm: float) -> None:
        """Set slider domain only; spin can still go beyond via arrows / typing."""
        lo = max(0.0, float(min_nm))
        hi = float(max_nm)
        if not math.isfinite(hi) or hi <= lo:
            hi = lo + 1.0
        self._slider_min_nm = lo
        self._slider_max_nm = hi
        self._sync_slider_from_spin()

    def blockSignals(self, block: bool) -> None:  # noqa: N802
        self._spin.blockSignals(block)
        self._slider.blockSignals(block)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        self._spin.setEnabled(enabled)
        self._slider.setEnabled(enabled)
        super().setEnabled(enabled)

    def _value_to_ticks(self, value: float) -> int:
        span = self._slider_max_nm - self._slider_min_nm
        if span <= 0:
            return 0
        frac = (float(value) - self._slider_min_nm) / span
        return max(0, min(_SLIDER_TICKS, int(round(frac * _SLIDER_TICKS))))

    def _ticks_to_value(self, ticks: int) -> float:
        span = self._slider_max_nm - self._slider_min_nm
        frac = float(ticks) / float(_SLIDER_TICKS)
        return self._slider_min_nm + frac * span

    def _sync_slider_from_spin(self) -> None:
        self._slider.blockSignals(True)
        self._slider.setValue(self._value_to_ticks(float(self._spin.value())))
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
            self._spin.setValue(self._ticks_to_value(int(ticks)))
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


class PointIndexSpinSlider(QWidget):
    """
    1-based point-index spinbox with horizontal slider (Guinier first/last).

    Slider domain is typically capped at the last point with ``q ≤ 2`` nm⁻¹;
    the spin range stays wider so typed values can exceed the slider span.
    """

    valueChanged = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._block = False
        self._slider_min = 1
        self._slider_max = 100
        self._spin = QSpinBox()
        self._spin.setMinimum(1)
        self._spin.setMaximum(99999)
        self._spin.setValue(1)
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setMinimum(0)
        self._slider.setMaximum(_SLIDER_TICKS)
        self._slider.setValue(0)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        lay.addWidget(self._slider, 1)
        lay.addWidget(self._spin, 0)
        self._spin.valueChanged.connect(self._on_spin)
        self._slider.valueChanged.connect(self._on_slider)
        self._sync_slider_from_spin()

    def spin(self) -> QSpinBox:
        return self._spin

    def value(self) -> int:
        return int(self._spin.value())

    def setValue(self, v: int) -> None:  # noqa: N802
        self._block = True
        try:
            self._spin.setValue(int(v))
            self._sync_slider_from_spin()
        finally:
            self._block = False

    def set_slider_span(self, *, min_i: int = 1, max_i: int) -> None:
        lo = max(1, int(min_i))
        hi = max(lo, int(max_i))
        self._slider_min = lo
        self._slider_max = hi
        self._sync_slider_from_spin()

    def set_spin_maximum(self, max_i: int) -> None:
        self._spin.setMaximum(max(1, int(max_i)))

    def blockSignals(self, block: bool) -> None:  # noqa: N802
        self._spin.blockSignals(block)
        self._slider.blockSignals(block)

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        self._spin.setEnabled(enabled)
        self._slider.setEnabled(enabled)
        super().setEnabled(enabled)

    def _value_to_ticks(self, value: int) -> int:
        span = self._slider_max - self._slider_min
        if span <= 0:
            return 0
        frac = (float(value) - float(self._slider_min)) / float(span)
        return max(0, min(_SLIDER_TICKS, int(round(frac * _SLIDER_TICKS))))

    def _ticks_to_value(self, ticks: int) -> int:
        span = self._slider_max - self._slider_min
        frac = float(ticks) / float(_SLIDER_TICKS)
        return int(round(self._slider_min + frac * span))

    def _sync_slider_from_spin(self) -> None:
        self._slider.blockSignals(True)
        self._slider.setValue(self._value_to_ticks(int(self._spin.value())))
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
            self._spin.setValue(self._ticks_to_value(int(ticks)))
        finally:
            self._block = False
        self.valueChanged.emit(self.value())
