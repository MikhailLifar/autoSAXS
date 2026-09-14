from __future__ import annotations

from typing import Optional

import numpy as np
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT
from matplotlib.patches import Rectangle
from mpl_toolkits.axes_grid1 import make_axes_locatable
from PyQt5.QtCore import Qt, pyqtSignal

from ...widgets.plots import DropTiffImageCanvas
from .model import MaskMode, MaskModel, PaintPolarity


class MaskCanvas(DropTiffImageCanvas):
    """Matplotlib TIFF canvas with mask drawing overlays."""

    edited = pyqtSignal()

    def __init__(self, *, model: MaskModel) -> None:
        super().__init__()
        self._model = model
        self._toolbar: Optional[NavigationToolbar2QT] = None
        self._mask_overlay = None
        self._preview_overlay = None
        self._poly_line_artist = None
        self._poly_pts_artist = None
        self._shape_artists: list = []
        self._preview_mask: Optional[np.ndarray] = None
        self.mpl_connect("button_press_event", self._on_click)

    def set_toolbar(self, toolbar: Optional[NavigationToolbar2QT]) -> None:
        self._toolbar = toolbar

    def set_model(self, model: MaskModel) -> None:
        self._model = model
        self.refresh_overlays()

    def set_threshold_preview(self, mask: Optional[np.ndarray]) -> None:
        self._preview_mask = mask
        self.refresh_overlays()

    def show_blank_frame(self, shape_hw: tuple[int, int], *, title: str = "Mask (no image)") -> None:
        """
        Show a constant frame at the cmap minimum (black for viridis) so a mask
        overlay remains visible when no TIFF is loaded.
        """
        H, W = int(shape_hw[0]), int(shape_hw[1])
        if H <= 0 or W <= 0:
            self.clear()
            return
        blank = np.zeros((H, W), dtype=float)
        self._remove_colorbar()
        self._ax.clear()
        self._last_image_shape = (H, W)
        self._last_tiff_path = ""
        self._ax.set_title(title)
        self._ax.set_xlabel("x (px)")
        self._ax.set_ylabel("y (px)")
        im = self._ax.imshow(
            blank,
            cmap="viridis",
            origin="lower",
            aspect="equal",
            interpolation="nearest",
            vmin=0.0,
            vmax=1.0,
        )
        try:
            divider = make_axes_locatable(self._ax)
            self._cax = divider.append_axes("right", size="4%", pad=0.05)
            self._cbar = self._fig.colorbar(im, cax=self._cax)
            self._cbar.set_label("log(1 + I)")
        except Exception:
            self._cbar = None
            self._cax = None
        self._fig.tight_layout()
        self.draw_idle()
        self.setCursor(Qt.CrossCursor)

    def _is_left_click_in_axes(self, ev: object) -> bool:
        if getattr(ev, "inaxes", None) is None:
            return False
        return int(getattr(ev, "button", 0)) == 1

    def _on_click(self, ev: object) -> None:
        if self._toolbar is not None and str(getattr(self._toolbar, "mode", "") or ""):
            return
        if not self._is_left_click_in_axes(ev):
            return
        if self._model.mode == MaskMode.THRESHOLD:
            return
        x = getattr(ev, "xdata", None)
        y = getattr(ev, "ydata", None)
        if x is None or y is None:
            return
        try:
            x_f = float(x)
            y_f = float(y)
        except Exception:
            return
        mode = self._model.mode
        if mode == MaskMode.POLYGON:
            self._on_click_polygon(x_f, y_f, bool(getattr(ev, "dblclick", False)))
        elif mode == MaskMode.PIXEL:
            if bool(getattr(ev, "dblclick", False)):
                return
            self._model.toggle_pixel(x_f, y_f)
        elif mode == MaskMode.RECTANGULAR:
            if bool(getattr(ev, "dblclick", False)):
                return
            self._model.add_rect_click(x_f, y_f)
        self.edited.emit()
        self.refresh_overlays()

    def _on_click_polygon(self, x_f: float, y_f: float, dblclick: bool) -> None:
        if dblclick:
            if not self._model.current:
                self._model.add_point(x_f, y_f)
            else:
                lx, ly = self._model.current[-1]
                if abs(lx - x_f) > 1e-9 or abs(ly - y_f) > 1e-9:
                    self._model.add_point(x_f, y_f)
            self._model.finish_polygon()
        else:
            self._model.add_point(x_f, y_f)

    def _clear_overlay_artists(self) -> None:
        for a in (self._poly_line_artist, self._poly_pts_artist, self._mask_overlay, self._preview_overlay):
            if a is None:
                continue
            try:
                a.remove()
            except Exception:
                pass
        self._poly_line_artist = None
        self._poly_pts_artist = None
        self._mask_overlay = None
        self._preview_overlay = None
        for a in list(self._shape_artists):
            try:
                a.remove()
            except Exception:
                pass
        self._shape_artists = []

    def _stroke_for_polarity(self, pol: PaintPolarity) -> dict:
        if pol == PaintPolarity.UNMASK:
            return {"edgecolor": "#7ec8ff", "facecolor": "none", "linestyle": "--", "linewidth": 1.6}
        return {"edgecolor": "#ff6b6b", "facecolor": (1.0, 0.2, 0.2, 0.12), "linestyle": "-", "linewidth": 1.2}

    def refresh_overlays(self) -> None:
        ax = self._ax
        self._clear_overlay_artists()

        m = self._model.mask_union()
        if m is not None and m.size:
            try:
                rgba = np.zeros((m.shape[0], m.shape[1], 4), dtype=float)
                rgba[m, 0] = 1.0
                rgba[m, 3] = 0.40
                self._mask_overlay = ax.imshow(
                    rgba,
                    origin="lower",
                    aspect="equal",
                    interpolation="nearest",
                    zorder=10,
                )
            except Exception:
                self._mask_overlay = None

        if self._preview_mask is not None and self._preview_mask.size:
            try:
                pm = np.asarray(self._preview_mask, dtype=bool)
                rgba = np.zeros((pm.shape[0], pm.shape[1], 4), dtype=float)
                rgba[pm, 0] = 1.0
                rgba[pm, 1] = 0.35
                rgba[pm, 3] = 0.28
                self._preview_overlay = ax.imshow(
                    rgba,
                    origin="lower",
                    aspect="equal",
                    interpolation="nearest",
                    zorder=11,
                )
            except Exception:
                self._preview_overlay = None

        for verts, pol in self._model.polygons:
            if len(verts) < 2:
                continue
            xs = [p[0] for p in verts] + [verts[0][0]]
            ys = [p[1] for p in verts] + [verts[0][1]]
            style = self._stroke_for_polarity(pol)
            try:
                (ln,) = ax.plot(
                    xs,
                    ys,
                    color=style["edgecolor"],
                    linewidth=style["linewidth"],
                    linestyle=style["linestyle"],
                    alpha=0.95,
                    zorder=12,
                )
                self._shape_artists.append(ln)
            except Exception:
                pass

        for x0, y0, x1, y1, pol in self._model.rects:
            style = self._stroke_for_polarity(pol)
            try:
                rect = Rectangle(
                    (min(x0, x1), min(y0, y1)),
                    abs(x1 - x0),
                    abs(y1 - y0),
                    fill=style["facecolor"] != "none",
                    facecolor=style["facecolor"] if style["facecolor"] != "none" else (0, 0, 0, 0),
                    edgecolor=style["edgecolor"],
                    linestyle=style["linestyle"],
                    linewidth=style["linewidth"],
                    zorder=12,
                )
                ax.add_patch(rect)
                self._shape_artists.append(rect)
            except Exception:
                pass

        if self._model.rect_first is not None:
            x0, y0 = self._model.rect_first
            try:
                (pt,) = ax.plot(
                    [x0],
                    [y0],
                    marker="s",
                    linestyle="None",
                    color="white",
                    markersize=6,
                    alpha=0.95,
                    zorder=13,
                )
                self._shape_artists.append(pt)
            except Exception:
                pass

        cur = list(self._model.current)
        if cur:
            xs = [p[0] for p in cur]
            ys = [p[1] for p in cur]
            pol_style = self._stroke_for_polarity(self._model.paint_polarity)
            try:
                (pts,) = ax.plot(
                    xs, ys, marker="o", linestyle="None", color="white", markersize=4, alpha=0.95, zorder=13
                )
                self._poly_pts_artist = pts
            except Exception:
                self._poly_pts_artist = None
            if len(cur) >= 2:
                try:
                    (ln,) = ax.plot(
                        xs,
                        ys,
                        color=pol_style["edgecolor"],
                        linewidth=1.2,
                        linestyle=pol_style["linestyle"],
                        alpha=0.95,
                        zorder=13,
                    )
                    self._poly_line_artist = ln
                except Exception:
                    self._poly_line_artist = None

        self.draw_idle()
