from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...core.models import RunRequest


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
    Generic job executed sequentially by the liveview job executor.

    `priority`: higher values run earlier (FIFO among same priority).
    `context`: small metadata for UI refresh (e.g. tiff_path, tiff_stem).
    """

    id: str
    priority: int = 0
    steps: List[JobStep] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)


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

