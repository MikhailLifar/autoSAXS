from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT
from matplotlib.figure import Figure
from mpl_toolkits.axes_grid1 import make_axes_locatable
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QDialog, QSizePolicy, QVBoxLayout, QWidget


def mpl_navigation_toolbar(canvas: FigureCanvas, parent: QWidget) -> NavigationToolbar2QT:
    """Matplotlib zoom / pan / home / save toolbar for figure dialogs (same affordances everywhere)."""
    return NavigationToolbar2QT(canvas, parent)


class LogCurvePlot(FigureCanvas):
    def __init__(self) -> None:
        self._fig = Figure(figsize=(4, 3), dpi=100)
        super().__init__(self._fig)
        self._ax = self._fig.add_subplot(111)
        self._x_label = "q (nm$^{-1}$)"
        self._ax.set_xlabel(self._x_label)
        self._ax.set_ylabel("I (a.u.)")
        self._ax.set_yscale("log")

    def set_x_label(self, label: str) -> None:
        self._x_label = str(label)
        self._ax.set_xlabel(self._x_label)
        self.draw_idle()

    def clear(self) -> None:
        self._ax.clear()
        self._ax.set_xlabel(self._x_label)
        self._ax.set_ylabel("I (a.u.)")
        self._ax.set_yscale("log")
        self.draw_idle()
        self.setCursor(Qt.ArrowCursor)

    def plot_dat(self, path: str, *, label: Optional[str] = None) -> None:
        from autosaxs.utils import read_saxs

        q, I, _sigma, _meta = read_saxs(path)
        q = np.asarray(q)
        I = np.asarray(I)
        m = np.isfinite(q) & np.isfinite(I) & (I > 0)
        self._ax.clear()
        if m.any():
            self._ax.plot(q[m], I[m], label=label or path)
        if label:
            self._ax.legend(fontsize=8)
        self._ax.set_title(Path(path).name)
        self._ax.set_xlabel(self._x_label)
        self._ax.set_ylabel("I (a.u.)")
        self._ax.set_yscale("log")
        self._ax.grid(True, alpha=0.2)
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor if m.any() else Qt.ArrowCursor)

    def plot_sample_and_scaled_buffer(
        self,
        sample_path: str,
        buffer_path: str,
        *,
        subtract_options: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        log10(I) vs q for sample and buffer after the same scaling subtract uses (point_match / match_tail).
        """
        from autosaxs.processor import subtract_buffer

        method, mtops = subtract_options_to_match_tail_ops(subtract_options or {})
        fd, tmp = tempfile.mkstemp(suffix=".dat")
        os.close(fd)
        try:
            q, I_sub, I_buff_scaled, *_rest = subtract_buffer(
                buffer_path,
                sample_path,
                tmp,
                method=method,
                match_tail_ops=mtops,
            )
        except Exception:
            self.clear()
            self._ax.set_title("Could not build sample/buffer overlay")
            self.draw_idle()
            return
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass

        I_sample = np.asarray(I_sub, dtype=float) + np.asarray(I_buff_scaled, dtype=float)
        q = np.asarray(q, dtype=float)
        m_s = np.isfinite(q) & np.isfinite(I_sample) & (I_sample > 0)
        m_b = np.isfinite(q) & np.isfinite(I_buff_scaled) & (I_buff_scaled > 0)
        self._ax.clear()
        if m_s.any():
            self._ax.plot(q[m_s], I_sample[m_s], label="sample", linewidth=1.0)
        if m_b.any():
            self._ax.plot(q[m_b], I_buff_scaled[m_b], label="buffer (scaled)", linewidth=1.0)
        self._ax.legend(fontsize=8)
        self._ax.set_title("Sample + scaled buffer")
        self._ax.set_xlabel(self._x_label)
        self._ax.set_ylabel("I (a.u.)")
        self._ax.set_yscale("log")
        self._ax.grid(True, alpha=0.2)
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor if (m_s.any() or m_b.any()) else Qt.ArrowCursor)


class DatCurveViewerDialog(QDialog):
    """
    Full-window interactive SAXS curve view: matplotlib NavigationToolbar (zoom, pan, home, save) + LogCurvePlot.
    Used for .dat artifacts; thumbnails elsewhere stay rasterized for speed.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Curve viewer")
        self.resize(1100, 800)
        self._plot = LogCurvePlot()
        lay = QVBoxLayout(self)
        lay.addWidget(mpl_navigation_toolbar(self._plot, self))
        lay.addWidget(self._plot, 1)

    def plot_panel(self) -> LogCurvePlot:
        return self._plot

    def show_single_dat(
        self,
        path: str,
        *,
        x_label: Optional[str] = None,
        curve_label: Optional[str] = None,
        window_title: Optional[str] = None,
    ) -> None:
        if window_title:
            self.setWindowTitle(window_title)
        else:
            self.setWindowTitle(f"Curve viewer — {Path(path).name}")
        if x_label is not None:
            self._plot.set_x_label(x_label)
        try:
            self._plot.plot_dat(path, label=curve_label)
        except Exception:
            self._plot.clear()
            if self._plot.figure.axes:
                self._plot.figure.axes[0].set_title("Could not load curve")
            self._plot.draw_idle()

    def show_sample_buffer_compare(
        self,
        sample_path: str,
        buffer_path: str,
        *,
        subtract_options: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.setWindowTitle(f"Sample + scaled buffer — {Path(sample_path).name} / {Path(buffer_path).name}")
        self._plot.set_x_label("q (nm$^{-1}$)")
        self._plot.plot_sample_and_scaled_buffer(
            sample_path,
            buffer_path,
            subtract_options=subtract_options,
        )


def open_dat_curve_dialog(
    parent: Optional[QWidget],
    path: str,
    *,
    reuse: Optional[DatCurveViewerDialog] = None,
    x_label: Optional[str] = None,
    curve_label: Optional[str] = None,
    window_title: Optional[str] = None,
) -> Optional[DatCurveViewerDialog]:
    """Open or refresh an interactive .dat curve viewer (matplotlib zoom/pan toolbar)."""
    p = (path or "").strip()
    if not p or not os.path.isfile(p) or Path(p).suffix.lower() != ".dat":
        return reuse
    dlg = reuse if reuse is not None else DatCurveViewerDialog(parent)
    dlg.show_single_dat(p, x_label=x_label, curve_label=curve_label, window_title=window_title)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


def open_compare_curves_dialog(
    parent: Optional[QWidget],
    sample_path: str,
    buffer_path: str,
    *,
    subtract_options: Optional[Dict[str, Any]] = None,
    reuse: Optional[DatCurveViewerDialog] = None,
) -> Optional[DatCurveViewerDialog]:
    """Interactive sample + scaled buffer overlay (same model as the small compare plot)."""
    sp = (sample_path or "").strip()
    bp = (buffer_path or "").strip()
    if not sp or not bp or not os.path.isfile(sp) or not os.path.isfile(bp):
        return reuse
    dlg = reuse if reuse is not None else DatCurveViewerDialog(parent)
    dlg.show_sample_buffer_compare(sp, bp, subtract_options=subtract_options)
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()
    return dlg


def subtract_options_to_match_tail_ops(opts: Dict[str, Any]) -> tuple[str, Optional[dict]]:
    """Mirror autosaxs.skill.subtract match_tail_ops construction from option kwargs."""
    method_key = str(opts.get("method", "point_match")).strip().lower().replace("-", "_")
    match_tail_ops: dict = {}
    q_min = opts.get("q_min")
    q_max = opts.get("q_max")
    if q_min is not None and q_max is not None:
        match_tail_ops["q_range_abs"] = (float(q_min), float(q_max))
    if method_key == "point_match":
        match_tail_ops["sample_form"] = str(opts.get("sample_form", "Porod-plus-linear"))
        match_tail_ops["buffer_form"] = str(opts.get("buffer_form", "linear"))
        match_tail_ops["point_match_factor"] = float(opts.get("point_match_factor", 0.995))
    return method_key, match_tail_ops if match_tail_ops else None


def _is_tif_path(path: str) -> bool:
    p = path.lower()
    return p.endswith(".tif") or p.endswith(".tiff")


class Image2DPlot(FigureCanvas):
    def __init__(self) -> None:
        self._fig = Figure(figsize=(4, 3), dpi=100)
        super().__init__(self._fig)
        self._ax = self._fig.add_subplot(111)
        self._cbar = None
        self._cax = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.updateGeometry()

    def _remove_colorbar(self) -> None:
        if self._cbar is not None:
            try:
                self._cbar.remove()
            except Exception:
                pass
            self._cbar = None
        if self._cax is not None:
            try:
                self._cax.remove()
            except Exception:
                pass
            self._cax = None

    def clear(self) -> None:
        self._remove_colorbar()
        self._ax.clear()
        self.draw_idle()
        self.setCursor(Qt.ArrowCursor)

    def show_tiff(self, path: str) -> None:
        # Display-only load. Prefer fabio, fallback to tifffile.
        arr = None
        try:
            import fabio

            arr = fabio.open(path).data
        except Exception:
            arr = None
        if arr is None:
            try:
                import tifffile

                arr = tifffile.imread(path)
            except Exception:
                arr = None
        if arr is None:
            self.clear()
            return

        a = np.asarray(arr)
        # If multi-frame, show first.
        if a.ndim > 2:
            a = a.reshape((-1,) + a.shape[-2:])[0]
        a = np.asarray(a, dtype=float)
        a = np.log1p(np.maximum(a, 0.0))

        self._remove_colorbar()
        self._ax.clear()

        self._ax.set_title(Path(path).name)
        self._ax.set_xlabel("x (px)")
        self._ax.set_ylabel("y (px)")
        im = self._ax.imshow(
            a,
            cmap="viridis",
            origin="lower",
            aspect="equal",  # never stretch pixels
            interpolation="nearest",
        )
        # Dedicated colorbar axes (avoid repeated fig.colorbar(ax=...) shrinking the image axes).
        try:
            divider = make_axes_locatable(self._ax)
            self._cax = divider.append_axes("right", size="5%", pad=0.05)
            self._cbar = self._fig.colorbar(im, cax=self._cax)
            self._cbar.set_label("log(1 + I)")
        except Exception:
            self._cbar = None
            self._cax = None
        self.draw_idle()
        self.setCursor(Qt.PointingHandCursor)


class DropTiffImageCanvas(Image2DPlot):
    """
    Same 2D matplotlib canvas as Image2DPlot, but accepts drag-and-drop of .tif/.tiff only
    (no browse, no text field). Emits absolute paths of dropped files.
    """

    tiff_files_dropped = pyqtSignal(object)  # list[str]

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self._draw_drop_hint()

    def clear(self) -> None:
        super().clear()
        self._draw_drop_hint()

    def _draw_drop_hint(self) -> None:
        self._ax.text(
            0.5,
            0.5,
            "Drop .tif / .tiff here",
            transform=self._ax.transAxes,
            ha="center",
            va="center",
            alpha=0.5,
            fontsize=11,
        )
        self.draw_idle()

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                local = url.toLocalFile()
                if local and os.path.isfile(local) and _is_tif_path(local):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dragMoveEvent(self, event) -> None:  # type: ignore[override]
        self.dragEnterEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        paths: list[str] = []
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                local = url.toLocalFile()
                if not local or not os.path.isfile(local):
                    continue
                if not _is_tif_path(local):
                    continue
                paths.append(os.path.abspath(local))
        if paths:
            self.tiff_files_dropped.emit(paths)
        event.setDropAction(Qt.CopyAction)
        event.accept()
