"""Shared helpers for ATSAS GNOM/DATGNOM skills (fit_distances, fit_sizes)."""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

import numpy as np

ATSAS_TOOL_BY_SKILL: Dict[str, str] = {
    "fit_distances": "DATGNOM",
    "fit_sizes": "GNOM",
}

# Shannon channel caps for silent default q_max (when user omits last/q_max).
N_SHANNON_CAP_FIT_DISTANCES = 15
N_SHANNON_CAP_FIT_SIZES = 25
DEFAULT_Q_SIGNAL_SNR = 2.0
DEFAULT_Q_SIGNAL_WINDOW = 11


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


def q_signal_max_nm(
    q_nm: np.ndarray,
    i: np.ndarray,
    sigma: Optional[np.ndarray],
    *,
    snr_min: float = DEFAULT_Q_SIGNAL_SNR,
    window: int = DEFAULT_Q_SIGNAL_WINDOW,
) -> Optional[float]:
    """
    Largest q whose trailing window still has median I/σ >= ``snr_min``.

    Returns None when σ is missing/unusable (caller should skip this cap).
    """
    q = np.asarray(q_nm, dtype=float)
    inten = np.asarray(i, dtype=float)
    if q.size == 0 or inten.size != q.size:
        return None
    if sigma is None:
        return None
    sig = np.asarray(sigma, dtype=float)
    if sig.size != q.size:
        return None
    mask = np.isfinite(q) & np.isfinite(inten) & np.isfinite(sig) & (sig > 0) & (q > 0)
    if int(np.count_nonzero(mask)) < 3:
        return None
    q = q[mask]
    snr = inten[mask] / sig[mask]
    snr = np.where(np.isfinite(snr), snr, 0.0)
    w = max(3, int(window))
    if w % 2 == 0:
        w += 1
    half = w // 2
    best: Optional[float] = None
    for i_end in range(half, q.size):
        lo = max(0, i_end - w + 1)
        med = float(np.median(snr[lo : i_end + 1]))
        if med >= float(snr_min):
            best = float(q[i_end])
    return best


def suggest_q_max_nm(
    q_nm: np.ndarray,
    i: np.ndarray,
    sigma: Optional[np.ndarray],
    *,
    d_est_nm: Optional[float],
    n_cap: int,
) -> Tuple[float, Dict[str, Any]]:
    """
    Silent default high-q bound when the user omits ``last`` / ``q_max``.

    ``q_max_default = min(q_file_max, q_signal, q_shannon_cap)`` with missing
    caps skipped. Always returns a finite q from the file range when possible.
    """
    q = np.asarray(q_nm, dtype=float)
    finite_q = q[np.isfinite(q) & (q > 0)]
    if finite_q.size == 0:
        raise ValueError("suggest_q_max_nm: no positive finite q values")
    q_file_max = float(np.max(finite_q))
    caps: Dict[str, Any] = {"q_file_max": q_file_max}

    candidates = [q_file_max]
    q_sig = q_signal_max_nm(q, i, sigma)
    if q_sig is not None and np.isfinite(q_sig) and q_sig > 0:
        caps["q_signal"] = float(q_sig)
        candidates.append(float(q_sig))
    else:
        caps["q_signal"] = None

    q_shannon = None
    try:
        d_est = float(d_est_nm) if d_est_nm is not None else float("nan")
    except (TypeError, ValueError):
        d_est = float("nan")
    n_c = int(n_cap)
    if np.isfinite(d_est) and d_est > 0 and n_c > 0:
        q_shannon = float(n_c) * math.pi / d_est
        caps["q_shannon_cap"] = q_shannon
        caps["d_est_nm"] = d_est
        caps["n_cap"] = n_c
        candidates.append(q_shannon)
    else:
        caps["q_shannon_cap"] = None

    q_max = float(min(candidates))
    # Keep inside the measured range (shannon cap can undershoot file min).
    q_min_file = float(np.min(finite_q))
    if q_max < q_min_file:
        q_max = q_file_max
    caps["q_max_default"] = q_max
    binding = []
    if abs(q_max - q_file_max) <= 1e-12 * max(1.0, abs(q_file_max)):
        binding.append("q_file_max")
    if caps.get("q_signal") is not None and abs(q_max - float(caps["q_signal"])) <= 1e-12 * max(
        1.0, abs(float(caps["q_signal"]))
    ):
        binding.append("q_signal")
    if caps.get("q_shannon_cap") is not None and abs(
        q_max - float(caps["q_shannon_cap"])
    ) <= 1e-12 * max(1.0, abs(float(caps["q_shannon_cap"]))):
        binding.append("q_shannon_cap")
    caps["binding"] = binding or ["q_file_max"]
    return q_max, caps


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
