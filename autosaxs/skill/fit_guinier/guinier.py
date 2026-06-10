"""
Guinier analysis skill layer: ATSAS I/O and orchestration.

Pure Guinier math lives in ``autosaxs.core.guinier``.
"""
from __future__ import annotations

from typing import Any, Dict, Optional
import os
import re
import subprocess
import tempfile

import numpy as np

from autosaxs.core.guinier import (  # noqa: F401
    ADAPTIVE_I_START_INDEX_MAX,
    ADAPTIVE_I_START_Q_MAX_NM,
    ADAPTIVE_N_MIN,
    _adaptive_i_start_allowed,
    _enumerate_adaptive_candidates,
    _guinier_fit_n_points,
    _select_adaptive_candidate,
    _validation_r2_or_nan,
    find_guinier_region,
    get_guinier_candidates,
    run_adaptive_guinier,
)


def parse_autorg_output(text: str, q: np.ndarray) -> Optional[Dict[str, Any]]:
    """
    Parse AUTORG stdout/stderr text (ATSAS) into a structured dict.
    """
    if not (text or "").strip():
        return None
    q = np.asarray(q, dtype=float)
    n_total = int(len(q))

    mr = re.search(r"Rg\s*=\s*([\d\.\+\-eE]+)", text)
    if not mr:
        return None
    try:
        autorg_rg = float(mr.group(1))
    except ValueError:
        return None

    mi0 = re.search(r"I\(0\)\s*=\s*([\d\.\+\-eE]+)", text)
    mq = re.search(r"Quality:\s*([\d\.\+\-eE]+)", text)
    mpts = re.search(r"Points\s+(\d+)\s+to\s+(\d+)\s+\((\d+)\s+total\)", text)

    autorg_i0 = float(mi0.group(1)) if mi0 else None
    autorg_quality: Optional[float] = None
    if mq:
        try:
            autorg_quality = float(mq.group(1))
        except ValueError:
            autorg_quality = None
    autorg_n_pts = int(mpts.group(3)) if mpts else None

    q_min_autorg: Optional[float] = None
    q_max_autorg: Optional[float] = None
    first_point_1based: Optional[int] = None
    last_point_1based: Optional[int] = None

    if mpts is not None:
        try:
            i1, i2 = int(mpts.group(1)), int(mpts.group(2))
        except ValueError:
            i1, i2 = 0, 0
        if i1 > 0 and i2 > 0:
            first_point_1based = max(1, min(i1, n_total))
            last_point_1based = max(1, min(i2, n_total))
            j1 = max(0, min(i1 - 1, n_total - 1))
            j2 = max(0, min(i2 - 1, n_total - 1))
            if j1 <= j2:
                q_min_autorg = float(q[j1])
                q_max_autorg = float(q[j2])

    interval: Optional[tuple[float, float]] = None
    if q_min_autorg is not None and q_max_autorg is not None:
        interval = (q_min_autorg, q_max_autorg)

    return {
        "Rg": autorg_rg,
        "I0": autorg_i0,
        "n_points": autorg_n_pts,
        "fit_quality": autorg_quality,
        "guinier_interval": interval,
        "first_point_1based": first_point_1based,
        "last_point_1based": last_point_1based,
    }


def run_autorg_atsas(atsas_dat_path: str, q: np.ndarray) -> Optional[Dict[str, Any]]:
    """Run ATSAS ``autorg`` on an existing ATSAS-format .dat file and parse its output."""
    if not atsas_dat_path or not os.path.isfile(atsas_dat_path):
        return None
    try:
        proc = subprocess.run(
            ["autorg", atsas_dat_path],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    content = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return parse_autorg_output(content, q)


def run_guinier_analysis(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray] = None,
    atsas_dat_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run Guinier analyses (first5, first10, autorg, adaptive) and return a unified result dict.
    """
    from autosaxs.core.utils import write_saxs_atsas_format

    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    sigma = np.asarray(sigma, dtype=float) if sigma is not None else None

    out: Dict[str, Any] = {
        "first5": None,
        "first10": None,
        "autorg": None,
        "adaptive": None,
        "chosen": None,
        "chosen_Rg": None,
        "chosen_I0": None,
        "chosen_quality": None,
        "chosen_n_points": None,
        "chosen_interval": None,
        "chosen_validation_r2": None,
        "classification": None,
        "quality_class": None,
        "selection_mode": None,
        "rg_min": None,
        "rg_max": None,
    }

    res5 = _guinier_fit_n_points(q, I, 5)
    if res5 is not None:
        out["first5"] = res5

    res10 = _guinier_fit_n_points(q, I, 10)
    if res10 is not None:
        out["first10"] = res10

    path_for_autorg = atsas_dat_path
    tmp_autorg_created = False
    if path_for_autorg is None:
        fd, path_for_autorg = tempfile.mkstemp(suffix=".dat", prefix="autosaxs_guinier_")
        try:
            os.close(fd)
            write_saxs_atsas_format(path_for_autorg, q, I, sigma)
            tmp_autorg_created = True
        except Exception:
            path_for_autorg = None
    if path_for_autorg is not None and os.path.exists(path_for_autorg):
        try:
            parsed = run_autorg_atsas(path_for_autorg, q)
            if parsed is not None:
                out["autorg"] = {
                    "Rg": parsed["Rg"],
                    "I0": parsed.get("I0"),
                    "n_points": parsed.get("n_points"),
                    "fit_quality": parsed.get("fit_quality"),
                    "guinier_interval": parsed.get("guinier_interval"),
                    "first_point_1based": parsed.get("first_point_1based"),
                    "last_point_1based": parsed.get("last_point_1based"),
                }
        finally:
            if tmp_autorg_created and path_for_autorg != atsas_dat_path:
                try:
                    if os.path.exists(path_for_autorg):
                        os.remove(path_for_autorg)
                except OSError:
                    pass

    try:
        out["adaptive"] = run_adaptive_guinier(q, I, sigma=sigma)
    except ValueError:
        out["adaptive"] = None

    if out["adaptive"] is not None:
        r = out["adaptive"]
        out["chosen"] = "adaptive"
        out["chosen_Rg"] = r.get("Rg")
        out["chosen_I0"] = r.get("I0")
        out["chosen_quality"] = r.get("fit_quality")
        out["chosen_n_points"] = r.get("n_points")
        out["chosen_interval"] = r.get("guinier_interval")
        out["chosen_validation_r2"] = r.get("validation_r2")
        out["classification"] = r.get("classification")
        out["quality_class"] = r.get("quality_class")
        out["selection_mode"] = r.get("selection_mode")
        out["rg_min"] = r.get("rg_min")
        out["rg_max"] = r.get("rg_max")

    return out
