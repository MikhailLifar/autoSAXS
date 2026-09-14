"""ATSAS ``gnom`` CLI runner (shared by skills and liveview P(r) wizard)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np


def normalize_force_zero(flag: str | bool | None, *, default: str = "Y") -> str:
    """Return ``Y`` or ``N`` for GNOM ``--force-zero-*`` options."""
    if flag is None:
        return default
    if isinstance(flag, bool):
        return "Y" if flag else "N"
    s = str(flag).strip().upper()
    if s in ("Y", "YES", "TRUE", "1"):
        return "Y"
    if s in ("N", "NO", "FALSE", "0"):
        return "N"
    return default


def run_gnom(
    *,
    atsas_dat_path: str,
    output_dir: str,
    rmax_nm: float,
    out_path: str,
    system: int = 0,
    rmin_nm: Optional[float] = None,
    rad56_nm: Optional[float] = None,
    first: Optional[int] = None,
    last: Optional[int] = None,
    alpha: Optional[float] = None,
    nr: Optional[int] = None,
    force_zero_rmin: str = "Y",
    force_zero_rmax: str = "Y",
) -> Tuple[bool, int, str, str]:
    """
    Run ATSAS GNOM. Returns ``(ok, returncode, stderr, out_text)``.

    Absolute paths are used because ``cwd`` may be an ensemble subdirectory while
    the ATSAS ``.dat`` lives in the sample output directory.
    """
    if int(system) == 2:
        return (
            False,
            2,
            "GNOM system=2 (user-supplied form factor) is not supported on the GNOM "
            "command line; use interactive GNOM/PRIMUS.",
            "",
        )

    atsas_dat_path_abs = str(Path(atsas_dat_path).expanduser().resolve())
    out_path_abs = str(Path(out_path).expanduser().resolve())
    output_dir_abs = str(Path(output_dir).expanduser().resolve())
    os.makedirs(output_dir_abs, exist_ok=True)

    fz_rmin = normalize_force_zero(force_zero_rmin)
    fz_rmax = normalize_force_zero(force_zero_rmax)

    cmd: List[str] = [
        "gnom",
        f"--system={int(system)}",
        f"--rmax={float(rmax_nm):.6g}",
        f"--force-zero-rmin={fz_rmin}",
        f"--force-zero-rmax={fz_rmax}",
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
    if alpha is not None and np.isfinite(float(alpha)) and float(alpha) > 0:
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


def run_gnom_pr(
    *,
    atsas_dat_path: str,
    output_dir: str,
    rmax_nm: float,
    out_path: str,
    first: Optional[int] = None,
    last: Optional[int] = None,
    alpha: Optional[float] = None,
    force_zero_rmin: str = "Y",
    force_zero_rmax: str = "Y",
) -> Tuple[bool, int, str, str]:
    """Monodisperse pair-distance GNOM (``system=0``)."""
    return run_gnom(
        atsas_dat_path=atsas_dat_path,
        output_dir=output_dir,
        rmax_nm=rmax_nm,
        out_path=out_path,
        system=0,
        first=first,
        last=last,
        alpha=alpha,
        force_zero_rmin=force_zero_rmin,
        force_zero_rmax=force_zero_rmax,
    )
