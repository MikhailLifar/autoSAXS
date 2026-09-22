"""q-min / q-max ↔ first / last helpers for GNOM adjust wizards."""

from __future__ import annotations

from typing import Any, Mapping, Optional, Tuple

import numpy as np
from PyQt5.QtWidgets import QDoubleSpinBox

from autosaxs.skill.gnom_fit_common import q_to_point_1based, resolve_first_last


def make_q_min_spin() -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setDecimals(4)
    sp.setRange(0.0, 100.0)
    sp.setSingleStep(0.01)
    sp.setValue(0.05)
    return sp


def make_q_max_spin() -> QDoubleSpinBox:
    sp = QDoubleSpinBox()
    sp.setDecimals(4)
    sp.setRange(0.0, 100.0)
    sp.setSingleStep(0.01)
    sp.setSpecialValueText("(none)")
    sp.setValue(0.0)
    return sp


def point_to_q(q_nm: np.ndarray, point_1based: int) -> Optional[float]:
    q_nm = np.asarray(q_nm, dtype=float)
    if q_nm.size == 0:
        return None
    i = int(point_1based) - 1
    if i < 0 or i >= q_nm.size:
        return None
    v = float(q_nm[i])
    return v if np.isfinite(v) else None


def q_bounds_from_params(
    params: Mapping[str, Any],
    q_nm: Optional[np.ndarray],
) -> Tuple[Optional[float], Optional[float]]:
    """Prefer explicit q_min/q_max; else convert first/last via ``q_nm``."""
    q_min = params.get("q_min")
    q_max = params.get("q_max")
    try:
        q_min_f = float(q_min) if q_min is not None else None
    except (TypeError, ValueError):
        q_min_f = None
    try:
        q_max_f = float(q_max) if q_max is not None else None
    except (TypeError, ValueError):
        q_max_f = None
    if q_min_f is not None and np.isfinite(q_min_f) and q_min_f > 0:
        pass
    elif q_nm is not None and params.get("first") is not None:
        q_min_f = point_to_q(q_nm, int(params["first"]))
    else:
        q_min_f = None
    if q_max_f is not None and np.isfinite(q_max_f) and q_max_f > 0:
        pass
    elif q_nm is not None and params.get("last") is not None:
        q_max_f = point_to_q(q_nm, int(params["last"]))
    else:
        q_max_f = None
    return q_min_f, q_max_f


def first_last_from_q_values(
    q_nm: np.ndarray,
    *,
    q_min: Optional[float],
    q_max: Optional[float],
) -> Tuple[int, Optional[int]]:
    return resolve_first_last(
        q_nm,
        q_min=q_min,
        q_max=q_max if q_max is not None and float(q_max) > 0 else None,
        fallback_q_min=float(np.asarray(q_nm, dtype=float)[0]) if len(q_nm) else None,
        skill_id="gnom_adjust",
    )


__all__ = [
    "first_last_from_q_values",
    "make_q_max_spin",
    "make_q_min_spin",
    "point_to_q",
    "q_bounds_from_params",
    "q_to_point_1based",
]
