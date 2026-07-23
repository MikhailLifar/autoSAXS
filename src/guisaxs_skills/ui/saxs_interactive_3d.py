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
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSlider,
    QVBoxLayout,
    QWidget,
)
from .isosurface_mesh_data import (
    DensityPointCloudData,
    IsosurfaceMeshData,
    compute_atoms_isosurface_mesh,
    compute_mrc_density_point_cloud,
    compute_shape_isosurface_mesh,
    mesh_data_to_poly3d,
)

_MRC_COLOR_MODES = ("density", "sigma")
_ELECTRON_BG = "#0b1220"
_ELECTRON_CMAP_STOPS = ("#062033", "#0b4f6c", "#0e7490", "#22d3ee", "#e0f7ff")


def _electron_cmap():
    from matplotlib.colors import LinearSegmentedColormap

    return LinearSegmentedColormap.from_list("electron_cold", list(_ELECTRON_CMAP_STOPS))


def _cloud_rgba(
    values: np.ndarray,
    *,
    alpha_min: float = 0.10,
    alpha_max: float = 0.88,
    high_is_bright: bool = True,
) -> tuple[np.ndarray, float, float, np.ndarray]:
    """
    Map scalar values → RGBA (cold electron cmap).

    When ``high_is_bright`` is False (σ mode), high values map to dim/dark + low alpha.
    Returns ``(rgba, vmin, vmax, t)`` where ``t`` is normalized strength in [0, 1]
    with the same orientation as the visual (1 = brightest / most opaque).
    """
    from matplotlib.colors import Normalize

    vals = np.asarray(values, dtype=np.float64)
    vmin = float(np.nanmin(vals))
    vmax = float(np.nanmax(vals))
    if not np.isfinite(vmin) or not np.isfinite(vmax) or abs(vmax - vmin) < 1e-15:
        vmax = vmin + 1.0
    norm = Normalize(vmin=vmin, vmax=vmax)
    t_raw = np.clip(norm(vals), 0.0, 1.0)
    t = t_raw if high_is_bright else (1.0 - t_raw)
    rgba = _electron_cmap()(t)
    rgba[:, 3] = alpha_min + (alpha_max - alpha_min) * t
    return rgba, vmin, vmax, t


