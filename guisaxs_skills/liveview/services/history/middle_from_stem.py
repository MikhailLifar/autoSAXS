"""
Load middle-column 1D/2D views from disk using autosaxs output naming (``int_<stem>.dat``, etc.).
No pipeline or skills are run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ...session.output_paths import (
    integrated_dat_path,
    subtracted_dat_path,
    tiff_output_root,
)
from ...session.state import LiveviewSessionState, LiveviewState, LiveviewWatchMode


def integrated_dat_for_tiff_stem(
    watchdir: Path,
    stem: str,
    *,
    integrator_ready: bool,
    tiff_path: str = "",
    mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
) -> str:
    """Return path to ``int_<stem>.dat`` under the TIFF output root if the file exists."""
    root = tiff_output_root(watchdir=watchdir, tiff_path=tiff_path, mode=mode)
    p = integrated_dat_path(root=root, stem=stem, integrator_ready=integrator_ready)
    return str(p.resolve()) if p.is_file() else ""


def subtracted_dat_for_tiff_stem(
    watchdir: Path,
    stem: str,
    *,
    tiff_path: str = "",
    mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
) -> str:
    """``subtracted/sub_<stem>.dat`` under the TIFF output root."""
    root = tiff_output_root(watchdir=watchdir, tiff_path=tiff_path, mode=mode)
    p = subtracted_dat_path(root=root, stem=stem)
    return str(p.resolve()) if p.is_file() else ""


def apply_middle_view_from_disk(
    middle: Any,
    *,
    watchdir: Path,
    tiff_path: str,
    state: LiveviewSessionState,
    subtract_options: Dict[str, Any],
) -> None:
    """
    Update ``LiveviewMiddlePanel`` 2D + 1D plots from paths derived from the TIFF stem.

    Missing files leave the corresponding viewers empty (or placeholder layout for C/CD).
    """
    tp = (tiff_path or "").strip()
    stem = Path(tp).stem if tp else ""
    wd = watchdir
    mode = state.watch_mode

    if tp and Path(tp).is_file():
        middle.show_image(tp)
    else:
        middle.show_image("")

    if not stem:
        st0 = state.current_state()
        if st0 in (LiveviewState.C, LiveviewState.CD):
            middle.show_subtraction_views(
                sample_dat="",
                buffer_dat="",
                subtracted_dat="",
                subtract_options=subtract_options,
            )
        else:
            middle.show_curve("", x_label="px" if st0 == LiveviewState.A else "q (nm$^{-1}$)")
        return

    integrator_ready = state.integrator_dir is not None and state.integrator_dir.is_dir()
    int_path = integrated_dat_for_tiff_stem(
        wd, stem, integrator_ready=integrator_ready, tiff_path=tp, mode=mode
    )
    st = state.current_state()

    if st == LiveviewState.A:
        middle.show_curve(int_path, x_label="px")
        return

    if st in (LiveviewState.C, LiveviewState.CD):
        sub_path = subtracted_dat_for_tiff_stem(wd, stem, tiff_path=tp, mode=mode)
        buf = state.buffer_dat_path
        buf_str = str(buf) if buf is not None and buf.is_file() else ""
        middle.show_subtraction_views(
            sample_dat=int_path,
            buffer_dat=buf_str,
            subtracted_dat=sub_path,
            subtract_options=subtract_options,
        )
        return

    middle.show_curve(int_path, x_label="q (nm$^{-1}$)")
