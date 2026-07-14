"""
Interactive 3D viewer for DAMMIF / mmCIF dummy-atom models (shared Qt widget).

Uses the same isosurface path as autosaxs ``PLTViewer.plot_3d_views_and_scattering`` (Gaussian
density + marching cubes + ``Poly3DCollection``), with scatter fallback if the surface fails.

Coordinates are in ångströms (Å), matching ATSAS DAMMIF / BODIES outputs. When a model is loaded,
axes show tick labels and a light grid so the physical scale can be read from the view.

- ``embedded=True`` (right-panel thumbnail): compact axis labels, auto-rotation, click opens
  fullscreen dialog.
- ``embedded=False`` (dialog): larger tick labels plus matplotlib navigation toolbar.
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
from matplotlib.ticker import MaxNLocator
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  # registers 3d projection
from PyQt5.QtCore import QObject, Qt, QThread, QTimer, pyqtSignal, pyqtSlot
from PyQt5.QtWidgets import QDialog, QVBoxLayout, QWidget

from .isosurface_mesh_data import (
    IsosurfaceMeshData,
    compute_atoms_isosurface_mesh,
    compute_shape_isosurface_mesh,
    mesh_data_to_poly3d,
)


class _IsosurfaceWorker(QObject):
    """Runs CIF read + marching cubes off the GUI thread."""

    finished = pyqtSignal(int, object)  # generation, payload dict
    failed = pyqtSignal(int, str)

    def __init__(self) -> None:
        super().__init__()
        self._job: Optional[dict[str, Any]] = None

    def configure(self, job: dict[str, Any]) -> None:
        self._job = dict(job)

    @pyqtSlot()
    def run(self) -> None:
        job = self._job or {}
        gen = int(job.get("gen", 0))
        try:
            kind = str(job.get("kind") or "")
            grid_size = int(job.get("grid_size") or 44)
            if kind == "cif":
                path = str(job.get("path") or "")
                from autosaxs.core.utils import read_bodies_cif

                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    atoms = read_bodies_cif(path)
                mesh = compute_atoms_isosurface_mesh(atoms, grid_size=grid_size, r_max=30.0)
                pts = np.asarray(atoms.positions, dtype=np.float64)
                self.finished.emit(
                    gen,
                    {
                        "kind": "cif",
                        "path": path,
                        "title": job.get("title") or "",
                        "mesh": mesh,
                        "points": pts,
                    },
                )
                return
            if kind == "bodies":
                shape_name = str(job.get("shape_name") or "")
                shape_params = dict(job.get("shape_params") or {})
                mesh = compute_shape_isosurface_mesh(
                    shape_name, shape_params, grid_size=grid_size, r_max=30.0
                )
                self.finished.emit(
                    gen,
                    {
                        "kind": "bodies",
                        "shape_name": shape_name,
                        "shape_params": shape_params,
                        "title": job.get("title") or "",
                        "mesh": mesh,
                    },
                )
                return
            self.failed.emit(gen, f"Unknown isosurface job kind: {kind!r}")
        except Exception as exc:
            self.failed.emit(gen, str(exc) or exc.__class__.__name__)


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
        self._load_gen = 0
        self._mesh_thread: Optional[QThread] = None
        self._mesh_worker: Optional[_IsosurfaceWorker] = None

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

    def _apply_empty_chrome(self) -> None:
        self._ax.set_axis_off()
        for axis in (self._ax.xaxis, self._ax.yaxis, self._ax.zaxis):
            axis.pane.set_visible(False)
            axis.line.set_visible(False)
        self._ax.set_title("")
        if self._embedded:
            self._fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
        else:
            self._fig.subplots_adjust(left=0.05, right=0.95, bottom=0.05, top=0.95)

    def _apply_scaled_axes_chrome(self) -> None:
        """Show Å tick labels and grid so model size can be read from the plot."""
        compact = self._embedded
        label_fs = 7 if compact else 10
        tick_fs = 6 if compact else 9
        pane_alpha = 0.12 if compact else 0.22

        self._ax.set_axis_on()
        self._ax.set_xlabel("x (Å)", fontsize=label_fs, labelpad=2 if compact else 6)
        self._ax.set_ylabel("y (Å)", fontsize=label_fs, labelpad=2 if compact else 6)
        self._ax.set_zlabel("z (Å)", fontsize=label_fs, labelpad=2 if compact else 6)
        self._ax.grid(True, linestyle=":", linewidth=0.5, alpha=0.55)
        nbins = 4 if compact else 5
        for axis in (self._ax.xaxis, self._ax.yaxis, self._ax.zaxis):
            axis.pane.set_visible(True)
            axis.pane.set_alpha(pane_alpha)
            axis.line.set_visible(True)
            axis.set_major_locator(MaxNLocator(nbins=nbins, prune=None))
        self._ax.tick_params(labelsize=tick_fs, pad=1 if compact else 3)
        if self._embedded:
            self._ax.set_title("")
            self._fig.subplots_adjust(left=0.0, right=1.0, bottom=0.0, top=1.0)
        else:
            if self._title:
                self._ax.set_title(self._title, fontsize=11)
            else:
                self._ax.set_title("")
            self._fig.subplots_adjust(left=0.02, right=0.98, bottom=0.02, top=0.96)

    def _reset_empty_view(self) -> None:
        self._has_model = False
        self._rotation_suspended = False
        if self._embedded:
            self._timer.stop()
        self._disconnect_click()
        self._ax.clear()
        self._apply_empty_chrome()
        if self._embedded:
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
        self._bump_load_gen()
        self._stop_mesh_worker()
        self._title = ""
        self._last_cif_path = None
        self._last_bodies_shape = None
        self._last_bodies_params = None
        self._reset_empty_view()

    def cif_path(self) -> Optional[str]:
        return self._last_cif_path

    def _bump_load_gen(self) -> int:
        self._load_gen += 1
        return self._load_gen

    def _stop_mesh_worker(self) -> None:
        thr = self._mesh_thread
        self._mesh_thread = None
        self._mesh_worker = None
        if thr is None:
            return
        try:
            thr.quit()
            thr.wait(100)
        except Exception:
            pass

    def _show_loading(self) -> None:
        self._disconnect_click()
        if self._embedded:
            self._timer.stop()
        self._has_model = False
        self._ax.clear()
        self._apply_empty_chrome()
        self._ax.text2D(
            0.5,
            0.5,
            "Loading 3D…",
            transform=self._ax.transAxes,
            ha="center",
            va="center",
            fontsize=9,
            color="0.45",
        )
        self._ax.view_init(elev=self._base_elev, azim=self._base_azim)
        try:
            self._ax.set_box_aspect((1, 1, 1))
        except Exception:
            pass
        self._canvas.draw_idle()

    def _start_mesh_job(self, job: dict[str, Any]) -> None:
        self._stop_mesh_worker()
        worker = _IsosurfaceWorker()
        thread = QThread(self)
        worker.moveToThread(thread)
        worker.configure(job)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_mesh_finished)
        worker.failed.connect(self._on_mesh_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        self._mesh_worker = worker
        self._mesh_thread = thread
        thread.start()

    def _on_mesh_failed(self, gen: int, message: str) -> None:
        if gen != self._load_gen:
            return
        self._mesh_thread = None
        self._mesh_worker = None
        self._reset_empty_view()
        self._ax.clear()
        self._apply_empty_chrome()
        self._ax.text2D(
            0.5,
            0.5,
            "Could not load 3D",
            transform=self._ax.transAxes,
            ha="center",
            va="center",
            fontsize=9,
            color="0.45",
        )
        self._canvas.draw_idle()
        _ = message

    def _on_mesh_finished(self, gen: int, payload: object) -> None:
        if gen != self._load_gen:
            return
        self._mesh_thread = None
        self._mesh_worker = None
        if not isinstance(payload, dict):
            self._on_mesh_failed(gen, "invalid payload")
            return
        kind = str(payload.get("kind") or "")
        alpha = 0.82 if self._embedded else 0.85
        edge_lw = 0.12 if self._embedded else 0.18
        mesh_data = payload.get("mesh")
        self._disconnect_click()
        if self._embedded:
            self._timer.stop()
        self._ax.clear()
        self._has_model = True

        if isinstance(mesh_data, IsosurfaceMeshData):
            mesh = mesh_data_to_poly3d(mesh_data, alpha=alpha, edge_linewidth=edge_lw)
            self._ax.add_collection3d(mesh)
            center = np.asarray(mesh_data.min_coords + mesh_data.max_coords, dtype=np.float64) * 0.5
            self._set_equal_limits(center, mesh_data.min_coords, mesh_data.max_coords)
        elif kind == "cif":
            pts = np.asarray(payload.get("points"), dtype=np.float64)
            if pts.ndim != 2 or pts.shape[1] != 3 or pts.shape[0] == 0:
                self._on_mesh_failed(gen, "empty atoms")
                return
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

        title = str(payload.get("title") or "")
        if kind == "cif":
            self._last_cif_path = str(payload.get("path") or "") or None
            self._last_bodies_shape = None
            self._last_bodies_params = None
        elif kind == "bodies":
            self._last_cif_path = None
            self._last_bodies_shape = str(payload.get("shape_name") or "") or None
            params = payload.get("shape_params")
            self._last_bodies_params = dict(params) if isinstance(params, dict) else None

        self._title = "" if self._embedded else title
        self._apply_scaled_axes_chrome()
        if self._embedded:
            self._anim_phase = 0
            self._anim_theta_deg = 0.0
            self._ax.view_init(elev=self._base_elev, azim=self._base_azim)
            self._click_cid = self._canvas.mpl_connect("button_press_event", self._on_canvas_click)
            if self.isVisible() and not self._rotation_suspended:
                self._timer.start()
        else:
            self._ax.view_init(elev=self._base_elev, azim=self._base_azim)
        try:
            self._ax.set_box_aspect((1, 1, 1))
        except Exception:
            pass
        self._canvas.draw_idle()

    def load_cif(self, path: str, *, title: Optional[str] = None) -> bool:
        p = Path(path)
        if not p.is_file():
            self.clear()
            return False
        gen = self._bump_load_gen()
        self._last_cif_path = str(p)
        self._last_bodies_shape = None
        self._last_bodies_params = None
        self._show_loading()
        self._start_mesh_job(
            {
                "gen": gen,
                "kind": "cif",
                "path": str(p),
                "title": title or p.name,
                "grid_size": self._grid_size,
            }
        )
        return True

    def load_bodies_analytical(
        self,
        shape_name: str,
        shape_params: dict[str, float],
        *,
        title: Optional[str] = None,
    ) -> bool:
        """Isosurface from analytical BODIES shape (async marching cubes)."""
        gen = self._bump_load_gen()
        self._last_cif_path = None
        self._last_bodies_shape = shape_name
        self._last_bodies_params = dict(shape_params)
        self._show_loading()
        self._start_mesh_job(
            {
                "gen": gen,
                "kind": "bodies",
                "shape_name": shape_name,
                "shape_params": dict(shape_params),
                "title": title or f"{shape_name} (analytical)",
                "grid_size": self._grid_size,
            }
        )
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

        self._title = "" if self._embedded else (title or "")
        self._apply_scaled_axes_chrome()
        if self._embedded:
            self._anim_phase = 0
            self._anim_theta_deg = 0.0
            self._ax.view_init(elev=self._base_elev, azim=self._base_azim)
            self._click_cid = self._canvas.mpl_connect("button_press_event", self._on_canvas_click)
            if self.isVisible() and not self._rotation_suspended:
                self._timer.start()
        else:
            self._ax.view_init(elev=self._base_elev, azim=self._base_azim)

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
