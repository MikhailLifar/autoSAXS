from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PyQt5.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ...logic.path_display import contracted_path_label
from ...ui.saxs_interactive_3d import Interactive3DViewerDialog, SaxsInteractive3DWidget


class LiveviewViewer3D(QWidget):
    """
    Liveview wrapper: interactive 3D (rotate / zoom) plus path + open-folder.
    Same ``SaxsInteractive3DWidget`` stack as other GUIs can embed via ``guisaxs_liveview.viewer3d``.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cif_path: Optional[str] = None
        self._bodies_shape: Optional[str] = None
        self._bodies_params: Optional[dict[str, float]] = None
        self._open_folder: Optional[Path] = None
        self._plot = SaxsInteractive3DWidget(self, embedded=True)
        self._plot.set_full_view_callback(self._open_full_3d_dialog)
        self._full_3d: Optional[Interactive3DViewerDialog] = None

        self._hint = QLabel("Click preview for interactive 3D.")
        self._hint.setWordWrap(True)
        self._open_btn = QPushButton("Open model folder…")
        self._open_btn.clicked.connect(self._on_open_folder)
        self._open_btn.setEnabled(False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._plot, 1)
        lay.addWidget(self._hint)
        row = QHBoxLayout()
        row.addWidget(self._open_btn)
        row.addStretch(1)
        lay.addLayout(row)

    def clear(self) -> None:
        self._cif_path = None
        self._bodies_shape = None
        self._bodies_params = None
        self._open_folder = None
        self._plot.setToolTip("")
        self._open_btn.setToolTip("")
        self._open_btn.setEnabled(False)
        self._plot.clear()

    def set_model_path(self, path: Optional[str]) -> None:
        """Load ``.cif`` for 3D; directories enable “open folder” without a loaded model."""
        p = (path or "").strip()
        self._cif_path = None
        self._bodies_shape = None
        self._bodies_params = None
        self._open_folder = None

        pp = Path(p) if p else None
        if pp is not None:
            if pp.is_file():
                self._open_folder = pp.parent.resolve()
            elif pp.is_dir():
                self._open_folder = pp.resolve()

        if p and os.path.isfile(p) and p.lower().endswith(".cif"):
            self._cif_path = p
            short, full = contracted_path_label(p)
            self._open_btn.setEnabled(True)
            self._open_btn.setToolTip(full)
            self._plot.setToolTip(full)
            ok = self._plot.load_cif(p, title=short)
            if not ok:
                self._hint.setText("Could not load this model. Open folder for files.")
            else:
                self._hint.setText("Click preview for interactive 3D.")
            return
        self._plot.clear()
        if p and os.path.isfile(p):
            _, full = contracted_path_label(p)
            self._plot.setToolTip(full)
            self._open_btn.setToolTip(full)
            self._open_btn.setEnabled(self._open_folder is not None and self._open_folder.is_dir())
            self._hint.setText("Not a .cif — open folder for outputs.")
        elif p and os.path.isdir(p):
            _, full = contracted_path_label(p)
            self._plot.setToolTip(full)
            self._open_btn.setToolTip(full)
            self._open_btn.setEnabled(True)
            self._hint.setText("No .cif here — open folder to inspect.")
        elif p:
            self._plot.setToolTip(p)
            self._open_btn.setToolTip(p)
            par = Path(p).parent
            self._open_folder = par if par.is_dir() else self._open_folder
            self._open_btn.setEnabled(bool(self._open_folder and self._open_folder.is_dir()))
            self._hint.setText("Waiting for a .cif path.")
        else:
            self._plot.setToolTip("")
            self._open_btn.setToolTip("")
            self._open_btn.setEnabled(False)
            self._hint.setText("Click preview for interactive 3D.")

    def set_bodies_analytical(
        self,
        shape: str,
        params: dict[str, float],
        *,
        folder: Optional[Path] = None,
    ) -> None:
        """3D preview from analytical BODIES shape (isosurface), not damstart ``.cif``."""
        self._cif_path = None
        self._bodies_shape = shape
        self._bodies_params = dict(params)
        if folder is not None:
            self._open_folder = folder.resolve() if folder.is_dir() else folder.parent.resolve()
        else:
            self._open_folder = None
        tip = str(self._open_folder) if self._open_folder else shape
        self._open_btn.setEnabled(bool(self._open_folder and self._open_folder.is_dir()))
        self._open_btn.setToolTip(tip)
        self._plot.setToolTip(tip)
        self._plot.load_bodies_analytical(shape, params, title=shape)
        self._hint.setText("Click preview for interactive 3D.")

    def _open_full_3d_dialog(self) -> None:
        if self._full_3d is None:
            self._full_3d = Interactive3DViewerDialog(self)
            self._full_3d.finished.connect(self._on_full_3d_dialog_closed)
        self._plot.pause_embedded_rotation()
        p = self._cif_path
        if p and os.path.isfile(p) and p.lower().endswith(".cif"):
            short, _full = contracted_path_label(p)
            self._full_3d.set_cif_path(p, window_title=f"3D — {short}")
        elif self._bodies_shape is not None and self._bodies_params is not None:
            self._full_3d.set_bodies_analytical(
                self._bodies_shape,
                self._bodies_params,
                window_title=f"3D — {self._bodies_shape} (analytical)",
            )
        else:
            self._plot.resume_embedded_rotation_if_visible()
            return
        self._full_3d.show()
        self._full_3d.raise_()
        self._full_3d.activateWindow()

    def _on_full_3d_dialog_closed(self, _result: int) -> None:
        self._plot.resume_embedded_rotation_if_visible()

    def _on_open_folder(self) -> None:
        folder = self._open_folder
        if folder is None or not folder.is_dir():
            return
        path = str(folder.resolve())
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", path])
            elif sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception:
            pass
