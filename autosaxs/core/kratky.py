"""Dimensionless Kratky analysis (no I/O)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

# Analytical reference for a compact globule in the Guinier limit: y(x) = x² exp(−x²/3).
SPHERE_X_MAX_REF = math.sqrt(3.0)
SPHERE_Y_MAX_REF = 3.0 / math.e


@dataclass(frozen=True)
class KratkyThresholds:
    globular_x_min: float = 1.65
    globular_x_max: float = 1.85
    globular_y_min: float = 1.0
    globular_y_max: float = 1.2
    elongated_x_min: float = 1.85
    elongated_x_max: float = 2.5
    elongated_y_min: float = 1.15
    coil_plateau_y: float = 2.0
    coil_plateau_tol: float = 0.25
    coil_high_x_min: float = 3.0
    x_search_min: float = 0.5
    x_search_max: float = 4.0


def compute_dimensionless_kratky(
    q: np.ndarray,
    I: np.ndarray,
    rg_nm: float,
    i0: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return (qRg, Y) with Y = (q·Rg)² · I(q) / I(0)."""
    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    if not (np.isfinite(rg_nm) and rg_nm > 0):
        raise ValueError("kratky: rg_nm must be a positive finite number")
    if not (np.isfinite(i0) and i0 > 0):
        raise ValueError("kratky: i0 must be a positive finite number")
    q_rg = q * float(rg_nm)
    y = (q_rg ** 2) * (I / float(i0))
    return q_rg, y


def find_kratky_peak(
    q_rg: np.ndarray,
    y: np.ndarray,
    *,
    x_min: float,
    x_max: float,
) -> Tuple[Optional[float], Optional[float], int]:
    """Global maximum of Y in [x_min, x_max]; returns (x_max_peak, y_max_peak, n_valid)."""
    q_rg = np.asarray(q_rg, dtype=float)
    y = np.asarray(y, dtype=float)
    valid = (
        np.isfinite(q_rg)
        & np.isfinite(y)
        & (q_rg >= x_min)
        & (q_rg <= x_max)
        & (y > 0)
    )
    n_valid = int(valid.sum())
    if n_valid == 0:
        return None, None, 0
    idx = int(np.nanargmax(y[valid]))
    x_peak = float(q_rg[valid][idx])
    y_peak = float(y[valid][idx])
    return x_peak, y_peak, n_valid


def _high_x_tail_mean(q_rg: np.ndarray, y: np.ndarray, *, x_min: float) -> Optional[float]:
    mask = np.isfinite(q_rg) & np.isfinite(y) & (q_rg >= x_min)
    if mask.sum() < 3:
        return None
    return float(np.nanmean(y[mask]))


def classify_kratky_conformation(
    x_peak: Optional[float],
    y_peak: Optional[float],
    *,
    tail_mean_y: Optional[float],
    thresholds: KratkyThresholds,
) -> Tuple[str, List[str]]:
    """
    Model-free conformation label from dimensionless Kratky peak and high-x tail.

    Returns (classification, rationale_lines).
    """
    reasons: List[str] = []
    t = thresholds

    if x_peak is None or y_peak is None:
        return "unknown", ["No valid Kratky peak found in the search window."]

    if tail_mean_y is not None and abs(tail_mean_y - t.coil_plateau_y) <= t.coil_plateau_tol:
        if y_peak < t.globular_y_min or x_peak > t.elongated_x_max:
            reasons.append(
                f"High-x tail mean Y ≈ {tail_mean_y:.3f} near Debye plateau ({t.coil_plateau_y:.2f})."
            )
            return "coil", reasons

    in_globular_x = t.globular_x_min <= x_peak <= t.globular_x_max
    in_globular_y = t.globular_y_min <= y_peak <= t.globular_y_max
    if in_globular_x and in_globular_y:
        reasons.append(
            f"Peak at (q·Rg, Y) = ({x_peak:.3f}, {y_peak:.3f}) "
            f"within globular bands "
            f"x ∈ [{t.globular_x_min}, {t.globular_x_max}], "
            f"Y ∈ [{t.globular_y_min}, {t.globular_y_max}]."
        )
        return "globular", reasons

    elongated_by_x = t.elongated_x_min <= x_peak <= t.elongated_x_max
    elongated_by_y = y_peak > t.elongated_y_min
    if elongated_by_x or elongated_by_y:
        parts = []
        if elongated_by_x:
            parts.append(f"x_peak = {x_peak:.3f} in [{t.elongated_x_min}, {t.elongated_x_max}]")
        if elongated_by_y:
            parts.append(f"y_peak = {y_peak:.3f} > {t.elongated_y_min}")
        reasons.append("Elongated signature: " + "; ".join(parts) + ".")
        return "elongated", reasons

    if tail_mean_y is not None and tail_mean_y >= t.coil_plateau_y - t.coil_plateau_tol:
        reasons.append(
            f"Elevated high-x tail (mean Y ≈ {tail_mean_y:.3f}) suggests coil-like plateau."
        )
        return "coil", reasons

    reasons.append(
        f"Peak ({x_peak:.3f}, {y_peak:.3f}) does not match globular or elongated reference bands."
    )
    return "intermediate", reasons


def analyze_dimensionless_kratky(
    q: np.ndarray,
    I: np.ndarray,
    *,
    rg_nm: float,
    i0: float,
    thresholds: Optional[KratkyThresholds] = None,
) -> Dict[str, Any]:
    """Run dimensionless Kratky analysis and return metrics + classification."""
    t = thresholds or KratkyThresholds()
    q_rg, y = compute_dimensionless_kratky(q, I, rg_nm, i0)
    x_peak, y_peak, n_valid = find_kratky_peak(
        q_rg,
        y,
        x_min=t.x_search_min,
        x_max=t.x_search_max,
    )
    tail_mean = _high_x_tail_mean(q_rg, y, x_min=t.coil_high_x_min)
    classification, rationale = classify_kratky_conformation(
        x_peak,
        y_peak,
        tail_mean_y=tail_mean,
        thresholds=t,
    )
    return {
        "q_rg": q_rg,
        "y": y,
        "x_max": x_peak,
        "y_max": y_peak,
        "n_valid_peak_search": n_valid,
        "tail_mean_y": tail_mean,
        "classification": classification,
        "rationale": rationale,
        "rg_nm": float(rg_nm),
        "i0": float(i0),
        "sphere_x_max_ref": SPHERE_X_MAX_REF,
        "sphere_y_max_ref": SPHERE_Y_MAX_REF,
        "thresholds": t,
    }
