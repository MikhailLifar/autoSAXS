"""
Calibrant Bragg-line helpers shared by calibrate and GUI overlays.

``q`` is always in nm⁻¹ (autoSAXS convention). Wavelength for pyFAI is metres.
"""

from __future__ import annotations

import numpy as np
from pyFAI.calibrant import CALIBRANT_FACTORY


def theoretical_peaks_q_nm_inv(
    calibrant_name: str,
    wavelength_m: float,
) -> np.ndarray:
    """
    Theoretical calibrant peak positions in ``q`` (nm⁻¹) for a named calibrant.

    Uses pyFAI ``CALIBRANT_FACTORY`` Bragg ``2θ`` lines at ``wavelength_m`` (metres),
    converted with ``q = 4π sin(θ) / λ`` and scaled to nm⁻¹ — the same conversion as
    geometry refinement in ``autosaxs.skill.calibrate``.

    Args:
        calibrant_name: pyFAI calibrant id (e.g. ``\"AgBh\"``, ``\"LaB6\"``).
        wavelength_m: X-ray wavelength in metres (same units as ``refined.yml``).

    Returns:
        1-D float array of theoretical ``q`` positions in nm⁻¹.
    """
    name = str(calibrant_name or "").strip()
    if not name:
        raise ValueError("calibrant_name must be a non-empty string")
    wl = float(wavelength_m)
    if not np.isfinite(wl) or wl <= 0:
        raise ValueError(f"wavelength_m must be a positive finite value in metres; got {wavelength_m!r}")

    calibrant = CALIBRANT_FACTORY(name)
    calibrant.set_wavelength(wl)
    tth = np.asarray(calibrant.get_2th(), dtype=float)
    if tth.size == 0:
        return np.asarray([], dtype=float)
    # q [nm^-1] = 4π sin(θ) / λ[m] * 1e-9
    return (4.0 * np.pi * np.sin(tth / 2.0) / wl) * 1e-9
