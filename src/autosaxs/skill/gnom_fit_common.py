"""Shared helpers for ATSAS GNOM/DATGNOM skills (fit_distances, fit_sizes)."""

from __future__ import annotations

from typing import Any, Dict, Optional

ATSAS_TOOL_BY_SKILL: Dict[str, str] = {
    "fit_distances": "DATGNOM",
    "fit_sizes": "GNOM",
}


def atsas_tool_label(skill_id: str) -> str:
    return ATSAS_TOOL_BY_SKILL.get(skill_id, "ATSAS GNOM/DATGNOM")


def default_atsas_failure_message(skill_id: str) -> str:
    tool = atsas_tool_label(skill_id)
    return (
        f"{tool} did not produce a valid distribution: every trial failed. "
        "This often indicates problems with integration, detector masking, or buffer "
        "subtraction — review the 1D curve and upstream processing before refitting."
    )


def is_atsas_fit_ok(result: Any) -> bool:
    """True when a skill result dict represents a successful ATSAS fit."""
    if not isinstance(result, dict):
        return False
    atsas_ok = _unwrap_scalar(result.get("atsas_fit_ok"))
    if atsas_ok is False:
        return False
    gnom_failed = _unwrap_scalar(result.get("gnom_failed"))
    if gnom_failed is True:
        return False
    if isinstance(gnom_failed, str) and gnom_failed.strip().lower() in ("true", "1", "yes"):
        return False
    best = _unwrap_scalar(result.get("best_gnom_out_path"))
    if isinstance(best, str) and best.strip():
        return True
    if atsas_ok is True:
        return True
    return False


def failure_message_from_result(result: Any, *, skill_id: str) -> str:
    if isinstance(result, dict):
        msg = result.get("failure_message")
        if isinstance(msg, list) and len(msg) == 1:
            msg = msg[0]
        if isinstance(msg, str) and msg.strip():
            return msg.strip()
    return default_atsas_failure_message(skill_id)


def _unwrap_scalar(val: Any) -> Any:
    if isinstance(val, list) and len(val) == 1:
        return val[0]
    return val
