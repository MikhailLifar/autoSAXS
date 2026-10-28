"""Sole owner of auto-process pipeline decisions: ``plan_for(session, sample)``."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from autosaxs.core.buffer_rules import (
    BufferMatchConflict,
    MatchKind,
    MatchResult,
    match_sample_to_buffer,
)

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
from ..services.calibration.masks import applied_mask_path
from .buffer_match import collect_buffer_candidates
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

# Re-export for executor / controller catch sites.
__all__ = ["PipelinePlan", "plan_for", "BufferMatchConflict"]


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


def _match_for_sample(
    state: LiveviewSessionState,
    *,
    sample_curve: str,
    sample_output_root: Path,
) -> MatchResult:
    """Run buffer_rules matcher; raise ``BufferMatchConflict`` on CONFLICT."""
    rules = tuple(state.buffer_rules or ())
    candidates = collect_buffer_candidates(
        state,
        sample_output_root=sample_output_root,
        exclude=[sample_curve],
    )
    result = match_sample_to_buffer(sample_curve, candidates, rules)
    if result.kind == MatchKind.CONFLICT:
        raise BufferMatchConflict(result)
    return result


def _append_analysis_and_report(
    steps: List[JobStep],
    state: LiveviewSessionState,
    *,
    profile: str,
    output_root: Path,
    stem: str,
    load_yaml: YamlOptionsLoader,
) -> None:
    steps.extend(_analysis_steps(state, profile, output_root=output_root, load_yaml=load_yaml))
    if state.analysis_enabled():
        steps.append(report_individual_step(output_root=output_root, basename=stem))


def _subtract_step(
    state: LiveviewSessionState,
    *,
    sample_curve: str,
    buffer_path: str,
    output_root: Path,
) -> JobStep:
    subdir = subtracted_dir(output_root)
    subdir.mkdir(parents=True, exist_ok=True)
    opts = {"output_dir": str(subdir), "use_cache": False}
    opts.update(dict(state.subtract_options or {}))
    return JobStep(
        name="subtract",
        request=RunRequest(
            skill_name="subtract",
            positional=[sample_curve, buffer_path],
            options=opts,
        ),
    )


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
    mask_p = applied_mask_path(state)
    if mask_p is not None:
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

    if not state.buffer_ready():
        # Buffer stage not configured: integrate then analysis on unsubtracted (legacy).
        profile = integrated_dat
        _append_analysis_and_report(
            steps, state, profile=profile, output_root=root, stem=stem, load_yaml=load_yaml
        )
        return PipelinePlan(
            steps=steps,
            profile_path=profile,
            output_root=root,
            source_path=tp,
            boarding=LiveviewIntakeMode.FRAME_2D,
            sample_stem=stem,
        )

    # Match against TIFF basename as well as predicted integrated curve so rules
    # written for either naming style work. Prefer the curve path for pairing.
    match = _match_for_sample(state, sample_curve=integrated_dat, sample_output_root=root)
    if match.kind == MatchKind.UNMATCHED:
        # Also try the source TIFF basename (rules often target *.tif pools).
        match_tiff = _match_for_sample(state, sample_curve=tp, sample_output_root=root)
        if match_tiff.kind != MatchKind.UNMATCHED:
            match = match_tiff
        else:
            # Not a claimed sample → stop after integrate (silent; no analysis).
            return PipelinePlan(
                steps=steps,
                profile_path=integrated_dat,
                output_root=root,
                source_path=tp,
                boarding=LiveviewIntakeMode.FRAME_2D,
                sample_stem=stem,
            )

    if match.kind == MatchKind.PAIRED:
        assert match.buffer is not None
        steps.append(
            _subtract_step(
                state,
                sample_curve=integrated_dat,
                buffer_path=match.buffer,
                output_root=root,
            )
        )
        profile = str(subtracted_dat_path(root=root, stem=stem).resolve())
        _append_analysis_and_report(
            steps, state, profile=profile, output_root=root, stem=stem, load_yaml=load_yaml
        )
        return PipelinePlan(
            steps=steps,
            profile_path=profile,
            output_root=root,
            source_path=tp,
            boarding=LiveviewIntakeMode.FRAME_2D,
            sample_stem=stem,
            subtracted_path=profile,
        )

    # NONE: claimed sample, skip subtract, continue analysis on integrated.
    profile = integrated_dat
    _append_analysis_and_report(
        steps, state, profile=profile, output_root=root, stem=stem, load_yaml=load_yaml
    )
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

    if not state.buffer_ready():
        profile = dp
        _append_analysis_and_report(
            steps, state, profile=profile, output_root=root, stem=stem, load_yaml=load_yaml
        )
        return PipelinePlan(
            steps=steps,
            profile_path=profile,
            output_root=root,
            source_path=dp,
            boarding=LiveviewIntakeMode.CURVE_1D,
            sample_stem=stem,
        )

    match = _match_for_sample(state, sample_curve=dp, sample_output_root=root)
    if match.kind == MatchKind.UNMATCHED:
        return PipelinePlan(
            steps=steps,
            profile_path=dp,
            output_root=root,
            source_path=dp,
            boarding=LiveviewIntakeMode.CURVE_1D,
            sample_stem=stem,
        )

    if match.kind == MatchKind.PAIRED:
        assert match.buffer is not None
        steps.append(
            _subtract_step(
                state,
                sample_curve=dp,
                buffer_path=match.buffer,
                output_root=root,
            )
        )
        profile = str(subtracted_dat_path(root=root, stem=stem).resolve())
        _append_analysis_and_report(
            steps, state, profile=profile, output_root=root, stem=stem, load_yaml=load_yaml
        )
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
    _append_analysis_and_report(
        steps, state, profile=profile, output_root=root, stem=stem, load_yaml=load_yaml
    )
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
