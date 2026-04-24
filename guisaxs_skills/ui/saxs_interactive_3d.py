"""
Interactive 3D viewer for DAMMIF / mmCIF dummy-atom models (shared Qt widget).

Uses the same isosurface path as autosaxs ``PLTViewer.plot_3d_views_and_scattering`` (Gaussian
density + marching cubes + ``Poly3DCollection``), with scatter fallback if the surface fails.

- ``embedded=True`` (right-panel thumbnail): no title/labels, axes hidden, auto-rotation, click opens
  fullscreen dialog.
- ``embedded=False`` (dialog): matplotlib navigation toolbar for rotate/zoom/pan.
"""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  # registers 3d projection
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QWidget


def _build_isosurface_poly3d(
    atoms: Any,
    *,
    grid_size: int,
    r_max: float,
    isosurface_sigma: float = 1.5,
    isosurface_level: Optional[float] = None,
    alpha: float = 0.82,
    edge_linewidth: float = 0.15,
) -> tuple[Optional[Poly3DCollection], np.ndarray, np.ndarray]:
    """
    Build a single-view isosurface mesh (same recipe as autosaxs ``viewer.py``).
    Returns (collection_or_none, min_coords, max_coords) in Å.
    """
    from matplotlib import cm
    from matplotlib.colors import Normalize

    from autosaxs.core.utils import calculate_atoms_density_and_isosurface

    density, level, min_coords, max_coords = calculate_atoms_density_and_isosurface(
        atoms,
        grid_size=grid_size,
        isosurface_sigma=isosurface_sigma,
        isosurface_level=isosurface_level,
    )
    try:
        from skimage.measure import marching_cubes
    except ImportError:
        return None, min_coords, max_coords

    com = atoms.get_center_of_mass()
    norm = Normalize(vmin=0, vmax=r_max)
    cmap = cm.viridis
    try:
        verts, faces, _, _ = marching_cubes(density, level=level)
    except ValueError:
        return None, min_coords, max_coords

    scale = (max_coords - min_coords) / (np.array(density.shape) - 1)
    verts = verts * scale + min_coords

    face_colors: list[tuple[float, float, float, float]] = []
    for face in faces:
        face_verts = verts[face]
        avg_dist = float(np.mean(np.linalg.norm(face_verts - com, axis=1)))
        face_colors.append(cmap(norm(avg_dist)))

    mesh = Poly3DCollection(
        verts[faces],
        alpha=alpha,
        facecolors=face_colors,
        edgecolor="k",
        linewidth=edge_linewidth,
    )
    return mesh, min_coords, max_coords


def _build_shape_isosurface_poly3d(
    shape_name: str,
    shape_params: dict[str, float],
    *,
    grid_size: int,
    r_max: float = 30.0,
    isosurface_level: Optional[float] = None,
    alpha: float = 0.82,
    edge_linewidth: float = 0.15,
) -> tuple[Optional[Poly3DCollection], np.ndarray, np.ndarray]:
    """
    Analytical BODIES shape isosurface (same pipeline as ``PLTViewer.plot_3d_views_and_scattering``).
    """
    from matplotlib import cm
    from matplotlib.colors import Normalize

    from autosaxs.core.utils import calculate_shape_density_and_isosurface

    density, level, min_coords, max_coords = calculate_shape_density_and_isosurface(
        (shape_name, shape_params),
        grid_size=grid_size,
        isosurface_level=isosurface_level,
    )
    try:
        from skimage.measure import marching_cubes
    except ImportError:
        return None, min_coords, max_coords

    com = np.array([0.0, 0.0, 0.0], dtype=np.float64)
    norm = Normalize(vmin=0, vmax=r_max)
    cmap = cm.viridis
    try:
        verts, faces, _, _ = marching_cubes(density, level=level)
    except ValueError:
        return None, min_coords, max_coords

    scale = (max_coords - min_coords) / (np.array(density.shape) - 1)
    verts = verts * scale + min_coords

    face_colors: list[tuple[float, float, float, float]] = []
    for face in faces:
        face_verts = verts[face]
        avg_dist = float(np.mean(np.linalg.norm(face_verts - com, axis=1)))
        face_colors.append(cmap(norm(avg_dist)))

    mesh = Poly3DCollection(
        verts[faces],
        alpha=alpha,
        facecolors=face_colors,
        edgecolor="k",
        linewidth=edge_linewidth,
    )
    return mesh, min_coords, max_coords


