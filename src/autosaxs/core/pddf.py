"""Pair distance distribution function p(r) utilities for SAXS modeling skills."""

from __future__ import annotations

from typing import Dict, Optional, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.spatial import cKDTree

from .utils import bodies_shape_to_dam_atoms

ANGSTROM_TO_NM = 0.1


def save_pddf_dat(path: str, r_nm: np.ndarray, p: np.ndarray) -> None:
    """Write two-column p(r) data: r (nm), p(r) (nm^-1)."""
    r_nm = np.asarray(r_nm, dtype=float)
    p = np.asarray(p, dtype=float)
    with open(path, "w", encoding="utf-8") as fp:
        fp.write("# r(nm)\tp(r)\n")
        for ri, pi in zip(r_nm, p):
            fp.write(f"{float(ri):.8g}\t{float(pi):.8g}\n")


def save_pddf_png(
    path: str,
    r_nm: np.ndarray,
    p: np.ndarray,
    *,
    title: str = "p(r)",
) -> None:
    """Save a simple p(r) line plot."""
    r_nm = np.asarray(r_nm, dtype=float)
    p = np.asarray(p, dtype=float)
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.plot(r_nm, p, lw=2)
    ax.set_xlabel("r (nm)")
    ax.set_ylabel("p(r)")
    ax.set_title(title)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _normalize_pddf(r: np.ndarray, p: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    r = np.asarray(r, dtype=float)
    p = np.asarray(p, dtype=float)
    mask = np.isfinite(r) & np.isfinite(p) & (p >= 0.0)
    r = r[mask]
    p = p[mask]
    if r.size < 2:
        raise ValueError("pddf: insufficient points to normalize")
    order = np.argsort(r)
    r = r[order]
    p = p[order]
    area = float(np.trapezoid(p, r))
    if area <= 0.0 or not np.isfinite(area):
        raise ValueError("pddf: non-positive normalization integral")
    return r, p / area


def _to_nm(r_ang: np.ndarray, p_ang: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Convert r (Å) and p(r) (Å^-1) to nm units."""
    return r_ang * ANGSTROM_TO_NM, p_ang / ANGSTROM_TO_NM


def _default_smooth_sigma_bins(n_bins: int) -> float:
    """Gaussian smoothing width in histogram-bin units (scales mildly with resolution)."""
    return max(3.0, 0.02 * float(n_bins))


def _smooth_pddf_bins(p: np.ndarray, *, sigma_bins: float) -> np.ndarray:
    """Light 1D Gaussian smoothing of a histogram PDF (keeps non-negativity)."""
    p = np.asarray(p, dtype=float)
    sigma = float(sigma_bins)
    if sigma <= 0.0 or p.size < 3:
        return np.maximum(p, 0.0)
    sm = gaussian_filter1d(p, sigma=sigma, mode="nearest")
    return np.maximum(sm, 0.0)


def _trim_pddf_tails(r: np.ndarray, p: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Drop near-zero tail bins after smoothing."""
    p_max = float(np.max(p)) if p.size else 0.0
    if p_max <= 0.0:
        return r, p
    thresh = 1e-4 * p_max
    mask = p >= thresh
    return r[mask], p[mask]


def _bead_sphere_radius_ang(positions: np.ndarray) -> float:
    """Bead radius a (Å) so touching neighbors have center separation ≈ 2a."""
    tree = cKDTree(positions)
    nn_dists, _ = tree.query(positions, k=2)
    nn = np.asarray(nn_dists[:, 1], dtype=float)
    nn = nn[np.isfinite(nn) & (nn > 0.0)]
    if nn.size == 0:
        raise ValueError("pddf: could not estimate bead nearest-neighbor spacing")
    a = 0.5 * float(np.median(nn))
    if not np.isfinite(a) or a <= 0.0:
        raise ValueError("pddf: non-positive bead sphere radius")
    return a


def _sample_uniform_in_balls(
    centers: np.ndarray,
    radius: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """Uniform points inside balls of given radius centered at ``centers`` (Å)."""
    n = int(centers.shape[0])
    u = rng.random(n)
    r = float(radius) * np.power(u, 1.0 / 3.0)
    dirs = rng.normal(size=(n, 3))
    norms = np.linalg.norm(dirs, axis=1, keepdims=True)
    # Extremely rare zero vectors from Normal(0,1); redraw those rows.
    bad = norms.ravel() < 1e-15
    while np.any(bad):
        dirs[bad] = rng.normal(size=(int(np.count_nonzero(bad)), 3))
        norms = np.linalg.norm(dirs, axis=1, keepdims=True)
        bad = norms.ravel() < 1e-15
    dirs = dirs / norms
    return centers + r[:, None] * dirs


def pddf_from_dam_atoms_montecarlo(
    atoms,
    *,
    n_pairs: int = 500_000,
    n_bins: int = 256,
    seed: int = 0,
    smooth_sigma_bins: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Continuum p(r) for a bead DAM via volume Monte Carlo inside bead spheres.

    Each bead is treated as a ball of radius ``a = 0.5 * median(NN distance)`` (Å).
    Cross-bead pairs (i ≠ j) are drawn; one uniform point is sampled in each bead's
    ball; p(r) is the PDF of those inter-point distances. Coordinates are Å; r is
    returned in nm.

    The histogram is lightly Gaussian-smoothed (``smooth_sigma_bins``) to suppress
    Monte Carlo noise before normalization.
    """
    positions = np.asarray(atoms.get_positions(), dtype=float)
    n_beads = int(positions.shape[0])
    if n_beads < 2:
        raise ValueError("pddf: need at least two DAM beads")

    a = _bead_sphere_radius_ang(positions)
    rng = np.random.default_rng(int(seed))
    target = int(n_pairs)
    chunks: list[np.ndarray] = []
    collected = 0
    while collected < target:
        batch = min(target - collected, 100_000)
        i = rng.integers(0, n_beads, batch)
        j = rng.integers(0, n_beads, batch)
        mask = i != j
        if not np.any(mask):
            continue
        i = i[mask]
        j = j[mask]
        u = _sample_uniform_in_balls(positions[i], a, rng)
        v = _sample_uniform_in_balls(positions[j], a, rng)
        d = np.linalg.norm(u - v, axis=1)
        chunks.append(d.astype(float, copy=False))
        collected += int(d.size)

    distances_ang = np.concatenate(chunks)[:target]
    if distances_ang.size == 0:
        raise ValueError("pddf: Monte Carlo produced no volume-pair distances")

    hist, edges = np.histogram(distances_ang, bins=int(n_bins))
    r_ang = 0.5 * (edges[:-1] + edges[1:])
    dr = float(edges[1] - edges[0])
    p = hist.astype(float) / (float(distances_ang.size) * dr)
    sigma_bins = (
        _default_smooth_sigma_bins(int(n_bins))
        if smooth_sigma_bins is None
        else float(smooth_sigma_bins)
    )
    p = _smooth_pddf_bins(p, sigma_bins=sigma_bins)
    r_ang, p = _trim_pddf_tails(r_ang, p)
    r_ang, p = _normalize_pddf(r_ang, p)
    return _to_nm(r_ang, p)


def pddf_from_bodies_shape(
    shape_name: str,
    shape_params: Dict[str, float],
    *,
    grid_size: int = 64,
    n_pairs: int = 500_000,
    n_bins: int = 256,
    seed: int = 0,
    smooth_sigma_bins: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """p(r) for a BODIES shape via its voxel DAM (same grid as 3D views) + volume MC."""
    atoms = bodies_shape_to_dam_atoms((shape_name, shape_params), grid_size=int(grid_size))
    return pddf_from_dam_atoms_montecarlo(
        atoms,
        n_pairs=int(n_pairs),
        n_bins=int(n_bins),
        seed=int(seed),
        smooth_sigma_bins=smooth_sigma_bins,
    )


def pddf_from_dammif_atoms(
    atoms,
    *,
    n_pairs: int = 500_000,
    n_bins: int = 256,
    seed: int = 0,
    smooth_sigma_bins: Optional[float] = None,
) -> Tuple[np.ndarray, np.ndarray]:
    """p(r) for a DAMMIF bead model via continuum volume Monte Carlo."""
    return pddf_from_dam_atoms_montecarlo(
        atoms,
        n_pairs=int(n_pairs),
        n_bins=int(n_bins),
        seed=int(seed),
        smooth_sigma_bins=smooth_sigma_bins,
    )
