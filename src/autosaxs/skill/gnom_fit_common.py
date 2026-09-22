"""Shared helpers for ATSAS GNOM/DATGNOM skills (fit_distances, fit_sizes)."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import numpy as np

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


def q_to_point_1based(q_nm: np.ndarray, q_target: float) -> int:
    """Nearest 1-based point index for ``q_target`` (nm⁻¹) on ``q_nm``."""
    q_nm = np.asarray(q_nm, dtype=float)
    if q_nm.size == 0:
        raise ValueError("q_to_point_1based: empty q array")
    if not np.isfinite(q_target):
        raise ValueError("q_to_point_1based: q_target is not finite")
    idx = int(np.argmin(np.abs(q_nm - float(q_target))))
    return idx + 1


def resolve_first_last(
    q_nm: np.ndarray,
    *,
    first: Optional[int] = None,
    last: Optional[int] = None,
    q_min: Optional[float] = None,
    q_max: Optional[float] = None,
    fallback_q_min: Optional[float] = None,
    skill_id: str = "fit_distances",
) -> Tuple[int, Optional[int]]:
    """
    Resolve GNOM/DATGNOM ``--first`` / ``--last`` (1-based).

    ``q_min`` / ``q_max`` (nm⁻¹) are an indirect way to set the same indices.
    Do not pass both ``first`` and ``q_min`` (or both ``last`` and ``q_max``).
    When neither ``first`` nor ``q_min`` is set, ``fallback_q_min`` (e.g. Guinier)
    is required.
    """
    if first is not None and q_min is not None:
        raise ValueError(f"{skill_id}: provide first or q_min, not both")
    if last is not None and q_max is not None:
        raise ValueError(f"{skill_id}: provide last or q_max, not both")

    q_nm = np.asarray(q_nm, dtype=float)
    n_pts = int(q_nm.size)

    if first is not None:
        first_pt = int(first)
    elif q_min is not None:
        first_pt = q_to_point_1based(q_nm, float(q_min))
    elif fallback_q_min is not None:
        first_pt = q_to_point_1based(q_nm, float(fallback_q_min))
    else:
        raise RuntimeError(
            f"{skill_id}: cannot derive --first without first, q_min, or Guinier q_min."
        )

    if last is not None:
        last_pt: Optional[int] = int(last)
    elif q_max is not None:
        last_pt = q_to_point_1based(q_nm, float(q_max))
    else:
        last_pt = None

    if first_pt < 1 or first_pt >= n_pts:
        raise ValueError(
            f"{skill_id}: require 1 <= first < n_points ({n_pts}); got first={first_pt}",
        )
    if last_pt is not None:
        if last_pt < 1 or last_pt > n_pts or first_pt >= last_pt:
            raise ValueError(
                f"{skill_id}: require 1 <= first < last <= n_points ({n_pts}); "
                f"got first={first_pt}, last={last_pt}",
            )
    return first_pt, last_pt


def _unwrap_scalar(val: Any) -> Any:
    if isinstance(val, list) and len(val) == 1:
        return val[0]
    return val
