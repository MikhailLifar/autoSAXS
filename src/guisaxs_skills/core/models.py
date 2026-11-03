from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, Union


JsonLike = Union[None, bool, int, float, str, List["JsonLike"], Dict[str, "JsonLike"]]


class RunState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    CANCELLING = "cancelling"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class SkillParam:
    name: str
    kind: str  # positional|required|optional|kwonly|required_kwonly
    default: Optional[Any] = None
    annotation: Optional[str] = None


@dataclass(frozen=True)
class SkillMeta:
    name: str
    summary: str
    doc: str
    positional_params: List[SkillParam] = field(default_factory=list)
    option_params: List[SkillParam] = field(default_factory=list)


@dataclass(frozen=True)
class RunRequest:
    skill_name: str
    positional: List[str]
    options: Dict[str, Any]

    def cli_argv(self) -> List[str]:
        """Build argv for ``python -m autosaxs.cli.cli`` (shared codec with CLI)."""
        from autosaxs.cli.request_codec import options_to_argv

        argv: List[str] = [self.skill_name]
        argv.extend(self.positional)
        argv.extend(options_to_argv(self.options))
        return argv


@dataclass(frozen=True)
class Artifact:
    role: str
    value: Union[str, List[str]]
    exists: bool = True

    def paths(self) -> List[str]:
        if isinstance(self.value, list):
            return self.value
        return [self.value]


def flatten_artifacts(result: Dict[str, Any]) -> List[Artifact]:
    out: List[Artifact] = []
    for k, v in (result or {}).items():
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            out.append(Artifact(role=k, value=list(v)))
        elif isinstance(v, str):
            out.append(Artifact(role=k, value=v))
    return out

