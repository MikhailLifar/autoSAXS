from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.models import Artifact, RunState, SkillMeta


@dataclass
class SessionState:
    workdir: Path
    selected_skill: Optional[SkillMeta] = None
    run_state: RunState = RunState.IDLE
    stdout: str = ""
    stderr: str = ""
    result: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[Artifact] = field(default_factory=list)
    selected_artifact_path: Optional[str] = None
    # Per-skill persisted form state (session-only)
    form_state_by_skill: Dict[str, Dict[str, Any]] = field(default_factory=dict)

