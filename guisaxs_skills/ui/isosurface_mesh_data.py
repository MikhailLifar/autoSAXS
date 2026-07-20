"""Pure (no-Qt) isosurface mesh computation for interactive 3D viewers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import numpy as np


@dataclass(frozen=True)
class IsosurfaceMeshData:
    """Triangle mesh ready to wrap in ``Poly3DCollection`` on the GUI thread."""

    verts: np.ndarray  # (N, 3)
    faces: np.ndarray  # (M, 3) int
    face_colors: np.ndarray  # (M, 4) float
    min_coords: np.ndarray  # (3,)
    max_coords: np.ndarray  # (3,)
    colorbar_label: Optional[str] = None
    colorbar_vmin: Optional[float] = None
    colorbar_vmax: Optional[float] = None


@dataclass(frozen=True)
class DensityPointCloudData:
    """Subsampled voxel cloud for DENSS density (and optional σ) visualization."""

    xyz: np.ndarray  # (N, 3) Å
    density: np.ndarray  # (N,) electron density at sampled voxels
    sigma: Optional[np.ndarray]  # (N,) or None
    min_coords: np.ndarray  # (3,) AABB of suprathreshold density (Å), not full MRC box
    max_coords: np.ndarray  # (3,)


def compute_atoms_isosurface_mesh(
    atoms: Any,
    *,
    grid_size: int,
    r_max: float = 30.0,
    isosurface_sigma: float = 1.5,
    isosurface_level: Optional[float] = None,
) -> Optional[IsosurfaceMeshData]:
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
        return None
    try:
        verts, faces, _, _ = marching_cubes(density, level=level)
    except ValueError:
        return None

    com = atoms.get_center_of_mass()
    norm = Normalize(vmin=0, vmax=r_max)
    cmap = cm.viridis
    scale = (max_coords - min_coords) / (np.array(density.shape) - 1)
    verts = verts * scale + min_coords
    face_colors = np.empty((len(faces), 4), dtype=np.float64)
    for i, face in enumerate(faces):
        face_verts = verts[face]
        avg_dist = float(np.mean(np.linalg.norm(face_verts - com, axis=1)))
        face_colors[i] = cmap(norm(avg_dist))
    return IsosurfaceMeshData(
        verts=np.asarray(verts, dtype=np.float64),
        faces=np.asarray(faces, dtype=np.int64),
        face_colors=face_colors,
        min_coords=np.asarray(min_coords, dtype=np.float64),
        max_coords=np.asarray(max_coords, dtype=np.float64),
    )


def compute_shape_isosurface_mesh(
    shape_name: str,
    shape_params: dict[str, float],
    *,
    grid_size: int,
    r_max: float = 30.0,
    isosurface_level: Optional[float] = None,
) -> Optional[IsosurfaceMeshData]:
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
        return None
    try:
        verts, faces, _, _ = marching_cubes(density, level=level)
    except ValueError:
        return None

    com = np.array([0.0, 0.0, 0.0], dtype=np.float64)
    norm = Normalize(vmin=0, vmax=r_max)
    cmap = cm.viridis
    scale = (max_coords - min_coords) / (np.array(density.shape) - 1)
    verts = verts * scale + min_coords
    face_colors = np.empty((len(faces), 4), dtype=np.float64)
    for i, face in enumerate(faces):
        face_verts = verts[face]
        avg_dist = float(np.mean(np.linalg.norm(face_verts - com, axis=1)))
        face_colors[i] = cmap(norm(avg_dist))
    return IsosurfaceMeshData(
        verts=np.asarray(verts, dtype=np.float64),
        faces=np.asarray(faces, dtype=np.int64),
        face_colors=face_colors,
        min_coords=np.asarray(min_coords, dtype=np.float64),
        max_coords=np.asarray(max_coords, dtype=np.float64),
    )


def _mrc_grid_coords(rho: np.ndarray, side) -> tuple[np.ndarray, np.ndarray, float]:
    """Return ``(min_coords, max_coords, voxel)`` for a cubic DENSS MRC."""
    n = int(rho.shape[0])
    side_f = float(np.asarray(side).reshape(-1)[0]) if np.size(side) else float(n)
    voxel = side_f / float(max(n, 1))
    half = 0.5 * side_f
    min_coords = np.array([-half, -half, -half], dtype=np.float64)
    max_coords = np.array([half, half, half], dtype=np.float64)
    return min_coords, max_coords, voxel


def compute_mrc_density_point_cloud(
    density_mrc: str,
    *,
    sigma_mrc: Optional[str] = None,
    level_fraction: float = 0.15,
    max_points: int = 12_000,
    rng_seed: int = 0,
) -> Optional[DensityPointCloudData]:
    """
    Point cloud of voxels with ρ ≥ ``level_fraction * ρ_max``.

    Points are drawn only above the threshold. Local point density encodes ρ by
    allocating samples ∝ ρ (with sub-voxel jitter), capped at ``max_points``.
    Optional ``sigma`` is taken from the parent voxel for a ρ/σ color-mode toggle.
    ``min_coords`` / ``max_coords`` are the particle AABB from suprathreshold voxels
    (padded), not the oversized DENSS MRC box.
    """
    try:
        import denss
    except ImportError as exc:
        raise RuntimeError("DENSS is required to load MRC density maps (pip install denss).") from exc

    rho, side = denss.read_mrc(str(density_mrc))
    rho = np.asarray(rho, dtype=np.float64)
    if rho.ndim != 3 or rho.size == 0:
        return None
    rho_max = float(np.nanmax(rho))
    if not np.isfinite(rho_max) or rho_max <= 0:
        return None
    level = float(level_fraction) * rho_max
    mask = np.isfinite(rho) & (rho >= level)
    if not np.any(mask):
        return None

    box_min, _box_max, voxel = _mrc_grid_coords(rho, side)
    flat_idx = np.flatnonzero(mask)
    weights = np.maximum(rho.ravel()[flat_idx], 0.0)
    wsum = float(np.sum(weights))
    if not np.isfinite(wsum) or wsum <= 0:
        return None

    # View limits follow the particle, not the large empty MRC cube.
    ijk_core = np.column_stack(np.unravel_index(flat_idx, rho.shape)).astype(np.float64)
    xyz_core = ijk_core * voxel + box_min
    core_span = float(np.max(xyz_core.max(axis=0) - xyz_core.min(axis=0)))
    pad = max(0.5 * voxel, 0.10 * max(core_span, 1e-6))
    part_min = xyz_core.min(axis=0) - pad
    part_max = xyz_core.max(axis=0) + pad

    rng = np.random.default_rng(int(rng_seed))
    n_target = int(max(int(max_points), 1))
    n_vox = int(flat_idx.size)
    probs = weights / wsum
    if n_target <= n_vox:
        chosen_local = rng.choice(n_vox, size=n_target, replace=False, p=probs)
        sel = flat_idx[chosen_local]
        jitter = 0.35
    else:
        # Oversample coarse grids: denser voxels get more jittered points.
        counts = rng.multinomial(n_target, probs)
        reps = np.repeat(np.arange(n_vox), counts)
        sel = flat_idx[reps]
        jitter = 0.45

    ijk = np.column_stack(np.unravel_index(sel, rho.shape)).astype(np.float64)
    ijk = ijk + rng.uniform(-jitter, jitter, size=ijk.shape)
    xyz = ijk * voxel + box_min
    density = rho.ravel()[sel].astype(np.float64)

    sigma_vals: Optional[np.ndarray] = None
    if sigma_mrc and Path(sigma_mrc).is_file():
        sigma, _side_s = denss.read_mrc(str(sigma_mrc))
        sigma = np.asarray(sigma, dtype=np.float64)
        if sigma.shape != rho.shape:
            raise RuntimeError(
                f"σ map shape {sigma.shape} does not match density shape {rho.shape}"
            )
        sigma_vals = sigma.ravel()[sel].astype(np.float64)

    return DensityPointCloudData(
        xyz=xyz,
        density=density,
        sigma=sigma_vals,
        min_coords=np.asarray(part_min, dtype=np.float64),
        max_coords=np.asarray(part_max, dtype=np.float64),
    )


def compute_mrc_isosurface_mesh(
    density_mrc: str,
    *,
    sigma_mrc: Optional[str] = None,
    level_fraction: float = 0.15,
    solid_rgba: tuple = (0.20, 0.45, 0.75, 0.85),
) -> Optional[IsosurfaceMeshData]:
    """
    Isosurface from a DENSS density MRC (legacy / optional path).

    When ``sigma_mrc`` is set, face colors encode mean σ on each triangle (viridis).
    Otherwise faces use ``solid_rgba``.
    """
    from matplotlib import cm
    from matplotlib.colors import Normalize

    try:
        import denss
    except ImportError as exc:
        raise RuntimeError("DENSS is required to load MRC density maps (pip install denss).") from exc
    try:
        from skimage.measure import marching_cubes
    except ImportError:
        return None

    rho, side = denss.read_mrc(str(density_mrc))
    rho = np.asarray(rho, dtype=np.float64)
    if rho.ndim != 3 or rho.size == 0:
        return None
    min_coords, max_coords, voxel = _mrc_grid_coords(rho, side)

    rho_max = float(np.nanmax(rho))
    if not np.isfinite(rho_max) or rho_max <= 0:
        return None
    level = float(level_fraction) * rho_max
    try:
        verts, faces, _, _ = marching_cubes(rho, level=level)
    except ValueError:
        return None

    verts = np.asarray(verts, dtype=np.float64) * voxel + min_coords
    faces = np.asarray(faces, dtype=np.int64)
    face_colors = np.empty((len(faces), 4), dtype=np.float64)

    if sigma_mrc and Path(sigma_mrc).is_file():
        sigma, side_s = denss.read_mrc(str(sigma_mrc))
        sigma = np.asarray(sigma, dtype=np.float64)
        if sigma.shape != rho.shape:
            raise RuntimeError(
                f"σ map shape {sigma.shape} does not match density shape {rho.shape}"
            )
        inv_voxel = 1.0 / max(voxel, 1e-12)
        face_sigma = np.empty(len(faces), dtype=np.float64)
        shape = np.array(sigma.shape, dtype=np.float64)
        for i, face in enumerate(faces):
            centroid = verts[face].mean(axis=0)
            idx = (centroid - min_coords) * inv_voxel
            ijk = np.clip(np.rint(idx), 0, shape - 1).astype(int)
            face_sigma[i] = float(sigma[ijk[0], ijk[1], ijk[2]])
        vmin = float(np.nanmin(face_sigma))
        vmax = float(np.nanmax(face_sigma))
        if not np.isfinite(vmin) or not np.isfinite(vmax) or abs(vmax - vmin) < 1e-15:
            vmax = vmin + 1.0
        norm = Normalize(vmin=vmin, vmax=vmax)
        cmap = cm.viridis
        for i in range(len(faces)):
            face_colors[i] = cmap(norm(face_sigma[i]))
        return IsosurfaceMeshData(
            verts=verts,
            faces=faces,
            face_colors=face_colors,
            min_coords=min_coords,
            max_coords=max_coords,
            colorbar_label="σ (density)",
            colorbar_vmin=vmin,
            colorbar_vmax=vmax,
        )

    r, g, b, a = (float(x) for x in solid_rgba)
    face_colors[:] = (r, g, b, a)

    return IsosurfaceMeshData(
        verts=verts,
        faces=faces,
        face_colors=face_colors,
        min_coords=min_coords,
        max_coords=max_coords,
    )


def mesh_data_to_poly3d(
    data: IsosurfaceMeshData,
    *,
    alpha: float = 0.82,
    edge_linewidth: float = 0.15,
    rgba: Optional[tuple] = None,
):
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    if rgba is not None:
        r, g, b = float(rgba[0]), float(rgba[1]), float(rgba[2])
        a = float(rgba[3]) if len(rgba) > 3 else float(alpha)
        colors = [(r, g, b, a)] * len(data.faces)
        use_alpha = a
    else:
        colors = [(float(c[0]), float(c[1]), float(c[2]), float(alpha)) for c in data.face_colors]
        use_alpha = alpha
    return Poly3DCollection(
        data.verts[data.faces],
        alpha=use_alpha,
        facecolors=colors,
        edgecolor="k",
        linewidth=edge_linewidth,
    )
