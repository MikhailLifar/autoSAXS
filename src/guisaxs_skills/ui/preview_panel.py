from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from ..liveview.ui.widgets.plots import DatCurveViewerDialog

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..logic.path_display import contracted_path_label


def _render_tiff_to_png(src_path: str, out_path: str) -> bool:
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
    short, _full = contracted_path_label(src_path)
    ax.set_title(short)
    try:
        cbar = fig.colorbar(ax.images[0], ax=ax, fraction=0.046, pad=0.04)
        cbar.set_label("log(1 + I)")
    except Exception:
        pass
    fig.tight_layout()
    fig.savefig(out_path, format="png")
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0


def _render_dat_to_png(
    src_path: str,
    out_path: str,
    *,
    q_min: Any = None,
    q_max: Any = None,
) -> bool:
    from matplotlib.figure import Figure

    try:
        from autosaxs.core.utils import read_saxs
    except Exception:
        return False

    q, I, sigma, _meta = read_saxs(src_path)
    fig = Figure(figsize=(8, 5), dpi=140)
    ax = fig.add_subplot(111)
    try:
        import numpy as np

        q = np.asarray(q)
        I = np.asarray(I)
        sigma = np.asarray(sigma) if sigma is not None else None
        m = np.isfinite(q) & np.isfinite(I) & (I > 0)
        if sigma is not None:
            m = m & np.isfinite(sigma) & (sigma >= 0)
        q = q[m]
        I = I[m]
        if sigma is not None:
            sigma = sigma[m]
    except Exception:
        pass

    if "np" in locals():
        # Make the thumbnail stable on log-y by avoiding errors that cross <= 0.
        if sigma is not None and len(I):
            sigma = np.minimum(sigma, 0.99 * I)

    try:
        from ..liveview.ui.widgets.plots import draw_q_match_band

        draw_q_match_band(ax, q_min, q_max)
    except Exception:
        pass

    if sigma is not None:
        ax.errorbar(
            q,
            I,
            yerr=sigma,
            fmt="o",
            markersize=3.0,
            linewidth=0,
            elinewidth=0.7,
            capsize=0,
            alpha=0.9,
        )
    else:
        ax.scatter(q, I, s=10, alpha=0.9)
    ax.set_xlabel("q (nm$^{-1}$)")
    ax.set_ylabel("I (a.u.)")
    ax.set_yscale("log")
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(out_path, format="png")
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0


def _render_csv_to_png(src_path: str, out_path: str) -> bool:
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

    ncols = max(len(r) for r in rows)
    for r in rows:
        if len(r) < ncols:
            r.extend([""] * (ncols - len(r)))

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


