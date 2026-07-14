"""Pure (no-Qt) isosurface mesh computation for interactive 3D viewers."""

from __future__ import annotations

from dataclasses import dataclass
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


def mesh_data_to_poly3d(
    data: IsosurfaceMeshData,
    *,
    alpha: float = 0.82,
    edge_linewidth: float = 0.15,
):
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    colors = [(float(c[0]), float(c[1]), float(c[2]), float(alpha)) for c in data.face_colors]
    return Poly3DCollection(
        data.verts[data.faces],
        alpha=alpha,
        facecolors=colors,
        edgecolor="k",
        linewidth=edge_linewidth,
    )
