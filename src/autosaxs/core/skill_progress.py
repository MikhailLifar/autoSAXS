"""Stable stderr progress lines for long-running skills (GUI / liveview parsers)."""

from __future__ import annotations

import sys
from typing import Any, Mapping, Optional


def emit_skill_progress(
    skill: str,
    stage: str,
    *,
    extra: Optional[Mapping[str, Any]] = None,
    **fields: Any,
) -> None:
    """
    Write one parseable progress line to stderr.

    Grammar (single line)::

        autosaxs.progress skill=<name> stage=<stage> [key=value ...]

    Example::

        autosaxs.progress skill=model_dam stage=dammif run=2/5
    """
    parts = [f"autosaxs.progress skill={skill} stage={stage}"]
    merged: dict[str, Any] = {}
    if extra:
        merged.update(dict(extra))
    merged.update(fields)
    for key, value in merged.items():
        if value is None:
            continue
        parts.append(f"{key}={value}")
    print(" ".join(parts), file=sys.stderr, flush=True)
