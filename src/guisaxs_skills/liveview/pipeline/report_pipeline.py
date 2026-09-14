"""Job steps for per-sample report assembly in liveview."""

from __future__ import annotations

from pathlib import Path

from ...core.models import RunRequest
from .jobs import JobStep


def report_individual_step(*, output_root: Path, basename: str) -> JobStep:
    """
    Assemble ``*_report_individual.md`` under ``output_root`` into ``reports/``.

    ``basename`` is the sample stem (TIFF stem / profile stem without ``int_``/``sub_``).
    """
    root = output_root.expanduser().resolve()
    stem = (basename or "").strip()
    return JobStep(
        name="report_individual",
        request=RunRequest(
            skill_name="report_individual",
            positional=[str(root), stem],
            options={"use_cache": False},
        ),
    )
