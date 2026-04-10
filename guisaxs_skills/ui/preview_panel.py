from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget


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


class ImageViewerDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Image viewer")
        self.resize(1100, 800)

        self._image = QLabel()
        self._image.setAlignment(Qt.AlignCenter)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setAlignment(Qt.AlignCenter)
        self._scroll.setWidget(self._image)

        self._zoom = QComboBox()
        self._zoom.addItems(["Fit", "25%", "50%", "100%", "200%", "400%"])
        self._zoom.setCurrentText("Fit")
        self._zoom.currentTextChanged.connect(self._apply_zoom)

        self._path: Optional[str] = None
        self._pix: Optional[QPixmap] = None

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Zoom:"))
        top.addWidget(self._zoom)
        top.addStretch(1)
        lay.addLayout(top)
        lay.addWidget(self._scroll, 1)

    def set_image_path(self, path: str) -> None:
        self._path = path
        self.setWindowTitle(f"Image viewer — {path}")
        pix = QPixmap(path)
        self._pix = None if pix.isNull() else pix
        if self._pix is None:
            self._image.setText("Unable to load image.")
            self._image.setPixmap(QPixmap())
            return
        self._apply_zoom()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._zoom.currentText() == "Fit":
            self._apply_zoom()

    def _apply_zoom(self) -> None:
        if self._pix is None:
            return
        mode = self._zoom.currentText()
        if mode == "Fit":
            # Fit to viewport while preserving aspect ratio.
            viewport = self._scroll.viewport().size()
            if viewport.width() <= 0 or viewport.height() <= 0:
                return
            scaled = self._pix.scaled(viewport, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._image.setPixmap(scaled)
            self._image.adjustSize()
            return
        try:
            pct = int(mode.strip().replace("%", ""))
        except Exception:
            pct = 100
        w = max(1, int(self._pix.width() * (pct / 100.0)))
        h = max(1, int(self._pix.height() * (pct / 100.0)))
        scaled = self._pix.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._image.setPixmap(scaled)
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
        self._viewer: Optional[ImageViewerDialog] = None
        self._temp_preview_png: Optional[str] = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._label)
        lay.addWidget(self._image, 1)

    def show_path(self, path: str) -> None:
        self._cleanup_temp_preview()
        self._label.setText(path)
        self._image.clear()
        self._current_image_path = None
        self._current_pixmap = None
        self._image.set_on_click(None)
        if not path or not os.path.exists(path):
            return
        p = Path(path)
        suffix = p.suffix.lower()
        preview_path: Optional[str] = None
        if suffix in (".png", ".jpg", ".jpeg", ".bmp"):
            preview_path = str(p)
        elif suffix in (".tif", ".tiff", ".dat", ".csv"):
            preview_path = self._make_preview_png(str(p))

        if preview_path:
            pix = QPixmap(preview_path)
            if pix.isNull():
                return
            self._current_image_path = preview_path
            self._current_pixmap = pix
            self._rescale_preview()
            self._image.set_on_click(self._open_viewer)

    def _cleanup_temp_preview(self) -> None:
        if self._temp_preview_png and os.path.exists(self._temp_preview_png):
            try:
                os.unlink(self._temp_preview_png)
            except Exception:
                pass
        self._temp_preview_png = None

    def _make_preview_png(self, src_path: str) -> Optional[str]:
        """
        Create a temporary PNG preview for non-PNG artifacts.

        Returns the generated PNG path (caller owns cleanup via _cleanup_temp_preview),
        or None if preview generation fails.
        """
        p = Path(src_path)
        suffix = p.suffix.lower()
        try:
            fd, out_path = tempfile.mkstemp(prefix="guisaxs_preview_", suffix=".png")
            os.close(fd)
        except Exception:
            return None

        ok = False
        try:
            if suffix in (".tif", ".tiff"):
                ok = self._render_tiff_to_png(src_path, out_path)
            elif suffix == ".dat":
                ok = self._render_dat_to_png(src_path, out_path)
            elif suffix == ".csv":
                ok = self._render_csv_to_png(src_path, out_path)
        except Exception:
            ok = False

        if not ok:
            try:
                os.unlink(out_path)
            except Exception:
                pass
            return None

        self._temp_preview_png = out_path
        return out_path

    def _render_tiff_to_png(self, src_path: str, out_path: str) -> bool:
        from matplotlib.figure import Figure

        import numpy as np

        img = None
        try:
            import fabio

            img = fabio.open(src_path).data
        except Exception:
            img = None
        if img is None:
            try:
                import tifffile

                img = tifffile.imread(src_path)
            except Exception:
                img = None
        if img is None:
            return False

        a = np.asarray(img)
        if a.ndim > 2:
            a = a.reshape((-1,) + a.shape[-2:])[0]
        a = np.asarray(a, dtype=float)
        a = np.log1p(np.maximum(a, 0.0))

        fig = Figure(figsize=(8, 6), dpi=140)
        ax = fig.add_subplot(111)
        ax.imshow(a, cmap="viridis", origin="lower", aspect="equal", interpolation="nearest")
        ax.set_xlabel("x (px)")
        ax.set_ylabel("y (px)")
        ax.set_title(Path(src_path).name if "Path" in globals() else src_path)
        try:
            cbar = fig.colorbar(ax.images[0], ax=ax, fraction=0.046, pad=0.04)
            cbar.set_label("log(1 + I)")
        except Exception:
            pass
        fig.tight_layout()
        fig.savefig(out_path, format="png")
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0

    def _render_dat_to_png(self, src_path: str, out_path: str) -> bool:
        from matplotlib.figure import Figure

        try:
            from autosaxs.utils import read_saxs
        except Exception:
            return False

        q, I, sigma, _meta = read_saxs(src_path)
        fig = Figure(figsize=(8, 5), dpi=140)
        ax = fig.add_subplot(111)
        try:
            import numpy as np

            q = np.asarray(q)
            I = np.asarray(I)
            m = np.isfinite(q) & np.isfinite(I) & (I > 0)
            q = q[m]
            I = I[m]
        except Exception:
            pass
        ax.plot(q, I, linewidth=1.0)
        ax.set_xlabel("q (nm$^{-1}$)")
        ax.set_ylabel("I (a.u.)")
        ax.set_yscale("log")
        ax.grid(True, alpha=0.25)
        fig.tight_layout()
        fig.savefig(out_path, format="png")
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0

    def _render_csv_to_png(self, src_path: str, out_path: str) -> bool:
        """
        Render CSV as either a simple x-y plot (first 2 numeric cols) or a table snapshot.
        """
        import csv

        from matplotlib.figure import Figure

        def _try_float(x: str) -> Optional[float]:
            try:
                return float(x)
            except Exception:
                return None

        with open(src_path, "r", newline="") as f:
            sample = f.read(8192)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample)
            except Exception:
                dialect = csv.excel

            reader = csv.reader(f, dialect)
            rows: list[list[str]] = []
            for i, r in enumerate(reader):
                if i >= 200:
                    break
                if r:
                    rows.append([c.strip() for c in r])

        if not rows:
            return False

        # Determine max columns and pad rows for stable indexing.
        ncols = max(len(r) for r in rows)
        for r in rows:
            if len(r) < ncols:
                r.extend([""] * (ncols - len(r)))

        # Identify numeric columns using first few rows.
        probe = rows[0:25]
        numeric_cols: list[int] = []
        for j in range(ncols):
            vals = [_try_float(r[j]) for r in probe if r[j] != ""]
            if vals and sum(v is not None for v in vals) >= max(3, int(0.7 * len(vals))):
                numeric_cols.append(j)

        fig = Figure(figsize=(8, 5), dpi=140)
        ax = fig.add_subplot(111)

        if len(numeric_cols) >= 2:
            xj, yj = numeric_cols[0], numeric_cols[1]
            xs: list[float] = []
            ys: list[float] = []
            for r in rows:
                xv = _try_float(r[xj])
                yv = _try_float(r[yj])
                if xv is None or yv is None:
                    continue
                xs.append(xv)
                ys.append(yv)
            if xs and ys:
                ax.plot(xs, ys, linewidth=1.0)
                ax.set_xlabel(f"col {xj + 1}")
                ax.set_ylabel(f"col {yj + 1}")
                ax.grid(True, alpha=0.25)
                fig.tight_layout()
                fig.savefig(out_path, format="png")
                return os.path.exists(out_path) and os.path.getsize(out_path) > 0

        # Fallback: render a table snapshot.
        ax.axis("off")
        max_rows = min(25, len(rows))
        max_cols = min(6, ncols)
        cell_text = [r[:max_cols] for r in rows[:max_rows]]
        tbl = ax.table(cellText=cell_text, loc="center", cellLoc="left")
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8)
        tbl.scale(1, 1.2)
        fig.tight_layout()
        fig.savefig(out_path, format="png")
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0

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
            self._viewer = ImageViewerDialog(self)
        self._viewer.set_image_path(self._current_image_path)
        self._viewer.show()
        self._viewer.raise_()
        self._viewer.activateWindow()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        # Re-scale image on resize
        self._rescale_preview()
        super().resizeEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._cleanup_temp_preview()
        super().closeEvent(event)


def open_image_viewer(parent: Optional[QWidget], source_path: str) -> None:
    """
    Open the shared zoomable image viewer (Fit / % zoom — same as left/right thumbnail click-through).
    Resolves .tif / .dat / .csv to a raster preview when needed, then loads into the viewer.
    """
    path = (source_path or "").strip()
    if not path or not os.path.exists(path):
        return
    sfx = Path(path).suffix.lower()
    if sfx in (".png", ".jpg", ".jpeg", ".bmp"):
        raster = path
    else:
        helper = PreviewPanel()
        helper.show_path(path)
        raster = helper._current_image_path
        if not raster or not os.path.exists(raster):
            helper._cleanup_temp_preview()
            return
        dlg = ImageViewerDialog(parent)
        dlg.set_image_path(raster)
        helper._cleanup_temp_preview()
        dlg.show()
        dlg.raise_()
        dlg.activateWindow()
        return

    dlg = ImageViewerDialog(parent)
    dlg.set_image_path(raster)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()

