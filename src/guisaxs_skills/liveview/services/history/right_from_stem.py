"""
Load right-column analysis previews from disk (and live skill results via present_right).
"""

from __future__ import annotations

from pathlib import Path

from ...session.state import LiveviewSessionState, LiveviewWatchMode
from .right_artifacts import RightPresentSource, present_right


def apply_right_outputs_from_disk(
    right,
    *,
    watchdir: Path,
    tiff_stem: str,
    monodisperse_armed: bool = False,
    polydisperse_armed: bool = False,
    tiff_path: str = "",
    watch_mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
    state: LiveviewSessionState | None = None,
    session=None,
) -> None:
    """Clear analysis previews, then present discovered artifacts for ``tiff_stem``."""
    if state is None:
        # Backward-compatible: build a minimal view from kwargs (tests / old callers).
        if not hasattr(right, "_state"):
            right.clear_output_previews()
            return
        state = right._state
    present_right(
        right,
        state=state,
        sample_path=tiff_path,
        stem=tiff_stem,
        watch_mode=watch_mode,
        source=RightPresentSource.DISK,
        session=session,
    )
