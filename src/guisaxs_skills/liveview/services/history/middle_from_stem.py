"""
Sole owner of middle-column sync: intake layout + disk content for the current sample.

Call ``sync_middle_view`` from history / intake / session. Do not reshape visibility
from ``show_curve`` / ``show_subtraction_*`` — those only paint.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ...ingest.sample_revision import is_dat_path, is_tiff_path, sample_stem_from_path
from ...session.output_paths import (
    integrated_dat_path,
    subtracted_dat_path,
    tiff_output_root,
)
from ...session.sample import Sample
from ...session.state import LiveviewIntakeMode, LiveviewSessionState, LiveviewWatchMode


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


@dataclass(frozen=True)
class _MiddlePaint:
    """Resolved paths to paint after layout is applied."""

    image_path: str = ""
    main_curve_path: str = ""
    main_x_label: str = "q (nm$^{-1}$)"
    sample_dat: str = ""
    buffer_dat: str = ""
    subtracted_dat: str = ""
    use_dual: bool = False

    def signature(self) -> Tuple[Any, ...]:
        return (
            self.image_path,
            self.main_curve_path,
            self.main_x_label,
            self.sample_dat,
            self.buffer_dat,
            self.subtracted_dat,
            self.use_dual,
        )


def sync_middle_view(
    middle: Any,
    *,
    state: LiveviewSessionState,
    sample: Sample | None = None,
    subtract_options: Dict[str, Any] | None = None,
    force: bool = False,
) -> None:
    """
    Single owner: session intake/buffer → layout; sample (+ disk) → plots.

    Skips re-paint when layout and resolved paths are unchanged (unless ``force``).
    """
    opts = dict(subtract_options or state.subtract_options or {})
    middle.apply_intake_layout(state.intake_mode, buffer_ready=state.buffer_ready())

    paint = _resolve_paint(state=state, sample=sample)
    layout_sig = (state.intake_mode, state.buffer_ready())
    full_sig = layout_sig + paint.signature() + (tuple(sorted((k, str(v)) for k, v in opts.items())),)
    prev = getattr(middle, "_middle_sync_sig", None)
    if not force and prev == full_sig:
        return
    middle._middle_sync_sig = full_sig
    _apply_paint(middle, paint, subtract_options=opts)


def apply_middle_view_from_sample(
    middle: Any,
    *,
    sample: Sample,
    state: LiveviewSessionState,
    subtract_options: Dict[str, Any] | None = None,
) -> None:
    """Paint middle for ``sample`` (layout already expected from ``sync_middle_view``)."""
    sync_middle_view(
        middle,
        state=state,
        sample=sample,
        subtract_options=subtract_options,
        force=True,
    )


def apply_middle_view_from_disk(
    middle: Any,
    *,
    watchdir: Path,
    tiff_path: str,
    state: LiveviewSessionState,
    subtract_options: Dict[str, Any],
    boarding: LiveviewIntakeMode | None = None,
) -> None:
    """Disk paint for a path; prefer ``sync_middle_view`` when a ``Sample`` exists."""
    tp = (tiff_path or "").strip()
    if not tp:
        sync_middle_view(middle, state=state, sample=None, subtract_options=subtract_options, force=True)
        return
    board = boarding or state.intake_mode
    sample = Sample.from_path(tp, boarding=board)
    sync_middle_view(
        middle,
        state=state,
        sample=sample,
        subtract_options=subtract_options,
        force=True,
    )


def _resolve_paint(
    *,
    state: LiveviewSessionState,
    sample: Sample | None,
) -> _MiddlePaint:
    if sample is None:
        xlab = "px" if not state.is_calibrated() else "q (nm$^{-1}$)"
        if state.buffer_ready():
            return _MiddlePaint(use_dual=True, main_x_label=xlab)
        return _MiddlePaint(main_x_label=xlab)

    tp = sample.path
    wd = state.watchdir
    mode = state.watch_mode
    board = sample.boarding

    if is_dat_path(tp):
        return _resolve_curve_paint(dat_path=tp, watchdir=wd, state=state, boarding=board)

    image = tp if is_tiff_path(tp) and Path(tp).is_file() else ""
    stem = Path(tp).stem if tp else ""
    if not stem:
        xlab = "px" if not state.is_calibrated() else "q (nm$^{-1}$)"
        if state.buffer_ready():
            return _MiddlePaint(image_path=image, use_dual=True, main_x_label=xlab)
        return _MiddlePaint(image_path=image, main_x_label=xlab)

    integrator_ready = state.integrator_dir is not None and state.integrator_dir.is_dir()
    int_path = integrated_dat_for_tiff_stem(
        wd, stem, integrator_ready=integrator_ready, tiff_path=tp, mode=mode
    )

    if not state.is_calibrated():
        return _MiddlePaint(image_path=image, main_curve_path=int_path, main_x_label="px")

    if state.intake_mode == LiveviewIntakeMode.CURVE_SUB:
        sub_path = subtracted_dat_for_tiff_stem(wd, stem, tiff_path=tp, mode=mode)
        return _MiddlePaint(
            image_path="",
            main_curve_path=sub_path or int_path,
            main_x_label="q (nm$^{-1}$)",
        )

    if state.buffer_ready():
        sub_path = subtracted_dat_for_tiff_stem(wd, stem, tiff_path=tp, mode=mode)
        buf = state.buffer_dat_path
        buf_str = str(buf) if buf is not None and buf.is_file() else ""
        # 1D: main integrated + dual. 2D: dual only (layout hides main).
        main = int_path if state.intake_mode == LiveviewIntakeMode.CURVE_1D else ""
        return _MiddlePaint(
            image_path=image,
            main_curve_path=main,
            main_x_label="q (nm$^{-1}$)",
            sample_dat=int_path,
            buffer_dat=buf_str,
            subtracted_dat=sub_path,
            use_dual=True,
        )

    return _MiddlePaint(
        image_path=image,
        main_curve_path=int_path,
        main_x_label="q (nm$^{-1}$)",
    )


def _resolve_curve_paint(
    *,
    dat_path: str,
    watchdir: Path,
    state: LiveviewSessionState,
    boarding: LiveviewIntakeMode | None,
) -> _MiddlePaint:
    dp = Path(dat_path)
    if not dp.is_file():
        return _MiddlePaint()

    stem = sample_stem_from_path(dat_path)
    parts_lower = tuple(p.lower() for p in dp.parts)
    is_sub_file = "subtracted" in parts_lower or (boarding == LiveviewIntakeMode.CURVE_SUB)
    if boarding == LiveviewIntakeMode.CURVE_1D:
        is_sub_file = False

    resolved = str(dp.resolve())
    if is_sub_file or state.intake_mode == LiveviewIntakeMode.CURVE_SUB:
        return _MiddlePaint(main_curve_path=resolved, main_x_label="q (nm$^{-1}$)")

    if state.buffer_ready():
        buf = state.buffer_dat_path
        buf_str = str(buf) if buf is not None and buf.is_file() else ""
        sub_cand = watchdir / "subtracted" / f"sub_{stem}.dat"
        sub_str = str(sub_cand.resolve()) if sub_cand.is_file() else ""
        main = resolved if state.intake_mode == LiveviewIntakeMode.CURVE_1D else ""
        return _MiddlePaint(
            main_curve_path=main,
            main_x_label="q (nm$^{-1}$)",
            sample_dat=resolved,
            buffer_dat=buf_str,
            subtracted_dat=sub_str,
            use_dual=True,
        )

    return _MiddlePaint(main_curve_path=resolved, main_x_label="q (nm$^{-1}$)")


def _apply_paint(
    middle: Any,
    paint: _MiddlePaint,
    *,
    subtract_options: Dict[str, Any],
) -> None:
    middle.show_image(paint.image_path)
    if paint.use_dual:
        middle.show_subtraction_views(
            sample_dat=paint.sample_dat,
            buffer_dat=paint.buffer_dat,
            subtracted_dat=paint.subtracted_dat,
            subtract_options=subtract_options,
        )
        # CURVE_1D: ensure main integrated (show_subtraction_views also paints it).
        if paint.main_curve_path:
            middle.show_curve(paint.main_curve_path, x_label=paint.main_x_label)
        return
    if hasattr(middle, "clear_dual_plots"):
        middle.clear_dual_plots()
    middle.show_curve(paint.main_curve_path, x_label=paint.main_x_label)
