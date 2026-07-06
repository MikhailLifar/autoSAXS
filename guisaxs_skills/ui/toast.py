from __future__ import annotations

from PyQt5.QtCore import QEasingCurve, QPoint, QPropertyAnimation, Qt, QTimer, pyqtSignal
from PyQt5.QtWidgets import QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget


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


class ConfirmToast(QWidget):
    """Small centered prompt with OK/Cancel buttons."""

    accepted = pyqtSignal()
    rejected = pyqtSignal()

    def __init__(
        self,
        *,
        text: str,
        parent: QWidget,
        ok_text: str = "OK",
        cancel_text: str = "Cancel",
    ) -> None:
        super().__init__(parent=parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._finished = False

        self._label = QLabel(text, self)
        self._label.setWordWrap(True)
        self._label.setStyleSheet("color: white; background: transparent;")

        btn_ok = QPushButton(ok_text, self)
        btn_ok.clicked.connect(self._on_accept)
        btn_cancel = QPushButton(cancel_text, self)
        btn_cancel.clicked.connect(self._on_reject)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(8, 0, 8, 8)
        btn_row.addStretch(1)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 0)
        lay.setSpacing(8)
        lay.addWidget(self._label)
        lay.addLayout(btn_row)

        self.setStyleSheet(
            """
            QWidget {
              background: rgba(30, 30, 30, 230);
              border-radius: 6px;
            }
            QPushButton {
              background: rgba(60, 60, 60, 255);
              color: white;
              border: none;
              padding: 4px 12px;
              border-radius: 4px;
            }
            QPushButton:hover {
              background: rgba(80, 80, 80, 255);
            }
            """
        )

    def show_centered(self) -> None:
        parent = self.parentWidget()
        self.adjustSize()
        if parent is None:
            self.show()
            return
        gp = parent.mapToGlobal(QPoint(0, 0))
        x = gp.x() + max(int((parent.width() - self.width()) / 2), 0)
        y = gp.y() + max(int((parent.height() - self.height()) / 2), 0)
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_accept(self) -> None:
        self._finish(accepted=True)

    def _on_reject(self) -> None:
        self._finish(accepted=False)

    def _finish(self, *, accepted: bool) -> None:
        if self._finished:
            return
        self._finished = True
        if accepted:
            self.accepted.emit()
        else:
            self.rejected.emit()
        self.close()

