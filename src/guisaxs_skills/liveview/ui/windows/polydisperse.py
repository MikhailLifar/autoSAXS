from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import QDialog, QLabel, QVBoxLayout

from ..panels.right.polydisperse.window_widget import PolydisperseWindowWidget


class PolydisperseAnalysisWindow(QDialog):
    """
    Polydisperse analysis window — same chrome as MonodisperseWizardDialog
    (resizable top-level window, min/max buttons, size grip, screen-based default size).
    """

    closed = pyqtSignal()

    def __init__(self, *, window_widget: PolydisperseWindowWidget, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Polydisperse analysis")
        self.setWindowFlags(
            Qt.Window
            | Qt.CustomizeWindowHint
            | Qt.WindowTitleHint
            | Qt.WindowSystemMenuHint
            | Qt.WindowCloseButtonHint
            | Qt.WindowMinMaxButtonsHint
        )
        self.setSizeGripEnabled(True)
        try:
            scr = QGuiApplication.primaryScreen()
            geo = scr.availableGeometry() if scr is not None else None
            if geo is not None:
                w = max(1120, int(0.88 * int(geo.width())))
                h = max(780, int(0.92 * int(geo.height())))
                self.resize(w, h)
                self.setMinimumSize(960, 700)
        except Exception:
            self.setMinimumWidth(1120)
            self.resize(1320, 860)

        self._widget = window_widget
        if window_widget.parent() is not self:
            window_widget.setParent(self)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(
            QLabel(
                "Guinier (independent) → fit_sizes (D(R)) → optional model_mixture. "
                "Control changes suspend auto-processing until you resume."
            )
        )
        lay.addWidget(self._widget, 1)

    @property
    def window_widget(self) -> PolydisperseWindowWidget:
        return self._widget

    def set_running(self, running: bool) -> None:
        self._widget.set_running(bool(running))

    def closeEvent(self, event) -> None:  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)
