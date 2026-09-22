"""Sole owner of auto-process pipeline decisions: ``plan_for(session, sample)``."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ...core.models import RunRequest
from ..session.output_paths import (
    averaged_dir,
    averaged_proxy_dir,
    integrated_dat_path,
    subtracted_dat_path,
    subtracted_dir,
    tiff_output_root,
)
from ..session.sample import Sample
from ..session.state import LiveviewIntakeMode, LiveviewSessionState
from .jobs import (
    CompletedWork,
    Job,
    JobStep,
    PlanPhase,
    step_phase,
)
from .monodisperse_pipeline import MonodispersePipelineParts, build_monodisperse_steps
from .polydisperse_pipeline import PolydispersePipelineParts, build_polydisperse_steps
from .report_pipeline import report_individual_step

YamlOptionsLoader = Callable[[Optional[Path]], dict]


def _remaining_steps(steps: List[JobStep], completed: CompletedWork | None) -> List[JobStep]:
    if completed is None or completed.is_empty():
        return list(steps)
    out: List[JobStep] = []
    for step in steps:
        phase = step_phase(step.name)
        # Whole-phase skip for integrate / subtract / report.
        if phase in completed.phases and phase != PlanPhase.ANALYSIS:
            continue
        if step.name in completed.step_names:
            continue
        out.append(step)
    return out


@dataclass(frozen=True)
class PipelinePlan:
    steps: List[JobStep]
    profile_path: str
    output_root: Path
    source_path: str
    boarding: LiveviewIntakeMode
    sample_stem: str
    subtracted_path: str = ""

    def to_job(self, *, job_id_prefix: str | None = None) -> Job:
        board = self.boarding
        prefix = job_id_prefix or {
            LiveviewIntakeMode.FRAME_2D: "frame",
            LiveviewIntakeMode.CURVE_1D: "curve1d",
            LiveviewIntakeMode.CURVE_SUB: "curvesub",
        }.get(board, "sample")
        ctx: Dict[str, Any] = {
            "source_path": self.source_path,
            "sample_stem": self.sample_stem,
            "output_root": str(self.output_root.resolve()),
            "boarding": board.value,
            "profile_path": self.profile_path,
        }
        if self.subtracted_path:
            ctx["subtracted_path"] = self.subtracted_path
        return Job(
            id=f"{prefix}:{self.sample_stem}:{time.time_ns()}",
            priority=0,
            steps=list(self.steps),
            context=ctx,
            # completed defaults empty — Job owns progress from here on
        )


def plan_for(
    state: LiveviewSessionState,
    sample: Sample,
    *,
    load_yaml: YamlOptionsLoader,
    completed: CompletedWork | None = None,
) -> PipelinePlan:
    """
    Build the auto-process plan for ``sample`` from session facts only.

    When ``completed`` is set, returns only the **remaining** steps for that sample
    (phase-boundary replan). Callers must not apply this to other queued jobs.
    """
    boarding = sample.boarding
    if boarding == LiveviewIntakeMode.FRAME_2D:
        plan = _plan_frame(state, sample, load_yaml=load_yaml)
    elif boarding == LiveviewIntakeMode.CURVE_SUB:
        plan = _plan_curve_sub(state, sample, load_yaml=load_yaml)
    else:
        plan = _plan_curve_1d(state, sample, load_yaml=load_yaml)
    if completed is None or completed.is_empty():
        return plan
    return PipelinePlan(
        steps=_remaining_steps(plan.steps, completed),
        profile_path=plan.profile_path,
        output_root=plan.output_root,
        source_path=plan.source_path,
        boarding=plan.boarding,
        sample_stem=plan.sample_stem,
        subtracted_path=plan.subtracted_path,
    )


def _analysis_steps(
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


def _plan_frame(
    state: LiveviewSessionState,
    sample: Sample,
    *,
    load_yaml: YamlOptionsLoader,
) -> PipelinePlan:
    tp = sample.path
    stem = sample.stem
    wd = state.watchdir
    root = tiff_output_root(watchdir=wd, tiff_path=tp, mode=state.watch_mode)
    steps: List[JobStep] = []

    if not state.is_calibrated():
        outdir = averaged_proxy_dir(root)
        outdir.mkdir(parents=True, exist_ok=True)
        steps.append(
            JobStep(
                name="integrate_proxy",
                request=RunRequest(
                    skill_name="integrate_proxy",
                    positional=[tp],
                    options={"output_dir": str(outdir), "use_cache": False},
                ),
            )
        )
        return PipelinePlan(
            steps=steps,
            profile_path="",
            output_root=root,
            source_path=tp,
            boarding=LiveviewIntakeMode.FRAME_2D,
            sample_stem=stem,
        )

    if state.integrator_dir is None:
        raise RuntimeError("Missing integrator_dir (not calibrated)")
    outdir = averaged_dir(root)
    outdir.mkdir(parents=True, exist_ok=True)
    integrate_opts: dict = {"output_dir": str(outdir), "use_cache": False}
    mask_p = state.mask_path
    if mask_p is not None and mask_p.is_file():
        integrate_opts["mask"] = str(mask_p.resolve())
    steps.append(
        JobStep(
            name="integrate",
            request=RunRequest(
                skill_name="integrate",
                positional=[tp, str(state.integrator_dir)],
                options=integrate_opts,
            ),
        )
    )
    integrated_dat = str(integrated_dat_path(root=root, stem=stem, integrator_ready=True).resolve())

    if state.buffer_ready():
        assert state.buffer_dat_path is not None
        subdir = subtracted_dir(root)
        subdir.mkdir(parents=True, exist_ok=True)
        opts = {"output_dir": str(subdir), "use_cache": False}
        opts.update(dict(state.subtract_options or {}))
        steps.append(
            JobStep(
                name="subtract",
                request=RunRequest(
                    skill_name="subtract",
                    positional=[integrated_dat, str(state.buffer_dat_path)],
                    options=opts,
                ),
            )
        )
        profile = str(subtracted_dat_path(root=root, stem=stem).resolve())
        steps.extend(_analysis_steps(state, profile, output_root=root, load_yaml=load_yaml))
        if state.analysis_enabled():
            steps.append(report_individual_step(output_root=root, basename=stem))
        return PipelinePlan(
            steps=steps,
            profile_path=profile,
            output_root=root,
            source_path=tp,
            boarding=LiveviewIntakeMode.FRAME_2D,
            sample_stem=stem,
            subtracted_path=profile,
        )

    profile = integrated_dat
    steps.extend(_analysis_steps(state, profile, output_root=root, load_yaml=load_yaml))
    if state.analysis_enabled():
        steps.append(report_individual_step(output_root=root, basename=stem))
    return PipelinePlan(
        steps=steps,
        profile_path=profile,
        output_root=root,
        source_path=tp,
        boarding=LiveviewIntakeMode.FRAME_2D,
        sample_stem=stem,
    )


def _plan_curve_1d(
    state: LiveviewSessionState,
    sample: Sample,
    *,
    load_yaml: YamlOptionsLoader,
) -> PipelinePlan:
    dp = sample.path
    stem = sample.stem
    root = state.watchdir.resolve()
    steps: List[JobStep] = []

    if state.buffer_ready():
        assert state.buffer_dat_path is not None
        subdir = subtracted_dir(root)
        subdir.mkdir(parents=True, exist_ok=True)
        opts = {"output_dir": str(subdir), "use_cache": False}
        opts.update(dict(state.subtract_options or {}))
        steps.append(
            JobStep(
                name="subtract",
                request=RunRequest(
                    skill_name="subtract",
                    positional=[dp, str(state.buffer_dat_path)],
                    options=opts,
                ),
            )
        )
        profile = str(subtracted_dat_path(root=root, stem=stem).resolve())
        steps.extend(_analysis_steps(state, profile, output_root=root, load_yaml=load_yaml))
        if state.analysis_enabled():
            steps.append(report_individual_step(output_root=root, basename=stem))
        return PipelinePlan(
            steps=steps,
            profile_path=profile,
            output_root=root,
            source_path=dp,
            boarding=LiveviewIntakeMode.CURVE_1D,
            sample_stem=stem,
            subtracted_path=profile,
        )

    profile = dp
    steps.extend(_analysis_steps(state, profile, output_root=root, load_yaml=load_yaml))
    if state.analysis_enabled():
        steps.append(report_individual_step(output_root=root, basename=stem))
    return PipelinePlan(
        steps=steps,
        profile_path=profile,
        output_root=root,
        source_path=dp,
        boarding=LiveviewIntakeMode.CURVE_1D,
        sample_stem=stem,
    )


def _plan_curve_sub(
    state: LiveviewSessionState,
    sample: Sample,
    *,
    load_yaml: YamlOptionsLoader,
) -> PipelinePlan:
    dp = sample.path
    stem = sample.stem
    root = state.watchdir.resolve()
    steps: List[JobStep] = []
    profile = dp
    steps.extend(_analysis_steps(state, profile, output_root=root, load_yaml=load_yaml))
    if state.analysis_enabled():
        steps.append(report_individual_step(output_root=root, basename=stem))
    return PipelinePlan(
        steps=steps,
        profile_path=profile,
        output_root=root,
        source_path=dp,
        boarding=LiveviewIntakeMode.CURVE_SUB,
        sample_stem=stem,
        subtracted_path=profile,
    )
