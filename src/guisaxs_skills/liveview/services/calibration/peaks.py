"""
Resolve theoretical calibrant peak ``q`` (nm⁻¹) for the liveview curve viewer.

Peak positions come from autoSAXS ``theoretical_peaks_q_nm_inv`` (calibrant name +
wavelength) — the GUI does not embed Bragg tables.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import List, Optional, Sequence, Union

import yaml

from autosaxs.core.calibrant import theoretical_peaks_q_nm_inv


def wavelength_m_from_refined_yml(path: Union[str, Path, None]) -> Optional[float]:
    """Return wavelength in metres from ``refined.yml``, or ``None`` if missing/invalid."""
    if path is None:
        return None
    p = Path(path).expanduser()
    if not p.is_file():
        return None
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, yaml.YAMLError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    wl = raw.get("wavelength")
    try:
        val = float(wl)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(val) or val <= 0:
        return None
    return val


def calibrant_name_from_calibration_dat(dat_path: Union[str, Path, None]) -> Optional[str]:
    """Return calibrant type from ``calibration_curve.dat`` metadata written by ``calibrate``."""
    if dat_path is None:
        return None
    p = Path(dat_path).expanduser()
    if not p.is_file():
        return None
    try:
        from autosaxs.core.utils import read_saxs

        _q, _I, _s, meta = read_saxs(str(p))
    except Exception:
        return None
    if not isinstance(meta, dict):
        return None
    name = meta.get("calibrant")
    if name is None:
        name = meta.get("calibrant_name")
    text = str(name or "").strip()
    return text or None


def _guess_refined_yml_near_curve(dat_path: Union[str, Path, None]) -> Optional[Path]:
    """``calibrate`` writes ``calibration_plots/calibration_curve.dat`` beside ``../refined.yml``."""
    if dat_path is None:
        return None
    p = Path(dat_path).expanduser()
    for cand in (p.parent.parent / "refined.yml", p.parent / "refined.yml"):
        if cand.is_file():
            return cand.resolve()
    return None


def theoretical_peak_q_nm_for_calibration_curve(
    dat_path: Union[str, Path, None] = None,
    *,
    refined_yml: Union[str, Path, None] = None,
    calibrant_name: Optional[str] = None,
    wavelength_m: Optional[float] = None,
) -> List[float]:
    """
    Theoretical peak ``q`` (nm⁻¹) for overlaying on a calibration curve viewer.

    Resolves calibrant type from ``dat_path`` metadata when ``calibrant_name`` is
    omitted, and wavelength from ``refined_yml`` (or a sibling guess next to the
    curve) when ``wavelength_m`` is omitted.
    Calls ``autosaxs.core.calibrant.theoretical_peaks_q_nm_inv``.
    """
    name = (calibrant_name or "").strip() or (calibrant_name_from_calibration_dat(dat_path) or "")
    yml = refined_yml
    if wavelength_m is None and (yml is None or not Path(yml).expanduser().is_file()):
        yml = _guess_refined_yml_near_curve(dat_path)
    wl = wavelength_m if wavelength_m is not None else wavelength_m_from_refined_yml(yml)
    if not name or wl is None:
        return []
    try:
        peaks = theoretical_peaks_q_nm_inv(name, float(wl))
    except Exception:
        return []
    out: list[float] = []
    for q in peaks:
        try:
            v = float(q)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            out.append(v)
    return out


def peaks_in_curve_q_window(
    peaks_q_nm: Sequence[float],
    dat_path: Union[str, Path, None],
) -> List[float]:
    """Keep peaks that fall inside the loaded curve's ``q`` span (when readable)."""
    if not peaks_q_nm or dat_path is None:
        return list(peaks_q_nm or [])
    p = Path(dat_path).expanduser()
    if not p.is_file():
        return list(peaks_q_nm)
    try:
        from autosaxs.core.utils import read_saxs
        import numpy as np

        q, _I, _s, _m = read_saxs(str(p))
        q = np.asarray(q, dtype=float)
        finite = q[np.isfinite(q)]
        if finite.size == 0:
            return list(peaks_q_nm)
        q0 = float(finite.min())
        q1 = float(finite.max())
    except Exception:
        return list(peaks_q_nm)
    return [float(v) for v in peaks_q_nm if q0 <= float(v) <= q1]
