"""
Load right-column analysis previews from disk using autosaxs per-sample subdirs (stem = TIFF basename).
"""

from __future__ import annotations

from pathlib import Path

from ...session.output_paths import (
    integrated_dat_path,
    subtracted_dat_path,
    tiff_output_root,
)
from ...session.state import LiveviewWatchMode


def apply_right_outputs_from_disk(
    right,
    *,
    watchdir: Path,
    tiff_stem: str,
    monodisperse_armed: bool = False,
    polydisperse_armed: bool = False,
    tiff_path: str = "",
    watch_mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
) -> None:
    """Clear analysis previews, then load paths for ``tiff_stem`` under the TIFF output root."""
    right.clear_output_previews()
    if not (monodisperse_armed or polydisperse_armed) or not (tiff_stem or "").strip():
        return
    stem = tiff_stem.strip()
    root = tiff_output_root(watchdir=watchdir, tiff_path=tiff_path, mode=watch_mode)
    sub = subtracted_dat_path(root=root, stem=stem)
    integ = integrated_dat_path(root=root, stem=stem, integrator_ready=True)
    prof = sub if sub.is_file() else integ
    profile_path = str(prof.resolve()) if prof.is_file() else ""

    if monodisperse_armed and hasattr(right, "load_monodisperse_from_disk"):
        right.load_monodisperse_from_disk(
            profile_path=profile_path,
            stem=stem,
            tiff_path=tiff_path,
        )
    if polydisperse_armed and hasattr(right, "load_polydisperse_from_disk"):
        right.load_polydisperse_from_disk(
            profile_path=profile_path,
            stem=stem,
            tiff_path=tiff_path,
        )
