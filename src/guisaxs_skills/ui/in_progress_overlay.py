"""Tasteful in-progress overlay for modeling / preview hosts."""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QObject, QRectF, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen
from PyQt5.QtWidgets import QWidget

from .style import COLOR_ACCENT, COLOR_MUTED_TEXT

_LABEL = "In-progress"
_TICK_MS = 32
_PERIOD_MS = 1800


class InProgressOverlay(QWidget):
    """Dimmed cover with label + sweeping accent strip (child of the preview host)."""

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_MS)
        self._timer.timeout.connect(self._tick)
        host.installEventFilter(self)
        self.hide()

    def set_active(self, active: bool) -> None:
        on = bool(active)
        if on:
            self._phase = 0.0
            self._sync_geometry()
            self.show()
            self.raise_()
            if not self._timer.isActive():
                self._timer.start()
            self.update()
        else:
            self._timer.stop()
            self.hide()

    def is_active(self) -> bool:
        return self.isVisible()

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self.parentWidget() and event.type() == QEvent.Resize:
            self._sync_geometry()
        return False

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = self.rect()
        p.fillRect(r, QColor(15, 21, 29, 140))

        strip_h = max(4, min(10, max(1, r.height() // 18)))
        strip_y = max(0, r.height() // 2 + 18)
        band = QRectF(0, strip_y, r.width(), strip_h)
        base = QColor(COLOR_MUTED_TEXT)
        base.setAlpha(70)
        p.fillRect(band, base)

        sweep_w = max(48.0, r.width() * 0.35)
        x = -sweep_w + (r.width() + sweep_w) * self._phase
        grad = QLinearGradient(x, 0, x + sweep_w, 0)
        clear = QColor(COLOR_ACCENT)
        clear.setAlpha(0)
        mid = QColor(COLOR_ACCENT)
        mid.setAlpha(220)
        grad.setColorAt(0.0, clear)
        grad.setColorAt(0.45, mid)
        grad.setColorAt(0.55, mid)
        grad.setColorAt(1.0, clear)
        p.fillRect(band, grad)

        font = QFont(p.font())
        font.setPointSize(max(11, font.pointSize() + 1))
        font.setBold(True)
        p.setFont(font)
        p.setPen(QPen(QColor("#e7eef6")))
        text_rect = QRectF(0, max(0, strip_y - 36), r.width(), 28)
        p.drawText(text_rect, int(Qt.AlignHCenter | Qt.AlignVCenter), _LABEL)
        p.end()

    def _tick(self) -> None:
        if not self.isVisible():
            self._timer.stop()
            return
        self._phase = (self._phase + float(_TICK_MS) / float(_PERIOD_MS)) % 1.0
        self.update()

    def _sync_geometry(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self.setGeometry(0, 0, parent.width(), parent.height())
