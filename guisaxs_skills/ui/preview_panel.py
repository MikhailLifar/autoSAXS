from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QDialog, QLabel, QScrollArea, QVBoxLayout, QWidget


class _ClickableImageLabel(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self._on_click = None

    def set_on_click(self, fn) -> None:
        self._on_click = fn
        self.setCursor(Qt.PointingHandCursor if fn is not None else Qt.ArrowCursor)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.LeftButton and self._on_click is not None:
            self._on_click()
            event.accept()
            return
        super().mousePressEvent(event)


class _ImageViewerDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Artifact viewer")
        self.resize(1100, 800)

        self._image = QLabel()
        self._image.setAlignment(Qt.AlignCenter)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setAlignment(Qt.AlignCenter)
        scroll.setWidget(self._image)

        lay = QVBoxLayout(self)
        lay.addWidget(scroll)

    def set_image_path(self, path: str) -> None:
        self.setWindowTitle(f"Artifact viewer — {path}")
        pix = QPixmap(path)
        if pix.isNull():
            self._image.setText("Unable to load image.")
            self._image.setPixmap(QPixmap())
            return
        self._image.setPixmap(pix)
        self._image.adjustSize()


class PreviewPanel(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._label = QLabel("Select an artifact to preview")
        self._label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._label.setWordWrap(True)

        self._image = _ClickableImageLabel()
        self._image.setAlignment(Qt.AlignCenter)
        self._image.set_on_click(None)

        self._current_image_path: Optional[str] = None
        self._current_pixmap: Optional[QPixmap] = None
        self._viewer: Optional[_ImageViewerDialog] = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._label)
        lay.addWidget(self._image, 1)

    def show_path(self, path: str) -> None:
        self._label.setText(path)
        self._image.clear()
        self._current_image_path = None
        self._current_pixmap = None
        self._image.set_on_click(None)
        if not path or not os.path.exists(path):
            return
        p = Path(path)
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp"):
            pix = QPixmap(str(p))
            if pix.isNull():
                return
            self._current_image_path = str(p)
            self._current_pixmap = pix
            self._rescale_preview()
            self._image.set_on_click(self._open_viewer)

    def _rescale_preview(self) -> None:
        if self._current_pixmap is None:
            return
        self._image.setPixmap(
            self._current_pixmap.scaled(self._image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def _open_viewer(self) -> None:
        if not self._current_image_path:
            return
        if self._viewer is None:
            self._viewer = _ImageViewerDialog(self)
        self._viewer.set_image_path(self._current_image_path)
        self._viewer.show()
        self._viewer.raise_()
        self._viewer.activateWindow()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        # Re-scale image on resize
        self._rescale_preview()
        super().resizeEvent(event)