class SaxsInteractive3DWidget(QWidget):
    """
    Qt widget: 3D isosurface (preferred) or scatter of dummy atoms from ``.cif``.
    """

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        embedded: bool = False,
        grid_size: Optional[int] = None,
    ) -> None:
        super().__init__(parent)
        self._embedded = embedded
        self._grid_size = int(grid_size) if grid_size is not None else (44 if embedded else 64)
        self._title = ""
        self._last_cif_path: Optional[str] = None
        self._last_bodies_shape: Optional[str] = None
        self._last_bodies_params: Optional[dict[str, float]] = None
        self._full_view_cb: Optional[Callable[[], None]] = None
        self._click_cid: Optional[int] = None
        self._has_model = False

        figsize = (3.2, 3.0) if embedded else (5.0, 4.5)
        dpi = 100
        self._fig = Figure(figsize=figsize, dpi=dpi)
        self._fig.patch.set_alpha(0.0)
        self._canvas = FigureCanvas(self._fig)
        self._ax = self._fig.add_subplot(111, projection="3d")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._canvas, 1)
        if not embedded:
            self._toolbar = NavigationToolbar(self._canvas, self)
            lay.addWidget(self._toolbar, 0)
        else:
            self._toolbar = None

        self._timer = QTimer(self)
        self._timer.setInterval(50)
        self._timer.timeout.connect(self._tick_embedded_rotation)
        self._rotation_suspended = False
        self._anim_phase = 0
        self._anim_theta_deg = 0.0
        self._base_elev = 22.0
        self._base_azim = -60.0
        self._phase_duration_s = 20.0

        self._reset_empty_view()

    def set_full_view_callback(self, fn: Optional[Callable[[], None]]) -> None:
        self._full_view_cb = fn
        if self._embedded:
            self._canvas.setCursor(Qt.PointingHandCursor if fn is not None else Qt.ArrowCursor)

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._embedded and self._has_model and not self._rotation_suspended:
            self._timer.start()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        if self._embedded:
            self._timer.stop()
        super().hideEvent(event)

    def pause_embedded_rotation(self) -> None:
        """Stop the thumbnail spin (e.g. while the fullscreen 3D dialog is open)."""
        if not self._embedded:
            return
        self._rotation_suspended = True
        self._timer.stop()

    def resume_embedded_rotation_if_visible(self) -> None:
        """Resume spin after the fullscreen dialog closes."""
        if not self._embedded:
            return
        self._rotation_suspended = False
        if self._has_model and self.isVisible():
            self._timer.start()

    def _disconnect_click(self) -> None:
        if self._click_cid is not None:
            try:
                self._canvas.mpl_disconnect(self._click_cid)
            except Exception:
                pass
            self._click_cid = None

    def _on_canvas_click(self, event: Any) -> None:
        if not self._embedded or event.button != 1:
            return
        if self._full_view_cb is None or not self._has_model:
            return
        if event.inaxes != self._ax:
            return
        self._full_view_cb()

    def _apply_embedded_chrome(self) -> None:
        self._ax.set_axis_off()
        for axis in (self._ax.xaxis, self._ax.yaxis, self._ax.zaxis):
            axis.pane.set_visible(False)
            axis.line.set_visible(False)
        self._ax.set_title("")
        self._fig.subplots_adjust(left=0, right=1, bottom=0, top=1)

    def _apply_dialog_chrome(self) -> None:
        self._ax.set_axis_off()
        for axis in (self._ax.xaxis, self._ax.yaxis, self._ax.zaxis):
            axis.pane.set_visible(False)
        self._ax.set_title("")

    def _reset_empty_view(self) -> None:
        self._has_model = False
        self._rotation_suspended = False
        if self._embedded:
            self._timer.stop()
        self._disconnect_click()
        self._ax.clear()
        if self._embedded:
            self._apply_embedded_chrome()
            self._ax.text2D(
                0.5,
                0.5,
                "No model",
                transform=self._ax.transAxes,
                ha="center",
                va="center",
                fontsize=9,
                color="0.45",
            )
        else:
            self._apply_dialog_chrome()
            self._ax.text2D(
                0.5,
                0.5,
                "Load a .cif file",
                transform=self._ax.transAxes,
                ha="center",
                va="center",
            )
        self._ax.view_init(elev=self._base_elev, azim=self._base_azim)
        try:
            self._ax.set_box_aspect((1, 1, 1))
        except Exception:
            pass
        self._canvas.draw_idle()

    def set_title(self, title: str) -> None:
        self._title = title or ""
        if not self._embedded:
            self._ax.set_title(self._title)
            self._canvas.draw_idle()

    def clear(self) -> None:
        self._title = ""
        self._last_cif_path = None
        self._last_bodies_shape = None
        self._last_bodies_params = None
        self._reset_empty_view()

    def cif_path(self) -> Optional[str]:
        return self._last_cif_path

    def load_cif(self, path: str, *, title: Optional[str] = None) -> bool:
        p = Path(path)
        if not p.is_file():
            self.clear()
            return False
        try:
            from autosaxs.core.utils import read_bodies_cif

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                atoms = read_bodies_cif(str(p))
        except Exception:
            self.clear()
            return False
        self._last_cif_path = str(p)
        self._last_bodies_shape = None
        self._last_bodies_params = None
        t = title or p.name
        return self._load_atoms(atoms, title=t)

    def load_bodies_analytical(
        self,
        shape_name: str,
        shape_params: dict[str, float],
        *,
        title: Optional[str] = None,
    ) -> bool:
        """Isosurface from analytical BODIES shape (``calculate_shape_density_and_isosurface``)."""
        self._last_cif_path = None
        self._last_bodies_shape = shape_name
        self._last_bodies_params = dict(shape_params)
        self._disconnect_click()
        if self._embedded:
            self._timer.stop()

        mesh, min_c, max_c = _build_shape_isosurface_poly3d(
            shape_name,
            shape_params,
            grid_size=self._grid_size,
            r_max=30.0,
            alpha=0.82 if self._embedded else 0.85,
            edge_linewidth=0.12 if self._embedded else 0.18,
        )

        self._ax.clear()
        self._has_model = True
        center = np.asarray(min_c + max_c, dtype=np.float64) * 0.5

        if mesh is not None:
            self._ax.add_collection3d(mesh)
            self._set_equal_limits(center, np.asarray(min_c), np.asarray(max_c))
        else:
            self._ax.text2D(
                0.5,
                0.5,
                "Isosurface failed",
                transform=self._ax.transAxes,
                ha="center",
                va="center",
                fontsize=9,
                color="0.45",
            )
            self._ax.set_xlim(-1, 1)
            self._ax.set_ylim(-1, 1)
            self._ax.set_zlim(-1, 1)

        t = title or f"{shape_name} (analytical)"
        if self._embedded:
            self._apply_embedded_chrome()
            self._anim_phase = 0
            self._anim_theta_deg = 0.0
            self._ax.view_init(elev=self._base_elev, azim=self._base_azim)
            self._click_cid = self._canvas.mpl_connect("button_press_event", self._on_canvas_click)
            if self.isVisible() and not self._rotation_suspended:
                self._timer.start()
        else:
            self._apply_dialog_chrome()
            self._title = t
            self._ax.view_init(elev=self._base_elev, azim=self._base_azim)

        try:
            self._ax.set_box_aspect((1, 1, 1))
        except Exception:
            pass
        self._canvas.draw_idle()
        return True

    def bodies_model(self) -> tuple[Optional[str], Optional[dict[str, float]]]:
        return self._last_bodies_shape, self._last_bodies_params

    def _set_equal_limits(
        self,
        center: np.ndarray,
        min_c: np.ndarray,
        max_c: np.ndarray,
        *,
        pad_frac: float = 0.06,
    ) -> None:
        span = float(np.max(max_c - min_c))
        span = max(span, 1e-6)
        pad = span * pad_frac
        lim = span * 0.5 + pad
        self._ax.set_xlim(center[0] - lim, center[0] + lim)
        self._ax.set_ylim(center[1] - lim, center[1] + lim)
        self._ax.set_zlim(center[2] - lim, center[2] + lim)

    def _load_atoms(self, atoms: Any, *, title: str) -> bool:
        self._disconnect_click()
        if self._embedded:
            self._timer.stop()

        mesh, min_c, max_c = _build_isosurface_poly3d(
            atoms,
            grid_size=self._grid_size,
            r_max=30.0,
            alpha=0.82 if self._embedded else 0.85,
            edge_linewidth=0.12 if self._embedded else 0.18,
        )

        self._ax.clear()
        self._has_model = True
        center = np.asarray(min_c + max_c, dtype=np.float64) * 0.5

        if mesh is not None:
            self._ax.add_collection3d(mesh)
            self._set_equal_limits(center, np.asarray(min_c), np.asarray(max_c))
        else:
            pts = np.asarray(atoms.positions, dtype=np.float64)
            if pts.size == 0:
                self.clear()
                return False
            self._ax.scatter(
                pts[:, 0],
                pts[:, 1],
                pts[:, 2],
                s=7.0 if self._embedded else 10.0,
                c="#2a6ea6",
                alpha=0.75,
                depthshade=True,
                edgecolors="none",
            )
            lo = np.min(pts, axis=0)
            hi = np.max(pts, axis=0)
            center = (lo + hi) * 0.5
            span_v = hi - lo
            lim = max(float(np.max(span_v)) * 0.5 * 1.08, 1e-6)
            self._ax.set_xlim(center[0] - lim, center[0] + lim)
            self._ax.set_ylim(center[1] - lim, center[1] + lim)
            self._ax.set_zlim(center[2] - lim, center[2] + lim)

        if self._embedded:
            self._apply_embedded_chrome()
            self._anim_phase = 0
            self._anim_theta_deg = 0.0
            self._ax.view_init(elev=self._base_elev, azim=self._base_azim)
            self._click_cid = self._canvas.mpl_connect("button_press_event", self._on_canvas_click)
            if self.isVisible() and not self._rotation_suspended:
                self._timer.start()
        else:
            self._apply_dialog_chrome()
            if title:
                self._title = title
            self._ax.view_init(elev=self._base_elev, azim=self._base_azim)

        try:
            self._ax.set_box_aspect((1, 1, 1))
        except Exception:
            pass
        self._canvas.draw_idle()
        return True

    def _tick_embedded_rotation(self) -> None:
        if not self._embedded or not self._has_model or self._rotation_suspended:
            return
        dt = self._timer.interval() / 1000.0
        speed = 720.0 / max(self._phase_duration_s, 1e-6)
        self._anim_theta_deg += speed * dt
        if self._anim_theta_deg >= 720.0:
            self._anim_theta_deg = 0.0
            self._anim_phase = 1 - self._anim_phase

        if self._anim_phase == 0:
            self._ax.view_init(elev=self._base_elev, azim=self._base_azim + self._anim_theta_deg)
        else:
            self._ax.view_init(elev=self._base_elev + self._anim_theta_deg, azim=self._base_azim)

        self._canvas.draw_idle()

    def load_points(
        self,
        points: np.ndarray,
        *,
        title: Optional[str] = None,
        point_size: float = 8.0,
        alpha: float = 0.75,
    ) -> bool:
        """Scatter-only path (no isosurface) for raw Nx3 points."""
        pts = np.asarray(points, dtype=np.float64)
        if pts.ndim != 2 or pts.shape[1] != 3 or pts.shape[0] == 0:
            self.clear()
            return False
        self._last_cif_path = None
        self._last_bodies_shape = None
        self._last_bodies_params = None
        self._disconnect_click()
        if self._embedded:
            self._timer.stop()

        self._ax.clear()
        self._has_model = True
        self._ax.scatter(
            pts[:, 0],
            pts[:, 1],
            pts[:, 2],
            s=point_size,
            c="#2a6ea6",
            alpha=alpha,
            depthshade=True,
            edgecolors="none",
        )
        center = pts.mean(axis=0)
        span = (pts - center).max()
        span = float(max(span, 1e-6))
        pad = span * 0.05
        lim = span + pad
        self._ax.set_xlim(center[0] - lim, center[0] + lim)
        self._ax.set_ylim(center[1] - lim, center[1] + lim)
        self._ax.set_zlim(center[2] - lim, center[2] + lim)

        if self._embedded:
            self._apply_embedded_chrome()
            self._anim_phase = 0
            self._anim_theta_deg = 0.0
            self._ax.view_init(elev=self._base_elev, azim=self._base_azim)
            self._click_cid = self._canvas.mpl_connect("button_press_event", self._on_canvas_click)
            if self.isVisible() and not self._rotation_suspended:
                self._timer.start()
        else:
            self._apply_dialog_chrome()

        try:
            self._ax.set_box_aspect((1, 1, 1))
        except Exception:
            pass
        self._canvas.draw_idle()
        return True


class Interactive3DViewerDialog(QDialog):
    """Fullscreen-style 3D viewer with matplotlib toolbar (opened from embedded thumbnail click)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("3D model")
        self.resize(920, 720)
        self._plot = SaxsInteractive3DWidget(self, embedded=False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addWidget(self._plot, 1)

    def set_cif_path(self, path: str, *, window_title: Optional[str] = None) -> bool:
        ok = self._plot.load_cif(path)
        if window_title:
            self.setWindowTitle(window_title)
        else:
            short = Path(path).name
            self.setWindowTitle(f"3D — {short}")
        return ok

    def set_bodies_analytical(
        self,
        shape_name: str,
        shape_params: dict[str, float],
        *,
        window_title: Optional[str] = None,
    ) -> bool:
        ok = self._plot.load_bodies_analytical(shape_name, shape_params)
        if window_title:
            self.setWindowTitle(window_title)
        else:
            self.setWindowTitle(f"3D — {shape_name} (analytical)")
        return ok
