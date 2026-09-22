from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional

from ...core.models import RunRequest


class PlanPhase(str, Enum):
    """Coarse pipeline stages for phase-boundary replanning."""

    INTEGRATE = "integrate"
    SUBTRACT = "subtract"
    ANALYSIS = "analysis"
    REPORT = "report"


@dataclass(frozen=True)
class CompletedWork:
    """
    Progress already finished for one Job run.

    Owned by ``Job.completed`` (not the executor). ``plan_for(..., completed=)``
    uses phases / step_names to return only remaining steps; ``results`` feeds
    placeholder resolution for later steps (and survives cancel-requeue).

    ``phases``: finished coarse stages (INTEGRATE / SUBTRACT / REPORT).
    ``step_names``: finished individual steps (ANALYSIS mid-chain so arming can
    append later analysis without re-running finished ones).
    ``results``: skill result dicts keyed by step name.
    """

    phases: frozenset[PlanPhase] = frozenset()
    step_names: frozenset[str] = frozenset()
    results: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    def is_empty(self) -> bool:
        return not self.phases and not self.step_names

    def with_step(
        self,
        step_name: str,
        result: Mapping[str, Any] | None = None,
    ) -> CompletedWork:
        name = (step_name or "").strip()
        if not name:
            return self
        names = frozenset(set(self.step_names) | {name})
        phases = set(self.phases)
        phase = step_phase(name)
        if phase in (PlanPhase.INTEGRATE, PlanPhase.SUBTRACT, PlanPhase.REPORT):
            phases.add(phase)
        new_results = dict(self.results)
        if result is not None:
            new_results[name] = dict(result)
        return CompletedWork(
            phases=frozenset(phases),
            step_names=names,
            results=new_results,
        )


def step_phase(step_name: str) -> PlanPhase:
    """Map a job step name to its coarse plan phase."""
    n = (step_name or "").strip()
    if n in ("integrate", "integrate_proxy"):
        return PlanPhase.INTEGRATE
    if n == "subtract":
        return PlanPhase.SUBTRACT
    if n == "report_individual":
        return PlanPhase.REPORT
    return PlanPhase.ANALYSIS


@dataclass(frozen=True)
class JobStep:
    """
    One step in a job: a named RunRequest.

    The name is used for placeholder substitution (e.g. `${fit_distances.best_gnom_out_path}`).
    """

    name: str
    request: RunRequest


def is_manual_job(job: Job) -> bool:
    """True for user-triggered runs (wizard reruns, calibration, manual fits, …)."""
    return bool(job.context.get("manual"))


@dataclass(frozen=True)
class Job:
    """
    Executable unit: remaining steps, context, and **owned** completion progress.

    ``completed`` is the sole record of finished work for this run. The executor
    never keeps a parallel progress store — it only calls the methods below and
    holds the current ``Job`` value.
    """

    id: str
    priority: int = 0
    steps: List[JobStep] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    completed: CompletedWork = field(default_factory=CompletedWork)

    def mark_step_done(
        self,
        step_name: str,
        *,
        result: Mapping[str, Any] | None = None,
    ) -> Job:
        """Record a finished step and optional skill result (new frozen Job)."""
        return replace(self, completed=self.completed.with_step(step_name, result))

    def with_remaining_steps(
        self,
        steps: List[JobStep],
        *,
        context: Mapping[str, Any] | None = None,
    ) -> Job:
        """Replace the remaining step list after ``plan_for(..., completed=)``."""
        if context is None:
            return replace(self, steps=list(steps))
        return replace(self, steps=list(steps), context=dict(context))

    def as_retry(self, *, priority: int) -> Job:
        """
        Cancel-requeue: same ``completed`` (phases + results) / context / steps,
        new id and priority.

        ``_start_job`` replans from ``completed`` so finished work is not repeated
        and placeholders still resolve from stored results.
        """
        return replace(
            self,
            id=f"{self.id}:retry:{time.time_ns()}",
            priority=int(priority),
        )


class PlaceholderError(RuntimeError):
    pass


def _lookup_dot_path(obj: Any, path: str) -> Any:
    cur = obj
    for part in (path or "").split("."):
        if part == "":
            continue
        if isinstance(cur, dict):
            if part not in cur:
                raise KeyError(part)
            cur = cur[part]
            continue
        raise KeyError(part)
    return cur


def resolve_placeholders_in_str(s: str, *, results_by_step: Dict[str, Dict[str, Any]]) -> str:
    """
    Replace `${stepName.key.subkey}` occurrences using `results_by_step`.

    Only string values are supported as substitutions. If a placeholder resolves to a non-string,
    it is coerced via `str(...)`.
    """

    if "${" not in s:
        return s
    out = ""
    i = 0
    while i < len(s):
        j = s.find("${", i)
        if j < 0:
            out += s[i:]
            break
        out += s[i:j]
        k = s.find("}", j + 2)
        if k < 0:
            raise PlaceholderError("Unclosed placeholder in string")
        expr = s[j + 2 : k].strip()
        if not expr:
            raise PlaceholderError("Empty placeholder")
        if "." not in expr:
            raise PlaceholderError(f"Placeholder must include dot-path: {expr!r}")
        step, rest = expr.split(".", 1)
        if step not in results_by_step:
            raise PlaceholderError(f"Unknown step in placeholder: {step!r}")
        try:
            val = _lookup_dot_path(results_by_step[step], rest)
        except KeyError as e:
            raise PlaceholderError(f"Missing key in placeholder: {expr!r}") from e
        out += str(val)
        i = k + 1
    return out


def resolve_request_placeholders(req: RunRequest, *, results_by_step: Dict[str, Dict[str, Any]]) -> RunRequest:
    pos: List[str] = []
    for p in req.positional:
        if isinstance(p, str):
            pos.append(resolve_placeholders_in_str(p, results_by_step=results_by_step))
        else:
            pos.append(str(p))
    opts: Dict[str, Any] = {}
    for k, v in (req.options or {}).items():
        if isinstance(v, str):
            opts[k] = resolve_placeholders_in_str(v, results_by_step=results_by_step)
        else:
            opts[k] = v
    return RunRequest(skill_name=req.skill_name, positional=pos, options=opts)
