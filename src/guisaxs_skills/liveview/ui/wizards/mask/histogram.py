from __future__ import annotations

from typing import Optional

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, Rectangle
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .model import default_log1p_band


class IntensityHistogramPanel(QWidget):
    """
    Interactive log1p(I) histogram with a dual-handle keep band.

    Pixels outside [lo, hi] are masked. Raw I < 0 never enter the keep band
    (no intensity shifting).
    """

    range_changed = pyqtSignal(float, float)
    range_committed = pyqtSignal(float, float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._intensity: Optional[np.ndarray] = None
        self._lo = 0.0
        self._hi = 1.0
        self._default_lo = 0.0
        self._default_hi = 1.0
        self._hist_x: Optional[np.ndarray] = None
        self._hist_y: Optional[np.ndarray] = None
        self._drag: Optional[str] = None  # "lo" | "hi" | "span"
        self._span_anchor = 0.0
        self._neg_count = 0

        self._fig = Figure(figsize=(5.0, 1.8), dpi=100)
        self._fig.patch.set_facecolor("#1b1f24")
        self._ax = self._fig.add_subplot(111)
        self._canvas = FigureCanvas(self._fig)
        self._canvas.setMinimumHeight(140)

        self._lbl_lo = QLabel("lo: 0.00")
        self._lbl_hi = QLabel("hi: 0.00")
        self._lbl_neg = QLabel("")
        for lbl in (self._lbl_lo, self._lbl_hi, self._lbl_neg):
            lbl.setStyleSheet("color: #c8d0d8; font-size: 11px;")

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(self._lbl_lo)
        top.addStretch(1)
        top.addWidget(self._lbl_neg)
        top.addStretch(1)
        top.addWidget(self._lbl_hi)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addWidget(self._canvas, 1)

        self._canvas.mpl_connect("button_press_event", self._on_press)
        self._canvas.mpl_connect("button_release_event", self._on_release)
        self._canvas.mpl_connect("motion_notify_event", self._on_motion)
        self._canvas.mpl_connect("button_press_event", self._on_dblclick)

        self._redraw_empty()

    def lo(self) -> float:
        return float(self._lo)

    def hi(self) -> float:
        return float(self._hi)

    def set_intensity(self, intensity: Optional[np.ndarray]) -> None:
        self._intensity = None if intensity is None else np.asarray(intensity, dtype=float)
        self._rebuild_histogram()
        self.reset_to_default()

    def reset_to_default(self) -> None:
        if self._intensity is None:
            self._lo, self._hi = 0.0, 1.0
            self._default_lo, self._default_hi = self._lo, self._hi
        else:
            self._lo, self._hi = default_log1p_band(self._intensity)
            self._default_lo, self._default_hi = self._lo, self._hi
        self._update_labels()
        self._redraw()
        self.range_changed.emit(self._lo, self._hi)

    def set_range(self, lo: float, hi: float, *, emit: bool = True) -> None:
        lo_f, hi_f = float(lo), float(hi)
        if hi_f < lo_f:
            lo_f, hi_f = hi_f, lo_f
        self._lo, self._hi = lo_f, hi_f
        self._update_labels()
        self._redraw()
        if emit:
            self.range_changed.emit(self._lo, self._hi)

    def _rebuild_histogram(self) -> None:
        self._hist_x = None
        self._hist_y = None
        self._neg_count = 0
        if self._intensity is None:
            return
        data = self._intensity
        finite = np.isfinite(data)
        self._neg_count = int(np.sum(finite & (data < 0.0)))
        nonneg = finite & (data >= 0.0)
        if not np.any(nonneg):
            return
        logv = np.log1p(data[nonneg])
        lo = float(np.min(logv))
        hi = float(np.max(logv))
        if not np.isfinite(lo) or not np.isfinite(hi):
            return
        if hi <= lo:
            hi = lo + 1e-6
        counts, edges = np.histogram(logv, bins=64, range=(lo, hi))
        centers = 0.5 * (edges[:-1] + edges[1:])
        self._hist_x = centers
        self._hist_y = counts.astype(float)

    def _update_labels(self) -> None:
        self._lbl_lo.setText(f"lo: {self._lo:.3g}")
        self._lbl_hi.setText(f"hi: {self._hi:.3g}")
        if self._neg_count > 0:
            self._lbl_neg.setText(f"{self._neg_count} raw I<0 → masked")
        else:
            self._lbl_neg.setText("keep band (outside = masked)")

    def _redraw_empty(self) -> None:
        ax = self._ax
        ax.clear()
        ax.set_facecolor("#12161a")
        ax.text(0.5, 0.5, "Load an image to edit intensity thresholds", ha="center", va="center", color="#6a7380")
        ax.set_xticks([])
        ax.set_yticks([])
        for spine in ax.spines.values():
            spine.set_visible(False)
        self._fig.tight_layout(pad=0.4)
        self._canvas.draw_idle()

    def _redraw(self) -> None:
        ax = self._ax
        ax.clear()
        ax.set_facecolor("#12161a")
        if self._hist_x is None or self._hist_y is None or self._intensity is None:
            self._redraw_empty()
            return

        x = self._hist_x
        y = self._hist_y
        width = (x[1] - x[0]) if len(x) > 1 else 1.0
        # Full histogram (muted)
        ax.bar(x, y, width=width * 0.92, color="#3a4550", edgecolor="none", alpha=0.85, zorder=1)
        # Kept band highlight
        keep = (x >= self._lo) & (x <= self._hi)
        if np.any(keep):
            ax.bar(
                x[keep],
                y[keep],
                width=width * 0.92,
                color="#5ec8a0",
                edgecolor="none",
                alpha=0.95,
                zorder=2,
            )
        # Tail wash
        ymax = float(np.max(y)) if y.size else 1.0
        ymax = max(ymax, 1.0)
        ax.add_patch(
            Rectangle(
                (x[0] - width, 0),
                max(0.0, self._lo - (x[0] - width)),
                ymax * 1.15,
                facecolor="#ff4d4d",
                alpha=0.12,
                edgecolor="none",
                zorder=0,
            )
        )
        ax.add_patch(
            Rectangle(
                (self._hi, 0),
                max(0.0, (x[-1] + width) - self._hi),
                ymax * 1.15,
                facecolor="#ff4d4d",
                alpha=0.12,
                edgecolor="none",
                zorder=0,
            )
        )
        # Track + handles
        ax.axvline(self._lo, color="#f0f4f8", linewidth=1.4, alpha=0.95, zorder=4)
        ax.axvline(self._hi, color="#f0f4f8", linewidth=1.4, alpha=0.95, zorder=4)
        handle_y = ymax * 1.02
        for xv in (self._lo, self._hi):
            ax.plot([xv], [handle_y], marker="o", markersize=9, color="#f5f7fa", markeredgecolor="#1b1f24", zorder=5)
        ax.add_patch(
            FancyBboxPatch(
                (self._lo, ymax * 0.02),
                max(1e-9, self._hi - self._lo),
                ymax * 0.08,
                boxstyle="round,pad=0.01,rounding_size=0.02",
                facecolor="#5ec8a0",
                edgecolor="none",
                alpha=0.55,
                zorder=3,
            )
        )

        ax.set_xlim(x[0] - width, x[-1] + width)
        ax.set_ylim(0, ymax * 1.2)
        ax.set_xlabel("log(1 + I)  (I ≥ 0)", color="#9aa3ad", fontsize=9)
        ax.tick_params(colors="#9aa3ad", labelsize=8)
        for spine in ax.spines.values():
            spine.set_color("#2a323a")
        ax.set_yticks([])
        self._fig.tight_layout(pad=0.5)
        self._canvas.draw_idle()

    def _x_to_value(self, xdata: float) -> float:
        if self._hist_x is None or len(self._hist_x) == 0:
            return float(xdata)
        xmin = float(self._hist_x[0])
        xmax = float(self._hist_x[-1])
        return float(np.clip(xdata, xmin, xmax))

    def _on_press(self, ev: object) -> None:
        if getattr(ev, "dblclick", False):
            return
        if getattr(ev, "inaxes", None) is not self._ax:
            return
        if int(getattr(ev, "button", 0)) != 1:
            return
        x = getattr(ev, "xdata", None)
        if x is None:
            return
        xv = self._x_to_value(float(x))
        d_lo = abs(xv - self._lo)
        d_hi = abs(xv - self._hi)
        span = max(self._hi - self._lo, 1e-9)
        hit = 0.03 * max(abs(self._hi), abs(self._lo), 1.0)
        if d_lo <= d_hi and d_lo <= hit:
            self._drag = "lo"
        elif d_hi <= hit:
            self._drag = "hi"
        elif self._lo <= xv <= self._hi:
            self._drag = "span"
            self._span_anchor = xv
        else:
            # Jump nearest handle
            if d_lo <= d_hi:
                self._drag = "lo"
                self.set_range(xv, self._hi)
            else:
                self._drag = "hi"
                self.set_range(self._lo, xv)

    def _on_motion(self, ev: object) -> None:
        if self._drag is None:
            return
        if getattr(ev, "inaxes", None) is not self._ax:
            return
        x = getattr(ev, "xdata", None)
        if x is None:
            return
        xv = self._x_to_value(float(x))
        if self._drag == "lo":
            self.set_range(min(xv, self._hi), self._hi)
        elif self._drag == "hi":
            self.set_range(self._lo, max(xv, self._lo))
        elif self._drag == "span":
            delta = xv - self._span_anchor
            self._span_anchor = xv
            width = self._hi - self._lo
            new_lo = self._lo + delta
            new_hi = new_lo + width
            if self._hist_x is not None and len(self._hist_x):
                xmin = float(self._hist_x[0])
                xmax = float(self._hist_x[-1])
                if new_lo < xmin:
                    new_lo = xmin
                    new_hi = new_lo + width
                if new_hi > xmax:
                    new_hi = xmax
                    new_lo = new_hi - width
            self.set_range(new_lo, new_hi)

    def _on_release(self, ev: object) -> None:
        if self._drag is None:
            return
        self._drag = None
        self.range_committed.emit(self._lo, self._hi)

    def _on_dblclick(self, ev: object) -> None:
        if not getattr(ev, "dblclick", False):
            return
        if getattr(ev, "inaxes", None) is not self._ax:
            return
        self.reset_to_default()
        self.range_committed.emit(self._lo, self._hi)
