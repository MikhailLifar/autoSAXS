"""Pipeline steps that ask modeling children to Confirm (not real CLI skills)."""

from __future__ import annotations

from ...core.models import RunRequest
from .jobs import JobStep

CONFIRM_SHAPE_STEP = "confirm_shape"
CONFIRM_DR_STEP = "confirm_dr"

_CONFIRM_STEPS = frozenset({CONFIRM_SHAPE_STEP, CONFIRM_DR_STEP})


def is_modeling_confirm_step(name: str) -> bool:
    return str(name or "").strip() in _CONFIRM_STEPS


def confirm_shape_step() -> JobStep:
    """After mono analysis: command guisaxs-shape to Confirm when Auto."""
    return JobStep(
        name=CONFIRM_SHAPE_STEP,
        request=RunRequest(skill_name=CONFIRM_SHAPE_STEP, positional=[], options={}),
    )


def confirm_dr_step() -> JobStep:
    """After poly analysis: command guisaxs-dr to Confirm when Auto."""
    return JobStep(
        name=CONFIRM_DR_STEP,
        request=RunRequest(skill_name=CONFIRM_DR_STEP, positional=[], options={}),
    )
