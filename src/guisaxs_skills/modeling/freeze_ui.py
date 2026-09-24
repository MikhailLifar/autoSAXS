"""Semi-transparent freeze overlay when liveview pushes context during a running job."""

from __future__ import annotations

from typing import Any, Optional

from PyQt5.QtCore import QEvent, QObject, Qt
from PyQt5.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget


_FREEZE_MSG = (
    "Modeling job is still running.\n"
    "Liveview moved to another sample — this window is frozen\n"
    "so paths stay honest for the active job.\n\n"
    "Context will update when the job finishes."
)


class ContextFreezeOverlay(QWidget):
    """Blocks interaction; white translucent cover with a warning."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("modelingContextFreezeOverlay")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(
            "#modelingContextFreezeOverlay {"
            "  background-color: rgba(255, 255, 255, 200);"
            "}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 24, 24, 24)
        lab = QLabel(_FREEZE_MSG)
        lab.setAlignment(Qt.AlignCenter)
        lab.setWordWrap(True)
        lab.setStyleSheet("color: #222; font-size: 14px; background: transparent;")
        lay.addStretch(1)
        lay.addWidget(lab, 0, Qt.AlignCenter)
        lay.addStretch(1)
        self.hide()

    def cover_parent(self) -> None:
        p = self.parentWidget()
        if p is None:
            return
        self.setGeometry(p.rect())
        self.raise_()
        self.show()


class _CentralResizeFilter(QObject):
    def __init__(self, overlay: ContextFreezeOverlay, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._overlay = overlay

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Resize and self._overlay.isVisible():
            self._overlay.cover_parent()
        return False


def install_context_freeze(window: QMainWindow) -> None:
    """Attach freeze state on a modeling ``QMainWindow`` (idempotent)."""
    if getattr(window, "_freeze_overlay", None) is not None:
        return
    window._context_push_deferred = False  # type: ignore[attr-defined]
    window._freeze_overlay = None  # type: ignore[attr-defined]
    window._freeze_resize_filter = None  # type: ignore[attr-defined]


def enter_context_freeze(window: QMainWindow) -> None:
    install_context_freeze(window)
    window._context_push_deferred = True  # type: ignore[attr-defined]
    central = window.centralWidget()
    if central is None:
        return
    overlay: Optional[ContextFreezeOverlay] = getattr(window, "_freeze_overlay", None)
    if overlay is None:
        overlay = ContextFreezeOverlay(central)
        window._freeze_overlay = overlay  # type: ignore[attr-defined]
        filt = _CentralResizeFilter(overlay, window)
        central.installEventFilter(filt)
        window._freeze_resize_filter = filt  # type: ignore[attr-defined]
    overlay.cover_parent()


def leave_context_freeze(window: QMainWindow) -> None:
    overlay: Optional[ContextFreezeOverlay] = getattr(window, "_freeze_overlay", None)
    if overlay is not None:
        overlay.hide()


def notify_ready_for_context_if_deferred(window: QMainWindow, ipc: Any) -> None:
    """Clear freeze; if a context push was deferred, ask liveview to re-send."""
    deferred = bool(getattr(window, "_context_push_deferred", False))
    window._context_push_deferred = False  # type: ignore[attr-defined]
    leave_context_freeze(window)
    if deferred and ipc is not None:
        ipc.send_ready_for_context()
