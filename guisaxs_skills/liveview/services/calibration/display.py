"""
Build human-readable rows for the liveview calibration summary table from ``refined.yml``.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import List, Optional, Tuple

import yaml


def _load_pixel_size_m_from_integrator(integrator_dir: Path) -> Optional[Tuple[float, float]]:
    """Return ``(pixel_size_y, pixel_size_x)`` in metres from ``detector_params.json``."""
    jpath = integrator_dir / "detector_params.json"
    if not jpath.is_file():
        return None
    try:
        data = json.loads(jpath.read_text(encoding="utf-8", errors="replace"))
    except (OSError, TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    ps = data.get("pixel_size")
    if not isinstance(ps, (list, tuple)) or len(ps) < 2:
        return None
    try:
        sy = float(ps[0])
        sx = float(ps[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(sy) and math.isfinite(sx)) or sy == 0 or sx == 0:
        return None
    return sy, sx


def _resolve_integrator_dir(yml_path: Path, integrator_dir: Optional[Path]) -> Optional[Path]:
    if integrator_dir is not None:
        ip = Path(integrator_dir).expanduser().resolve()
        if (ip / "detector_params.json").is_file():
            return ip
    cand = yml_path.parent / "integrator"
    if (cand / "detector_params.json").is_file():
        return cand.resolve()
    return None


def refined_yml_display_rows(
    path: str | Path,
    *,
    integrator_dir: Optional[Path] = None,
) -> List[Tuple[str, str]]:
    """
    Return (label, value) rows: distance, beam center in pixels (``cy``, ``cx``), rotations, wavelength in nm.

    Center pixels follow autosaxs ``refine()`` convention: ``poni1 = pixel_size[0] * cy``,
    ``poni2 = pixel_size[1] * cx``. Pixel sizes are read from the integrator directory
    (``detector_params.json``), either ``integrator_dir`` or ``<refined.yml parent>/integrator``.
    """
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        return []
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, yaml.YAMLError, TypeError):
        return []
    if not isinstance(raw, dict):
        return []

    rows: list[tuple[str, str]] = []

    def _num(key: str) -> Optional[float]:
        v = raw.get(key)
        if isinstance(v, (int, float)) and math.isfinite(float(v)):
            return float(v)
        return None

    dist = _num("dist")
    if dist is not None:
        rows.append(("Sample-detector distance (m)", f"{dist:.6g}"))

    poni1 = _num("poni1")
    poni2 = _num("poni2")
    idir = _resolve_integrator_dir(p, integrator_dir)
    ps_m = _load_pixel_size_m_from_integrator(idir) if idir is not None else None
    if ps_m is not None and poni1 is not None and poni2 is not None:
        sy, sx = ps_m
        cy = poni1 / sy
        cx = poni2 / sx
        rows.append(("cy (px)", f"{cy:.3f}"))
        rows.append(("cx (px)", f"{cx:.3f}"))

    for i in (1, 2, 3):
        k = f"rot{i}"
        r = _num(k)
        if r is not None:
            deg = math.degrees(r)
            rows.append((f"Rotation {i} (rad / deg)", f"{r:.6f} / {deg:.3f}"))

    wl = _num("wavelength")
    if wl is not None:
        rows.append(("Wavelength (nm)", f"{wl * 1e9:.6f}"))

    return rows
