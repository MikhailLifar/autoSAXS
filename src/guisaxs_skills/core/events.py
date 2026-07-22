from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import Artifact, RunRequest


@dataclass(frozen=True)
class WorkdirSelected:
    path: str


@dataclass(frozen=True)
class RunSkillRequested:
    request: RunRequest


@dataclass(frozen=True)
class CancelRunRequested:
    pass


@dataclass(frozen=True)
class Status:
    text: str
    level: str = "info"  # info|warning|error


@dataclass(frozen=True)
class RunStarted:
    skill_name: str
    started_at: datetime


@dataclass(frozen=True)
class RunStdout:
    text: str


@dataclass(frozen=True)
class RunStderr:
    text: str


@dataclass(frozen=True)
class RunProgress:
    fraction: Optional[float] = None
    label: Optional[str] = None


@dataclass(frozen=True)
class RunResult:
    result: Dict[str, Any]


@dataclass(frozen=True)
class RunFailed:
    error_summary: str
    diagnostics: str = ""


@dataclass(frozen=True)
class RunCancelled:
    pass


@dataclass(frozen=True)
class ArtifactsUpdated:
    artifacts: List[Artifact]


@dataclass(frozen=True)
class CopyCliRequested:
    request: RunRequest


@dataclass(frozen=True)
class OpenArtifactRequested:
    path: str

