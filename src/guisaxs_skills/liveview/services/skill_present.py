"""Presentation helpers for liveview UI panels.

Panels must not import ``autosaxs.skill`` directly (importlinter). This module is the
sole liveview boundary for skill-side readout helpers used by UI presentation.
"""
from __future__ import annotations

from autosaxs.skill.fit_guinier.guinier import guinier_point_range_1based
from autosaxs.skill.gnom_fit_common import failure_message_from_result, is_atsas_fit_ok
from autosaxs.skill.model_mixture.mixture import (
    distribution_curve_for_model,
    parse_mixture_fit_file,
    select_best_mixture_row,
)

try:
    from autosaxs.skill.model_bodies import BODIES_SHAPES_LIST
except Exception:  # pragma: no cover - ATSAS-free / partial installs
    BODIES_SHAPES_LIST = [
        "cylinder",
        "dumbbell",
        "ellipsoid",
        "elliptic-cylinder",
        "hollow-cylinder",
        "hollow-sphere",
        "parallelepiped",
        "rotation-ellipsoid",
    ]

__all__ = [
    "BODIES_SHAPES_LIST",
    "distribution_curve_for_model",
    "failure_message_from_result",
    "guinier_point_range_1based",
    "is_atsas_fit_ok",
    "parse_mixture_fit_file",
    "select_best_mixture_row",
]
