from __future__ import annotations

from PyQt5.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QLabel, QWidget


class Toast(QWidget):
    """
    A small non-blocking message that fades out.
    """

    def __init__(self, *, text: str, parent: QWidget) -> None:
        super().__init__(parent=parent)
        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        self._label = QLabel(text, self)
        self._label.setWordWrap(True)
        self._label.setStyleSheet(
            """
            QLabel {
              background: rgba(30, 30, 30, 230);
              color: white;
              padding: 8px 10px;
              border-radius: 6px;
            }
            """
        )
        self._label.adjustSize()
        self.resize(self._label.size())

        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._opacity.setOpacity(1.0)

        self._anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._anim.setDuration(900)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self.close)

    def show_near_bottom(self, *, y_offset: int = 18) -> None:
        parent = self.parentWidget()
        if parent is None:
            self.show()
            return
        gp = parent.mapToGlobal(QPoint(0, 0))
        x = gp.x() + max(int((parent.width() - self.width()) / 2), 0)
        y = gp.y() + max(parent.height() - self.height() - y_offset, 0)
        self.move(x, y)
        self.show()
        # Small delay before fading so the text is readable.
        QTimer.singleShot(900, self._anim.start)