def prepare_viewer_raster(source_path: str) -> tuple[Optional[str], Optional[str]]:
    """
    Resolve an artifact path to a raster file suitable for ImageViewerDialog.

    Returns (raster_path, temp_png_to_unlink_or_none). Caller must unlink temp when done
    if temp_png_to_unlink_or_none is not None.
    """
    path = (source_path or "").strip()
    if not path or not os.path.exists(path):
        return None, None
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".png", ".jpg", ".jpeg", ".bmp"):
        return str(p), None
    # .dat curves use DatCurveViewerDialog (matplotlib toolbar), not raster ImageViewerDialog.
    if suffix not in (".tif", ".tiff", ".csv"):
        return None, None
    try:
        fd, out_path = tempfile.mkstemp(prefix="guisaxs_preview_", suffix=".png")
        os.close(fd)
    except Exception:
        return None, None
    ok = False
    try:
        if suffix in (".tif", ".tiff"):
            ok = _render_tiff_to_png(path, out_path)
        elif suffix == ".csv":
            ok = _render_csv_to_png(path, out_path)
    except Exception:
        ok = False
    if not ok:
        try:
            os.unlink(out_path)
        except Exception:
            pass
        return None, None
    return out_path, out_path


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
        self.setWindowTitle("Image")
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
        self._zoom.setToolTip("Zoom")
        self._zoom.currentTextChanged.connect(self._apply_zoom)

        self._path: Optional[str] = None
        self._pix: Optional[QPixmap] = None

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(self._zoom)
        top.addStretch(1)
        lay.addLayout(top)
        lay.addWidget(self._scroll, 1)

    def set_image_path(
        self,
        path: str,
        *,
        window_title: Optional[str] = None,
        full_path_tooltip: Optional[str] = None,
    ) -> None:
        self._path = path
        tip_source = (full_path_tooltip or path or "").strip()
        short, full_tip = contracted_path_label(tip_source) if tip_source else ("", "")
        tip = full_tip if full_tip else ""
        self.setToolTip(tip)
        self._scroll.setToolTip(tip)
        self._image.setToolTip(tip)
        if window_title:
            self.setWindowTitle(window_title)
        elif tip_source:
            self.setWindowTitle(f"Image — {short}")
        else:
            self.setWindowTitle("Image")
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
        self._label = QLabel("")
        self._label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._label.setWordWrap(True)

        self._image = _ClickableImageLabel()
        self._image.setAlignment(Qt.AlignCenter)
        self._image.set_on_click(None)

        self._current_image_path: Optional[str] = None
        self._current_pixmap: Optional[QPixmap] = None
        self._viewer: Optional[ImageViewerDialog] = None
        self._temp_preview_png: Optional[str] = None
        self._source_path_for_viewer: Optional[str] = None
        self._dat_curve_viewer: Optional["DatCurveViewerDialog"] = None
        self._preview_q_min: Any = None
        self._preview_q_max: Any = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._label)
        lay.addWidget(self._image, 1)

    def show_path(
        self,
        path: str,
        *,
        path_label_visible: bool = True,
        q_min: Any = None,
        q_max: Any = None,
    ) -> None:
        self._cleanup_temp_preview()
        self._source_path_for_viewer = None
        self._preview_q_min = q_min
        self._preview_q_max = q_max
        self._label.setText("")
        self._label.setToolTip("")
        self._image.clear()
        self._image.setToolTip("")
        self.setToolTip("")
        self._current_image_path = None
        self._current_pixmap = None
        self._image.set_on_click(None)
        if not path or not os.path.exists(path):
            return
        p = Path(path)
        self._source_path_for_viewer = str(p)
        short, full = contracted_path_label(p)
        if path_label_visible:
            self._label.setText(short)
            self._label.setToolTip(full)
        else:
            self._label.setText("")
            self._label.setToolTip(full)
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
            if not path_label_visible:
                self._image.setToolTip(full)
                self.setToolTip(full)

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
                ok = _render_tiff_to_png(src_path, out_path)
            elif suffix == ".dat":
                ok = _render_dat_to_png(
                    src_path,
                    out_path,
                    q_min=self._preview_q_min,
                    q_max=self._preview_q_max,
                )
            elif suffix == ".csv":
                ok = _render_csv_to_png(src_path, out_path)
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

    def _rescale_preview(self) -> None:
        if self._current_pixmap is None:
            return
        self._image.setPixmap(
            self._current_pixmap.scaled(self._image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def _open_viewer(self) -> None:
        src = self._source_path_for_viewer
        if src and Path(src).suffix.lower() == ".dat":
            from ..liveview.ui.widgets.plots import open_dat_curve_dialog

            self._dat_curve_viewer = open_dat_curve_dialog(
                self,
                src,
                reuse=self._dat_curve_viewer,
                q_min=self._preview_q_min,
                q_max=self._preview_q_max,
            )
            return
        if not self._current_image_path:
            return
        if self._viewer is None:
            self._viewer = ImageViewerDialog(self)
        self._viewer.set_image_path(
            self._current_image_path,
            full_path_tooltip=self._source_path_for_viewer or self._current_image_path,
        )
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


def open_image_viewer(
    parent: Optional[QWidget],
    source_path: str,
    *,
    reuse: Optional[ImageViewerDialog] = None,
    window_title: Optional[str] = None,
    full_path_tooltip: Optional[str] = None,
) -> Optional[ImageViewerDialog]:
    """
    Open the shared zoomable raster viewer (Fit / % zoom — same as left/right thumbnail click-through).
    Resolves .tif / .csv to a PNG preview when needed. For .dat curves use open_dat_curve_dialog instead.
    """
    raster, temp_png = prepare_viewer_raster(source_path)
    if not raster or not os.path.exists(raster):
        return reuse
    dlg = reuse if reuse is not None else ImageViewerDialog(parent)
    dlg.set_image_path(
        raster,
        window_title=window_title,
        full_path_tooltip=full_path_tooltip if full_path_tooltip is not None else source_path,
    )
    if temp_png:
        try:
            os.unlink(temp_png)
        except Exception:
            pass
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


def open_compare_sample_buffer_viewer(
    parent: Optional[QWidget],
    sample_path: str,
    buffer_path: str,
    *,
    subtract_options: Optional[Dict[str, Any]] = None,
    reuse: Optional["DatCurveViewerDialog"] = None,
) -> Optional["DatCurveViewerDialog"]:
    """Interactive sample + scaled buffer viewer (matplotlib toolbar); thin wrapper over open_compare_curves_dialog."""
    from ..liveview.ui.widgets.plots import open_compare_curves_dialog

    return open_compare_curves_dialog(
        parent,
        sample_path,
        buffer_path,
        subtract_options=subtract_options,
        reuse=reuse,
    )

