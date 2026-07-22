from __future__ import annotations

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import QDialog, QLabel, QVBoxLayout

from ..panels.right.monodisperse.wizard import MonodisperseWizardWidget


class MonodisperseWizardDialog(QDialog):
    """
    Monodisperse analysis wizard — same window chrome as CalibrationWizardDialog
    (resizable top-level window, min/max buttons, size grip, screen-based default size).
    """

    closed = pyqtSignal()

    def __init__(self, *, wizard: MonodisperseWizardWidget, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Monodisperse analysis")
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

        self._wizard = wizard
        if wizard.parent() is not self:
            wizard.setParent(self)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(
            QLabel(
                "Guinier → GNOM → optional shape (BODIES / DAMMIF). "
                "Control changes suspend auto-processing until you resume."
            )
        )
        lay.addWidget(self._wizard, 1)

    @property
    def wizard_widget(self) -> MonodisperseWizardWidget:
        return self._wizard

    def set_running(self, running: bool) -> None:
        self._wizard.set_running(bool(running))

    def closeEvent(self, event) -> None:  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)
