"""ATSAS GNOM runners for fit_sizes (polydisperse D(R))."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List, Optional


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
    if system == 2:
        return (
            False,
            2,
            "GNOM system=2 (user-supplied form factor) is not supported on the GNOM command line; use interactive GNOM/PRIMUS.",
            "",
        )
    # Absolute paths: cwd is often an ensemble subdir, while the ATSAS .dat lives in the
    # sample output dir — relative paths break under that cwd (same pattern as fit_distances).
    atsas_dat_path_abs = str(Path(atsas_dat_path).expanduser().resolve())
    out_path_abs = str(Path(out_path).expanduser().resolve())
    output_dir_abs = str(Path(output_dir).expanduser().resolve())
    os.makedirs(output_dir_abs, exist_ok=True)

    cmd: List[str] = [
        "gnom",
        f"--system={int(system)}",
        f"--rmax={float(rmax_nm):.6g}",
        f"--force-zero-rmin={force_zero_rmin}",
        f"--force-zero-rmax={force_zero_rmax}",
    ]
    if rmin_nm is not None:
        cmd.append(f"--rmin={float(rmin_nm):.6g}")
    if rad56_nm is not None:
        cmd.append(f"--rad56={float(rad56_nm):.6g}")
    if first is not None:
        cmd.append(f"--first={int(first)}")
    if last is not None:
        cmd.append(f"--last={int(last)}")
    if nr is not None:
        cmd.append(f"--nr={int(nr)}")
    if alpha is not None:
        cmd.append(f"--alpha={float(alpha):.6g}")
    cmd += ["-o", out_path_abs, atsas_dat_path_abs]
    proc = subprocess.run(cmd, cwd=output_dir_abs, capture_output=True, text=True)
    if proc.returncode != 0:
        return False, int(proc.returncode), (proc.stderr or proc.stdout or "")[:2000], ""
    if not os.path.isfile(out_path_abs):
        return False, int(proc.returncode), "gnom reported success but output file was not created", ""
    try:
        out_text = Path(out_path_abs).read_text(errors="replace")
    except OSError as e:
        return False, int(proc.returncode), f"failed to read GNOM output: {e}", ""
    return True, int(proc.returncode), (proc.stderr or "")[:2000], out_text
