"""Auto/Manual coach for modeling mini-apps (shape / DR).

Owns the local Auto flag and AttentionPulse targets. Decoupled from liveview
``session.auto_processing`` — each modeling window has its own Manual default.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import QPushButton

from ..liveview.ui.attention import AttentionPulse

_START_LABEL = "Start auto-processing"
_STOP_LABEL = "Stop auto-processing"


class ModelingAutoMode(QObject):
    """Manual by default; coach pulses Start + Confirm until Auto."""

    auto_changed = pyqtSignal(bool)  # True when Auto

    def __init__(
        self,
        parent: QObject,
        *,
        auto_btn: QPushButton,
        confirm_btn: QPushButton,
    ) -> None:
        super().__init__(parent)
        self._auto = False
        self._suppress = 0
        self._auto_btn = auto_btn
        self._confirm_btn = confirm_btn
        self._attention = AttentionPulse(self)
        self._auto_btn.clicked.connect(self._on_auto_btn_clicked)
        self._refresh_ui()

    def is_auto(self) -> bool:
        return bool(self._auto)

    def should_accept_ipc_confirm(self) -> bool:
        return bool(self._auto)

    def enter_auto(self) -> None:
        if self._auto:
            self._refresh_ui()
            return
        self._auto = True
        self._refresh_ui()
        self.auto_changed.emit(True)

    def enter_manual(self) -> None:
        if not self._auto:
            self._refresh_ui()
            return
        self._auto = False
        self._refresh_ui()
        self.auto_changed.emit(False)

    def on_control_changed(self, *_args) -> None:
        """Any run-affecting control edit → Manual (no-op while suppressed)."""
        if self._suppress > 0:
            return
        self.enter_manual()

    @contextmanager
    def suppress_control_changes(self) -> Iterator[None]:
        """Ignore control signals while applying context / disk params."""
        self._suppress += 1
        try:
            yield
        finally:
            self._suppress = max(0, self._suppress - 1)

    def shutdown(self) -> None:
        self._attention.shutdown()

    def _on_auto_btn_clicked(self) -> None:
        if self._auto:
            self.enter_manual()
        else:
            self.enter_auto()

    def _refresh_ui(self) -> None:
        if self._auto:
            self._auto_btn.setText(_STOP_LABEL)
            self._attention.clear()
        else:
            self._auto_btn.setText(_START_LABEL)
            self._attention.set_targets([self._auto_btn, self._confirm_btn])
