from __future__ import annotations

import os
import tempfile
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

        try:
            import matplotlib.image as mpimg
        except Exception:
            return False

        img = mpimg.imread(src_path)
        fig = Figure(figsize=(8, 6), dpi=140)
        ax = fig.add_subplot(111)
        ax.imshow(img, cmap="gray" if getattr(img, "ndim", 2) == 2 else None)
        ax.set_axis_off()
        fig.tight_layout(pad=0)
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
        ax.plot(q, I, linewidth=1.0)
        ax.set_xlabel("q")
        ax.set_ylabel("I")
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
            self._viewer = _ImageViewerDialog(self)
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