def _subsample_cloud_by_weight(
    n: int,
    weight: np.ndarray,
    *,
    target: int,
    rng_seed: int = 1,
) -> np.ndarray:
    """Boolean keep-mask; prefer high ``weight`` (e.g. reliability = 1−σ)."""
    w = np.asarray(weight, dtype=np.float64)
    if n <= 0:
        return np.zeros(0, dtype=bool)
    if target >= n:
        return np.ones(n, dtype=bool)
    w = np.maximum(w, 0.0)
    s = float(np.sum(w))
    if not np.isfinite(s) or s <= 0:
        w = np.ones(n, dtype=np.float64)
        s = float(n)
    probs = w / s
    rng = np.random.default_rng(int(rng_seed))
    chosen = rng.choice(n, size=int(target), replace=False, p=probs)
    keep = np.zeros(n, dtype=bool)
    keep[chosen] = True
    return keep


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
            if kind == "overlap":
                from autosaxs.core.utils import read_bodies_cif

                # Optional: resolve aligned paths on the worker thread (cifsup).
                prepare = job.get("prepare")
                items = list(job.get("items") or [])
                if callable(prepare):
                    items = list(prepare() or [])
                meshes_out = []
                for it in items:
                    path = str(it.get("path") or "")
                    if not path or not Path(path).is_file():
                        continue
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        atoms = read_bodies_cif(path)
                    mesh = compute_atoms_isosurface_mesh(atoms, grid_size=grid_size, r_max=30.0)
                    pts = np.asarray(atoms.positions, dtype=np.float64)
                    meshes_out.append(
                        {
                            "path": path,
                            "label": it.get("label") or Path(path).name,
                            "rgba": it.get("rgba"),
                            "mesh": mesh,
                            "points": pts,
                        }
                    )
                self.finished.emit(
                    gen,
                    {
                        "kind": "overlap",
                        "title": job.get("title") or "Overlap",
                        "meshes": meshes_out,
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
            if kind == "mrc":
                density_path = str(job.get("density_path") or "")
                sigma_path = str(job.get("sigma_path") or "") or None
                max_points = int(job.get("max_points") or 12_000)
                cloud = compute_mrc_density_point_cloud(
                    density_path,
                    sigma_mrc=sigma_path,
                    max_points=max_points,
                )
                self.finished.emit(
                    gen,
                    {
                        "kind": "mrc",
                        "path": density_path,
                        "sigma_path": sigma_path or "",
                        "title": job.get("title") or "",
                        "cloud": cloud,
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
        self._last_mrc_path: Optional[str] = None
        self._last_sigma_path: Optional[str] = None
        self._mrc_cloud: Optional[DensityPointCloudData] = None
        self._mrc_color_mode: str = "density"
        self._mrc_title: str = ""
        self._density_dark: bool = False
        self._occ_points: Optional[np.ndarray] = None
        self._occ_values: Optional[np.ndarray] = None
        self._occ_threshold: float = 0.0
        self._occ_colorbar = None
        self._sigma_colorbar = None
        self._mrc_colorbar = None
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
        self._set_density_dark(False)
        self._ax.set_axis_off()
        for axis in (self._ax.xaxis, self._ax.yaxis, self._ax.zaxis):
            axis.pane.set_visible(False)
            axis.line.set_visible(False)
        self._ax.set_title("")
        if self._embedded:
            self._fig.subplots_adjust(left=0, right=1, bottom=0, top=1)
        else:
            self._fig.subplots_adjust(left=0.05, right=0.95, bottom=0.05, top=0.95)

    def _set_density_dark(self, enabled: bool) -> None:
        """Dark 'electron' theme for DENSS clouds; light theme for DAM/CIF."""
        self._density_dark = bool(enabled)
        if enabled:
            self._fig.patch.set_facecolor(_ELECTRON_BG)
            self._fig.patch.set_alpha(1.0)
            self._ax.set_facecolor(_ELECTRON_BG)
            try:
                self._canvas.setStyleSheet(f"background-color: {_ELECTRON_BG};")
            except Exception:
                pass
        else:
            self._fig.patch.set_facecolor("none")
            self._fig.patch.set_alpha(0.0)
            self._ax.set_facecolor("none")
            try:
                self._canvas.setStyleSheet("")
            except Exception:
                pass

    def _apply_scaled_axes_chrome(self) -> None:
        """Show Å tick labels and grid so model size can be read from the plot."""
        compact = self._embedded
        label_fs = 7 if compact else 10
        tick_fs = 6 if compact else 9
        dark = self._density_dark
        pane_alpha = 0.08 if dark else (0.12 if compact else 0.22)
        # Matplotlib rejects color=None; only pass colors for the dark (DENSS) theme.
        label_kw: dict = {"fontsize": label_fs, "labelpad": 2 if compact else 6}
        grid_kw: dict = {"linestyle": ":", "linewidth": 0.5, "alpha": 0.25 if dark else 0.55}
        if dark:
            label_kw["color"] = "#9fb3c8"
            grid_kw["color"] = "#6b8cae"

        self._ax.set_axis_on()
        self._ax.set_xlabel("x (Å)", **label_kw)
        self._ax.set_ylabel("y (Å)", **label_kw)
        self._ax.set_zlabel("z (Å)", **label_kw)
        self._ax.grid(True, **grid_kw)
        nbins = 4 if compact else 5
        for axis in (self._ax.xaxis, self._ax.yaxis, self._ax.zaxis):
            axis.pane.set_visible(True)
            axis.pane.set_alpha(pane_alpha)
            if dark:
                try:
                    axis.pane.set_facecolor(_ELECTRON_BG)
                    axis.pane.set_edgecolor("#1e3a4f")
                    axis.line.set_color("#4a6a82")
                except Exception:
                    pass
            axis.line.set_visible(True)
            axis.set_major_locator(MaxNLocator(nbins=nbins, prune=None))
        tick_kw = {"labelsize": tick_fs, "pad": 1 if compact else 3}
        if dark:
            tick_kw["colors"] = "#8aa0b5"
        self._ax.tick_params(**tick_kw)
        if self._embedded:
            self._ax.set_title("")
            self._fig.subplots_adjust(left=0.0, right=1.0, bottom=0.0, top=1.0)
        else:
            if self._title:
                title_kw: dict = {"fontsize": 11}
                if dark:
                    title_kw["color"] = "#c8d9e8"
                self._ax.set_title(self._title, **title_kw)
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
        self._last_mrc_path = None
        self._last_sigma_path = None
        self._mrc_cloud = None
        self._mrc_color_mode = "density"
        self._mrc_title = ""
        self._occ_points = None
        self._occ_values = None
        self._occ_threshold = 0.0
        self._clear_occ_colorbar()
        self._clear_sigma_colorbar()
        self._clear_mrc_colorbar()
        self._reset_empty_view()

    def cif_path(self) -> Optional[str]:
        return self._last_cif_path

    def _clear_occ_colorbar(self) -> None:
        if self._occ_colorbar is not None:
            try:
                self._occ_colorbar.remove()
            except Exception:
                pass
            self._occ_colorbar = None

    def _clear_sigma_colorbar(self) -> None:
        if self._sigma_colorbar is not None:
            try:
                self._sigma_colorbar.remove()
            except Exception:
                pass
            self._sigma_colorbar = None

    def _clear_mrc_colorbar(self) -> None:
        if self._mrc_colorbar is not None:
            try:
                self._mrc_colorbar.remove()
            except Exception:
                pass
            self._mrc_colorbar = None

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
            (message[:60] + "…") if len(message) > 60 else (message or "Could not load 3D"),
            transform=self._ax.transAxes,
            ha="center",
            va="center",
            fontsize=8,
            color="0.45",
        )
        self._canvas.draw_idle()

    def _on_mesh_finished(self, gen: int, payload: object) -> None:
        if gen != self._load_gen:
            return
        self._mesh_thread = None
        self._mesh_worker = None
        if not isinstance(payload, dict):
            self._on_mesh_failed(gen, "invalid payload")
            return
        kind = str(payload.get("kind") or "")
        if kind == "mrc":
            cloud = payload.get("cloud")
            if not isinstance(cloud, DensityPointCloudData) or cloud.xyz.size == 0:
                self._on_mesh_failed(gen, "empty density cloud")
                return
            self._last_mrc_path = str(payload.get("path") or "") or None
            self._last_sigma_path = str(payload.get("sigma_path") or "") or None
            self._mrc_cloud = cloud
            self._mrc_title = str(payload.get("title") or "")
            self._last_cif_path = None
            self._last_bodies_shape = None
            self._last_bodies_params = None
            if self._mrc_color_mode == "sigma" and cloud.sigma is None:
                self._mrc_color_mode = "density"
            self._redraw_mrc_cloud()
            return

        alpha = 0.82 if self._embedded else 0.85
        edge_lw = 0.12 if self._embedded else 0.18
        mesh_data = payload.get("mesh")
        self._disconnect_click()
        if self._embedded:
            self._timer.stop()
        self._mrc_cloud = None
        self._clear_occ_colorbar()
        self._clear_sigma_colorbar()
        self._clear_mrc_colorbar()
        self._set_density_dark(False)
        self._ax.clear()
        self._has_model = True

        if isinstance(mesh_data, IsosurfaceMeshData):
            mesh = mesh_data_to_poly3d(mesh_data, alpha=alpha, edge_linewidth=edge_lw)
            self._ax.add_collection3d(mesh)
            center = np.asarray(mesh_data.min_coords + mesh_data.max_coords, dtype=np.float64) * 0.5
            self._set_equal_limits(center, mesh_data.min_coords, mesh_data.max_coords)
            if (
                not self._embedded
                and mesh_data.colorbar_label
                and mesh_data.colorbar_vmin is not None
                and mesh_data.colorbar_vmax is not None
            ):
                from matplotlib import cm
                from matplotlib.colors import Normalize
                from matplotlib.cm import ScalarMappable

                norm = Normalize(vmin=float(mesh_data.colorbar_vmin), vmax=float(mesh_data.colorbar_vmax))
                sm = ScalarMappable(norm=norm, cmap=cm.viridis)
                sm.set_array([])
                self._sigma_colorbar = self._fig.colorbar(sm, ax=self._ax, fraction=0.046, pad=0.08)
                self._sigma_colorbar.set_label(str(mesh_data.colorbar_label), fontsize=9)
        elif kind == "overlap":
            meshes = payload.get("meshes") or []
            mins = []
            maxs = []
            legend_bits = []
            for item in meshes:
                if not isinstance(item, dict):
                    continue
                md = item.get("mesh")
                rgba = item.get("rgba")
                label = str(item.get("label") or "")
                if isinstance(md, IsosurfaceMeshData):
                    poly = mesh_data_to_poly3d(
                        md,
                        alpha=float(rgba[3]) if rgba and len(rgba) > 3 else 0.45,
                        edge_linewidth=edge_lw * 0.7,
                        rgba=tuple(rgba) if rgba else None,
                    )
                    self._ax.add_collection3d(poly)
                    mins.append(md.min_coords)
                    maxs.append(md.max_coords)
                    if rgba and label:
                        legend_bits.append((label, rgba))
                else:
                    pts = np.asarray(item.get("points"), dtype=np.float64)
                    if pts.ndim == 2 and pts.shape[1] == 3 and pts.shape[0]:
                        c = rgba[:3] if rgba else (0.2, 0.5, 0.8)
                        self._ax.scatter(
                            pts[:, 0],
                            pts[:, 1],
                            pts[:, 2],
                            s=6.0,
                            c=[c],
                            alpha=0.55,
                            depthshade=True,
                            edgecolors="none",
                        )
                        mins.append(np.min(pts, axis=0))
                        maxs.append(np.max(pts, axis=0))
                        if rgba and label:
                            legend_bits.append((label, rgba))
            if mins and maxs:
                lo = np.min(np.stack(mins, axis=0), axis=0)
                hi = np.max(np.stack(maxs, axis=0), axis=0)
                center = (lo + hi) * 0.5
                self._set_equal_limits(center, lo, hi)
            else:
                self._ax.text2D(
                    0.5,
                    0.5,
                    "No shapes selected",
                    transform=self._ax.transAxes,
                    ha="center",
                    va="center",
                    fontsize=9,
                    color="0.45",
                )
            # Compact color legend (top-left)
            y = 0.97
            for lab, rgba in legend_bits[:8]:
                short = lab if len(lab) <= 42 else lab[:39] + "…"
                self._ax.text2D(
                    0.02,
                    y,
                    f"● {short}",
                    transform=self._ax.transAxes,
                    ha="left",
                    va="top",
                    fontsize=7,
                    color=tuple(rgba[:3]),
                )
                y -= 0.035
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
            self._last_mrc_path = None
            self._last_sigma_path = None
        elif kind == "overlap":
            self._last_cif_path = None
            self._last_bodies_shape = None
            self._last_bodies_params = None
            self._last_mrc_path = None
            self._last_sigma_path = None
        elif kind == "bodies":
            self._last_cif_path = None
            self._last_bodies_shape = str(payload.get("shape_name") or "") or None
            params = payload.get("shape_params")
            self._last_bodies_params = dict(params) if isinstance(params, dict) else None
            self._last_mrc_path = None
            self._last_sigma_path = None

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

    def _redraw_mrc_cloud(self) -> bool:
        cloud = self._mrc_cloud
        if cloud is None or cloud.xyz.size == 0:
            return False
        mode = self._mrc_color_mode if self._mrc_color_mode in _MRC_COLOR_MODES else "density"
        if mode == "sigma":
            if cloud.sigma is None:
                mode = "density"
                self._mrc_color_mode = "density"
            values = cloud.sigma if cloud.sigma is not None else cloud.density
            cbar_label = "σ (density)"
            high_is_bright = False
        else:
            values = cloud.density
            cbar_label = "ρ (density)"
            high_is_bright = True

        self._disconnect_click()
        if self._embedded:
            self._timer.stop()
        self._clear_occ_colorbar()
        self._clear_sigma_colorbar()
        self._clear_mrc_colorbar()
        self._set_density_dark(True)
        self._ax.clear()
        self._has_model = True

        rgba, vmin, vmax, t_vis = _cloud_rgba(values, high_is_bright=high_is_bright)
        pts = cloud.xyz
        # σ mode: keep fewer points in high-σ regions (sparse where uncertain).
        if mode == "sigma":
            n = int(pts.shape[0])
            target = max(800, n // 3) if self._embedded else max(1500, n // 2)
            keep = _subsample_cloud_by_weight(n, t_vis, target=target, rng_seed=2)
            pts = pts[keep]
            rgba = rgba[keep]
            t_vis = t_vis[keep]

        self._ax.scatter(
            pts[:, 0],
            pts[:, 1],
            pts[:, 2],
            c=rgba,
            s=5.0 if self._embedded else 8.0,
            depthshade=False,
            edgecolors="none",
            linewidths=0,
        )
        if not self._embedded:
            try:
                from matplotlib.cm import ScalarMappable
                from matplotlib.colors import Normalize

                # Colorbar matches the drawn mapping (bright = reliable for σ).
                if high_is_bright:
                    sm = ScalarMappable(norm=Normalize(vmin=vmin, vmax=vmax), cmap=_electron_cmap())
                else:
                    sm = ScalarMappable(
                        norm=Normalize(vmin=vmin, vmax=vmax),
                        cmap=_electron_cmap().reversed(),
                    )
                sm.set_array([])
                self._mrc_colorbar = self._fig.colorbar(sm, ax=self._ax, fraction=0.046, pad=0.08)
                self._mrc_colorbar.set_label(cbar_label, fontsize=9, color="#c8d9e8")
                self._mrc_colorbar.ax.yaxis.set_tick_params(color="#8aa0b5")
                for lab in self._mrc_colorbar.ax.get_yticklabels():
                    lab.set_color("#8aa0b5")
            except Exception:
                self._mrc_colorbar = None

        center = np.asarray(cloud.min_coords + cloud.max_coords, dtype=np.float64) * 0.5
        self._set_equal_limits(center, cloud.min_coords, cloud.max_coords, pad_frac=0.08)
        self._title = "" if self._embedded else self._mrc_title
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

    def set_mrc_color_mode(self, mode: str) -> bool:
        """Toggle DENSS cloud coloring between ``density`` and ``sigma``."""
        key = str(mode).strip().lower()
        if key not in _MRC_COLOR_MODES:
            return False
        if key == "sigma" and (self._mrc_cloud is None or self._mrc_cloud.sigma is None):
            return False
        if key == self._mrc_color_mode and self._mrc_cloud is not None:
            return True
        self._mrc_color_mode = key
        if self._mrc_cloud is None:
            return True
        return self._redraw_mrc_cloud()

    def mrc_color_mode(self) -> str:
        return self._mrc_color_mode

    def has_mrc_sigma(self) -> bool:
        return self._mrc_cloud is not None and self._mrc_cloud.sigma is not None

    def load_cif(self, path: str, *, title: Optional[str] = None) -> bool:
        p = Path(path)
        if not p.is_file():
            self.clear()
            return False
        gen = self._bump_load_gen()
        self._last_cif_path = str(p)
        self._last_bodies_shape = None
        self._last_bodies_params = None
        self._last_mrc_path = None
        self._last_sigma_path = None
        self._mrc_cloud = None
        self._occ_points = None
        self._occ_values = None
        self._clear_occ_colorbar()
        self._clear_mrc_colorbar()
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

    def load_overlap(
        self,
        *,
        items: Optional[list] = None,
        prepare: Optional[Callable[[], list]] = None,
        title: str = "Overlap — aligned",
    ) -> bool:
        """Render multiple aligned isosurfaces. ``prepare`` runs on the worker thread (cifsup)."""
        if not items and prepare is None:
            self.clear()
            return False
        gen = self._bump_load_gen()
        self._last_cif_path = None
        self._last_bodies_shape = None
        self._last_bodies_params = None
        self._last_mrc_path = None
        self._last_sigma_path = None
        self._mrc_cloud = None
        self._occ_points = None
        self._occ_values = None
        self._clear_occ_colorbar()
        self._clear_mrc_colorbar()
        self._show_loading()
        self._start_mesh_job(
            {
                "gen": gen,
                "kind": "overlap",
                "items": list(items or []),
                "prepare": prepare,
                "title": title,
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
        self._last_mrc_path = None
        self._last_sigma_path = None
        self._mrc_cloud = None
        self._clear_mrc_colorbar()
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

    def load_mrc(
        self,
        density_path: str,
        *,
        sigma_path: Optional[str] = None,
        title: Optional[str] = None,
        color_mode: str = "density",
    ) -> bool:
        """Density point cloud from a DENSS MRC; optional σ for color/opacity toggle."""
        p = Path(density_path)
        if not p.is_file():
            self.clear()
            return False
        gen = self._bump_load_gen()
        self._last_cif_path = None
        self._last_bodies_shape = None
        self._last_bodies_params = None
        self._last_mrc_path = str(p)
        self._last_sigma_path = str(sigma_path) if sigma_path else None
        self._mrc_cloud = None
        mode = str(color_mode).strip().lower()
        self._mrc_color_mode = mode if mode in _MRC_COLOR_MODES else "density"
        self._mrc_title = title or p.name
        self._occ_points = None
        self._occ_values = None
        self._clear_occ_colorbar()
        self._clear_sigma_colorbar()
        self._clear_mrc_colorbar()
        self._show_loading()
        max_points = 6_000 if self._embedded else 12_000
        self._start_mesh_job(
            {
                "gen": gen,
                "kind": "mrc",
                "density_path": str(p),
                "sigma_path": str(sigma_path) if sigma_path else "",
                "title": title or p.name,
                "max_points": max_points,
            }
        )
        return True

    def bodies_model(self) -> tuple[Optional[str], Optional[dict[str, float]]]:
        return self._last_bodies_shape, self._last_bodies_params

    def mrc_model(self) -> tuple[Optional[str], Optional[str]]:
        return self._last_mrc_path, self._last_sigma_path

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

    def load_occupancy_cif(
        self,
        path: str,
        *,
        threshold: float = 0.0,
        title: Optional[str] = None,
    ) -> bool:
        """Scatter beads colored by occupancy; hide beads below ``threshold``."""
        from ..liveview.services.dam_models import read_cif_xyz_occupancy

        p = Path(path)
        if not p.is_file():
            self.clear()
            return False
        try:
            pts, occ = read_cif_xyz_occupancy(str(p))
        except Exception:
            self.clear()
            return False
        if pts.size == 0:
            self.clear()
            return False
        self._bump_load_gen()
        self._stop_mesh_worker()
        self._last_cif_path = str(p)
        self._last_bodies_shape = None
        self._last_bodies_params = None
        self._occ_points = pts
        self._occ_values = occ
        self._occ_threshold = float(threshold)
        self._title = "" if self._embedded else (title or p.name)
        return self._redraw_occupancy()

    def set_occupancy_threshold(self, threshold: float) -> bool:
        if self._occ_points is None or self._occ_values is None:
            return False
        self._occ_threshold = float(threshold)
        return self._redraw_occupancy()

    def _redraw_occupancy(self) -> bool:
        from matplotlib import cm
        from matplotlib.colors import Normalize

        pts = self._occ_points
        occ = self._occ_values
        if pts is None or occ is None or pts.size == 0:
            return False
        thr = float(self._occ_threshold)
        mask = occ >= thr
        if not np.any(mask):
            # Keep axes but show empty message
            self._disconnect_click()
            if self._embedded:
                self._timer.stop()
            self._clear_occ_colorbar()
            self._ax.clear()
            self._has_model = True
            self._ax.text2D(
                0.5,
                0.5,
                f"No beads with occupancy ≥ {thr:.2f}",
                transform=self._ax.transAxes,
                ha="center",
                va="center",
                fontsize=9,
                color="0.45",
            )
            self._apply_scaled_axes_chrome()
            self._canvas.draw_idle()
            return True

        sel_pts = pts[mask]
        sel_occ = occ[mask]
        self._disconnect_click()
        if self._embedded:
            self._timer.stop()
        self._clear_occ_colorbar()
        self._ax.clear()
        self._has_model = True
        vmin = float(np.min(occ))
        vmax = float(np.max(occ))
        if abs(vmax - vmin) < 1e-12:
            vmax = vmin + 1.0
        norm = Normalize(vmin=vmin, vmax=vmax)
        sc = self._ax.scatter(
            sel_pts[:, 0],
            sel_pts[:, 1],
            sel_pts[:, 2],
            c=sel_occ,
            cmap=cm.viridis,
            norm=norm,
            s=10 if self._embedded else 14,
            alpha=0.85,
            depthshade=True,
            edgecolors="none",
        )
        if not self._embedded:
            try:
                self._occ_colorbar = self._fig.colorbar(sc, ax=self._ax, shrink=0.65, pad=0.08)
                self._occ_colorbar.set_label("Occupancy")
            except Exception:
                self._occ_colorbar = None
        center = sel_pts.mean(axis=0)
        min_c = sel_pts.min(axis=0)
        max_c = sel_pts.max(axis=0)
        self._set_equal_limits(center, min_c, max_c)
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
    """Fullscreen-style 3D viewer with model selector, occupancy, and overlap controls."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("3D model")
        self.resize(920, 720)
        self._catalog = None  # Optional DamModelCatalog
        self._entries_by_index: list = []

        top = QHBoxLayout()
        top.addWidget(QLabel("Model:"))
        self._combo = QComboBox()
        self._combo.setMinimumWidth(360)
        self._combo.currentIndexChanged.connect(self._on_model_changed)
        top.addWidget(self._combo, 1)

        self._occ_row = QHBoxLayout()
        self._occ_label = QLabel("Occupancy ≥ 0.00")
        self._occ_slider = QSlider(Qt.Horizontal)
        self._occ_slider.setRange(0, 100)
        self._occ_slider.setValue(0)
        self._occ_slider.valueChanged.connect(self._on_occ_threshold_changed)
        self._occ_row.addWidget(self._occ_label)
        self._occ_row.addWidget(self._occ_slider, 1)
        self._occ_widget = QWidget()
        self._occ_widget.setLayout(self._occ_row)
        self._occ_widget.setVisible(False)

        self._denss_color_row = QHBoxLayout()
        self._denss_color_label = QLabel("Color by:")
        self._denss_color_combo = QComboBox()
        self._denss_color_combo.addItem("Density (ρ)", "density")
        self._denss_color_combo.addItem("Uncertainty (σ)", "sigma")
        self._denss_color_combo.currentIndexChanged.connect(self._on_denss_color_mode_changed)
        self._denss_color_row.addWidget(self._denss_color_label)
        self._denss_color_row.addWidget(self._denss_color_combo, 1)
        self._denss_color_widget = QWidget()
        self._denss_color_widget.setLayout(self._denss_color_row)
        self._denss_color_widget.setVisible(False)

        self._overlap_list = QListWidget()
        self._overlap_list.setMaximumHeight(110)
        self._overlap_list.setVisible(False)
        self._overlap_list.itemChanged.connect(self._on_overlap_checks_changed)

        self._plot = SaxsInteractive3DWidget(self, embedded=False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.addLayout(top)
        lay.addWidget(self._occ_widget)
        lay.addWidget(self._denss_color_widget)
        lay.addWidget(self._overlap_list)
        lay.addWidget(self._plot, 1)

    def set_dam_catalog(self, catalog: Any, *, select_key: str = "best") -> bool:
        """Populate dropdown from a ``DamModelCatalog`` and load ``select_key``."""
        self._catalog = catalog
        self._combo.blockSignals(True)
        self._combo.clear()
        self._entries_by_index = []
        select_idx = 0
        for i, entry in enumerate(getattr(catalog, "entries", []) or []):
            self._combo.addItem(entry.label)
            self._entries_by_index.append(entry)
            if entry.key == select_key:
                select_idx = i
        self._combo.setCurrentIndex(select_idx if self._entries_by_index else -1)
        self._combo.blockSignals(False)
        self._populate_overlap_checks()
        if not self._entries_by_index:
            self._occ_widget.setVisible(False)
            self._denss_color_widget.setVisible(False)
            self._overlap_list.setVisible(False)
            return False
        self.setWindowTitle("3D — DAM models")
        return self._load_entry(self._entries_by_index[select_idx])

    def set_cif_path(self, path: str, *, window_title: Optional[str] = None) -> bool:
        self._catalog = None
        self._entries_by_index = []
        self._combo.blockSignals(True)
        self._combo.clear()
        self._combo.addItem(Path(path).name)
        self._combo.blockSignals(False)
        self._occ_widget.setVisible(False)
        self._denss_color_widget.setVisible(False)
        self._overlap_list.setVisible(False)
        ok = self._plot.load_cif(path)
        if window_title:
            self.setWindowTitle(window_title)
        else:
            self.setWindowTitle(f"3D — {Path(path).name}")
        return ok

    def set_bodies_analytical(
        self,
        shape_name: str,
        shape_params: dict[str, float],
        *,
        window_title: Optional[str] = None,
    ) -> bool:
        self._catalog = None
        self._entries_by_index = []
        self._combo.blockSignals(True)
        self._combo.clear()
        self._combo.addItem(f"{shape_name} (analytical)")
        self._combo.blockSignals(False)
        self._occ_widget.setVisible(False)
        self._denss_color_widget.setVisible(False)
        self._overlap_list.setVisible(False)
        ok = self._plot.load_bodies_analytical(shape_name, shape_params)
        if window_title:
            self.setWindowTitle(window_title)
        else:
            self.setWindowTitle(f"3D — {shape_name} (analytical)")
        return ok

    def set_denss_catalog(self, catalog: Any, *, select_key: str = "primary") -> bool:
        """Populate dropdown from a ``DenssModelCatalog`` and load ``select_key``."""
        self._catalog = catalog
        self._combo.blockSignals(True)
        self._combo.clear()
        self._entries_by_index = []
        select_idx = 0
        for i, entry in enumerate(getattr(catalog, "entries", []) or []):
            self._combo.addItem(entry.label)
            self._entries_by_index.append(entry)
            if entry.key == select_key:
                select_idx = i
        self._combo.setCurrentIndex(select_idx if self._entries_by_index else -1)
        self._combo.blockSignals(False)
        self._occ_widget.setVisible(False)
        self._overlap_list.setVisible(False)
        if not self._entries_by_index:
            self._denss_color_widget.setVisible(False)
            return False
        self.setWindowTitle("3D — DENSS density")
        return self._load_entry(self._entries_by_index[select_idx])

    def _populate_overlap_checks(self) -> None:
        self._overlap_list.blockSignals(True)
        self._overlap_list.clear()
        catalog = self._catalog
        if catalog is None:
            self._overlap_list.blockSignals(False)
            return
        candidates = list(getattr(catalog, "overlay_candidates", lambda: [])())
        if not candidates:
            best = catalog.best() if hasattr(catalog, "best") else None
            if best is not None and getattr(best, "kind", "") == "dam":
                candidates = [best]
        for entry in candidates:
            item = QListWidgetItem(entry.label)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setData(Qt.UserRole, entry.key)
            item.setCheckState(Qt.Checked if entry.is_most_probable else Qt.Unchecked)
            # If nothing marked most probable, check the first item.
            self._overlap_list.addItem(item)
        if self._overlap_list.count() and not any(
            self._overlap_list.item(i).checkState() == Qt.Checked
            for i in range(self._overlap_list.count())
        ):
            self._overlap_list.item(0).setCheckState(Qt.Checked)
        self._overlap_list.blockSignals(False)

    def _selected_overlap_keys(self) -> list:
        keys = []
        for i in range(self._overlap_list.count()):
            item = self._overlap_list.item(i)
            if item.checkState() == Qt.Checked:
                key = item.data(Qt.UserRole)
                if key:
                    keys.append(str(key))
        return keys

    def _on_model_changed(self, index: int) -> None:
        if index < 0 or index >= len(self._entries_by_index):
            return
        self._load_entry(self._entries_by_index[index])

    def _on_occ_threshold_changed(self, value: int) -> None:
        thr = float(value) / 100.0
        self._occ_label.setText(f"Occupancy ≥ {thr:.2f}")
        self._plot.set_occupancy_threshold(thr)

    def _on_denss_color_mode_changed(self, _index: int) -> None:
        mode = self._denss_color_combo.currentData()
        if mode:
            self._plot.set_mrc_color_mode(str(mode))

    def _sync_denss_color_controls(self, *, has_sigma: bool) -> None:
        self._denss_color_widget.setVisible(True)
        self._denss_color_combo.blockSignals(True)
        self._denss_color_combo.setItemData(1, "sigma")
        # Disable σ option when no ensemble map.
        model = self._denss_color_combo.model()
        if model is not None:
            from PyQt5.QtGui import QStandardItemModel

            if isinstance(model, QStandardItemModel):
                item = model.item(1)
                if item is not None:
                    item.setEnabled(bool(has_sigma))
        if not has_sigma:
            self._denss_color_combo.setCurrentIndex(0)
            self._plot.set_mrc_color_mode("density")
        else:
            # Keep current selection if valid.
            cur = self._denss_color_combo.currentData()
            if cur == "sigma":
                self._plot.set_mrc_color_mode("sigma")
            else:
                self._plot.set_mrc_color_mode("density")
        self._denss_color_combo.blockSignals(False)

    def _on_overlap_checks_changed(self, _item: QListWidgetItem = None) -> None:
        # Only refresh while overlap view is active.
        idx = self._combo.currentIndex()
        if idx < 0 or idx >= len(self._entries_by_index):
            return
        if getattr(self._entries_by_index[idx], "kind", "") != "overlap":
            return
        self._load_overlap_view()

    def _load_overlap_view(self) -> bool:
        from ..liveview.services.dam_models import prepare_overlap_items

        catalog = self._catalog
        if catalog is None:
            return False
        keys = self._selected_overlap_keys()
        if not keys:
            # Keep axes empty rather than forcing a selection.
            return self._plot.load_overlap(items=[], title="Overlap — none selected")

        def _prepare():
            return prepare_overlap_items(catalog, selected_keys=keys)

        self.setWindowTitle("3D — Overlap (aligned)")
        return self._plot.load_overlap(prepare=_prepare, title="Overlap — aligned")

    def _load_entry(self, entry: Any) -> bool:
        kind = getattr(entry, "kind", "dam")
        path = getattr(entry, "cif_path", "") or getattr(entry, "mrc_path", "")
        label = getattr(entry, "label", Path(path).name if path else "model")
        self.setWindowTitle(f"3D — {label}")
        if kind == "density":
            self._occ_widget.setVisible(False)
            self._overlap_list.setVisible(False)
            sigma = getattr(entry, "sigma_path", None) or None
            has_sigma = bool(sigma and Path(str(sigma)).is_file())
            self._sync_denss_color_controls(has_sigma=has_sigma)
            mode = self._denss_color_combo.currentData() or "density"
            if mode == "sigma" and not has_sigma:
                mode = "density"
            return self._plot.load_mrc(path, sigma_path=sigma, title=label, color_mode=str(mode))
        if kind == "occupancy":
            self._occ_widget.setVisible(True)
            self._denss_color_widget.setVisible(False)
            self._overlap_list.setVisible(False)
            thr = float(self._occ_slider.value()) / 100.0
            self._occ_label.setText(f"Occupancy ≥ {thr:.2f}")
            return self._plot.load_occupancy_cif(path, threshold=thr, title=label)
        if kind == "overlap":
            self._occ_widget.setVisible(False)
            self._denss_color_widget.setVisible(False)
            self._overlap_list.setVisible(True)
            try:
                return self._load_overlap_view()
            except Exception as exc:
                QMessageBox.warning(self, "Overlap view", str(exc))
                return False
        self._occ_widget.setVisible(False)
        self._denss_color_widget.setVisible(False)
        self._overlap_list.setVisible(False)
        return self._plot.load_cif(path, title=label)
