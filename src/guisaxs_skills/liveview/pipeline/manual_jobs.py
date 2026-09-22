"""Manual / intervention job builders for LiveviewJobExecutor."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, List, Optional

from ...core.models import RunRequest
from ..session.output_paths import subtracted_dat_path, subtracted_dir
from ..session.state import LiveviewSessionState, LiveviewWatchMode, MonodisperseShapeMode
from .jobs import Job, JobStep
from .monodisperse_pipeline import (
    MonodispersePipelineParts,
    build_monodisperse_steps,
    profile_sample_stem,
)
from .polydisperse_pipeline import PolydispersePipelineParts, build_polydisperse_steps
from .report_pipeline import report_individual_step

YamlOptionsLoader = Callable[[Optional[Path]], dict]


def build_monodisperse_manual_job(
    *,
    profile_abs: str,
    steps: List[JobStep],
    output_root: Optional[Path] = None,
    watchdir: Path,
    priority: int = 150,
) -> Job:
    prof = str(Path(profile_abs).expanduser().resolve())
    stem = profile_sample_stem(prof)
    root = (output_root or watchdir).expanduser().resolve()
    return Job(
        id=f"mono_manual:{stem}:{time.time_ns()}",
        priority=int(priority),
        steps=steps,
        context={
            "manual": True,
            "monodisperse": True,
            "profile_path": prof,
            "sample_stem": stem,
            "output_root": str(root),
        },
    )


def build_polydisperse_manual_job(
    *,
    profile_abs: str,
    steps: List[JobStep],
    output_root: Optional[Path] = None,
    watchdir: Path,
    priority: int = 150,
) -> Job:
    prof = str(Path(profile_abs).expanduser().resolve())
    stem = profile_sample_stem(prof)
    root = (output_root or watchdir).expanduser().resolve()
    return Job(
        id=f"poly_manual:{stem}:{time.time_ns()}",
        priority=int(priority),
        steps=steps,
        context={
            "manual": True,
            "polydisperse": True,
            "profile_path": prof,
            "sample_stem": stem,
            "output_root": str(root),
        },
    )


def subtraction_output_root(*, state: LiveviewSessionState, sample_dat: str) -> Path:
    wd = state.watchdir.resolve()
    if state.watch_mode != LiveviewWatchMode.TREE:
        return wd
    sp = Path((sample_dat or "").strip()).expanduser().resolve()
    if sp.parent.name in ("averaged", "averaged_proxy"):
        return sp.parent.parent
    return sp.parent


def analysis_steps_for_profile(
    state: LiveviewSessionState,
    profile_abs: str,
    *,
    output_root: Path,
    load_yaml: YamlOptionsLoader,
    use_ui_params: bool = False,
) -> List[JobStep]:
    if not state.analysis_enabled():
        return []
    prof = str(Path(profile_abs).expanduser().resolve())
    steps: List[JobStep] = []
    fixed = bool(use_ui_params)
    if state.monodisperse_armed:
        steps.extend(
            build_monodisperse_steps(
                prof,
                output_root=output_root,
                state=state,
                parts=MonodispersePipelineParts.FULL,
                load_yaml=load_yaml,
                guinier_handoff=None,
                fixed_guinier_interval=fixed,
            )
        )
    if state.polydisperse_armed:
        steps.extend(
            build_polydisperse_steps(
                prof,
                output_root=output_root,
                state=state,
                parts=PolydispersePipelineParts.FULL,
                load_yaml=load_yaml,
                fixed_guinier_interval=fixed,
            )
        )
    return steps


def build_rerun_subtraction_job(
    *,
    state: LiveviewSessionState,
    sample_dat: str,
    buffer_dat: str,
    scaling_factor: float,
    load_yaml: YamlOptionsLoader,
    priority: int = 100,
    use_ui_params: bool = False,
) -> Job:
    sp = (sample_dat or "").strip()
    bp = (buffer_dat or "").strip()
    stem = Path(sp).stem
    if stem.startswith("int_"):
        stem = stem[len("int_") :]
    root = subtraction_output_root(state=state, sample_dat=sp)
    subdir = subtracted_dir(root)
    subdir.mkdir(parents=True, exist_ok=True)
    opts = {"output_dir": str(subdir.resolve()), "use_cache": False}
    opts.update(dict(state.subtract_options or {}))
    opts["scaling_factor"] = float(scaling_factor)
    steps: List[JobStep] = [
        JobStep(
            name="subtract",
            request=RunRequest(
                skill_name="subtract",
                positional=[sp, bp],
                options=opts,
            ),
        )
    ]
    profile = str(subtracted_dat_path(root=root, stem=stem).resolve())
    steps.extend(
        analysis_steps_for_profile(
            state,
            profile,
            output_root=root,
            load_yaml=load_yaml,
            use_ui_params=bool(use_ui_params),
        )
    )
    return Job(
        id=f"rerun_sub:{stem}:{time.time_ns()}",
        priority=int(priority),
        steps=steps,
        context={"manual": True, "sample_stem": stem, "profile_path": profile},
    )


def monodisperse_step_shape(
    *,
    state: LiveviewSessionState,
    profile_abs: str,
    output_root: Path,
    shape_mode: str,
    load_yaml: YamlOptionsLoader,
    gnom_out_path: Optional[str] = None,
) -> Optional[JobStep]:
    prev = state.monodisperse_shape_mode
    try:
        state.monodisperse_shape_mode = MonodisperseShapeMode(str(shape_mode).lower())
    except ValueError:
        state.monodisperse_shape_mode = MonodisperseShapeMode.NONE
    try:
        steps = build_monodisperse_steps(
            profile_abs,
            output_root=output_root,
            state=state,
            parts=MonodispersePipelineParts.SHAPE_ONLY,
            load_yaml=load_yaml,
            gnom_out_path=gnom_out_path,
        )
    finally:
        state.monodisperse_shape_mode = prev
    return steps[0] if steps else None
