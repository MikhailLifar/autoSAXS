"""Parse ``autosaxs.progress`` stderr lines into modeling status strings."""

from __future__ import annotations

import re
from typing import Dict, Optional

_PROGRESS_RE = re.compile(
    r"^\s*autosaxs\.progress\s+(.+?)\s*$",
    flags=re.IGNORECASE,
)
_KV_RE = re.compile(r"([A-Za-z_][\w]*)=([^\s]+)")

_STAGE_LABELS = {
    "dammif": "DAMMIF",
    "averaging": "Averaging…",
    "aligning": "Aligning…",
    "finalizing": "Finalizing…",
    "bodies": "BODIES",
    "pilot": "DENSS pilot…",
    "refined": "DENSS refine…",
    "density": "DENSS…",
    "mixture": "MIXTURE…",
    "running": "Running…",
}


def parse_progress_fields(line: str) -> Optional[Dict[str, str]]:
    """Return key/value fields from one ``autosaxs.progress`` line, or None."""
    m = _PROGRESS_RE.match((line or "").strip())
    if not m:
        return None
    fields = {k: v for k, v in _KV_RE.findall(m.group(1))}
    if "skill" not in fields or "stage" not in fields:
        return None
    return fields


def format_progress_status(fields: Dict[str, str]) -> str:
    """Human status for shape/DR status + passport (fallback-friendly)."""
    skill = str(fields.get("skill") or "").strip()
    stage = str(fields.get("stage") or "").strip().lower()
    run = str(fields.get("run") or "").strip()
    shape = str(fields.get("shape") or "").strip()

    if stage == "dammif":
        return f"DAMMIF {run}" if run else "DAMMIF…"
    if stage == "bodies":
        if run:
            return f"BODIES {run}"
        if shape:
            return f"BODIES {shape}"
        return "BODIES…"
    if stage in _STAGE_LABELS:
        return _STAGE_LABELS[stage]
    if skill:
        return f"Running {skill}…"
    return "Running…"


def status_from_stderr_line(line: str) -> Optional[str]:
    """Parse a single stderr line; return status text or None if not progress."""
    fields = parse_progress_fields(line)
    if fields is None:
        return None
    return format_progress_status(fields)


class ProgressStderrBuffer:
    """Accumulate stderr chunks and yield status updates per complete line."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, chunk: str) -> list[str]:
        if not chunk:
            return []
        self._buf += chunk
        out: list[str] = []
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            status = status_from_stderr_line(line)
            if status:
                out.append(status)
        return out
