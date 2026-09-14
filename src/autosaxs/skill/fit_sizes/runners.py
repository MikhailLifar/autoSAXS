"""ATSAS GNOM runners for fit_sizes (polydisperse D(R))."""

from __future__ import annotations

from typing import List, Optional

from autosaxs.core.atsas_gnom import run_gnom


def _shape_to_system(shape: str) -> int:
    s = (shape or "").strip().lower()
    if s in ("sphere", "spheres", "solid_spheres", "solid-spheres"):
        return 1
    if s in ("rod", "rods", "cylinder", "cylinders", "long_cylinders", "long-cylinders"):
        return 5
    if s in ("ellipsoid", "ellipsoids"):
        # GNOM system=2 is interactive-only on the command line per ATSAS 4 manual.
        return 2
    raise ValueError(f"fit_sizes: unknown shape={shape!r}; expected 'spheres', 'rods', or 'ellipsoids'")


def _run_gnom_once(
    *,
    atsas_dat_path: str,
    output_dir: str,
    system: int,
    rmin_nm: Optional[float],
    rmax_nm: float,
    rad56_nm: Optional[float],
    first: Optional[int],
    last: Optional[int],
    alpha: Optional[float],
    nr: Optional[int],
    out_path: str,
    force_zero_rmin: str = "Y",
    force_zero_rmax: str = "Y",
) -> tuple[bool, int, str, str]:
    """
    Returns (ok, returncode, stderr, out_text).
    """
    return run_gnom(
        atsas_dat_path=atsas_dat_path,
        output_dir=output_dir,
        rmax_nm=rmax_nm,
        out_path=out_path,
        system=int(system),
        rmin_nm=rmin_nm,
        rad56_nm=rad56_nm,
        first=first,
        last=last,
        alpha=alpha,
        nr=nr,
        force_zero_rmin=force_zero_rmin,
        force_zero_rmax=force_zero_rmax,
    )
