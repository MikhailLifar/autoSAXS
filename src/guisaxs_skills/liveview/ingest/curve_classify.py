"""Classify dropped/watched 1D SAXS curves for liveview boarding (1D vs Sub)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from autosaxs.core.utils import read_saxs

from ..session.state import LiveviewIntakeMode


class CurveClassifyError(ValueError):
    """Curve cannot be boarded (proxy, unsupported type, unreadable)."""


def _path_parts_lower(path: str) -> Tuple[str, ...]:
    try:
        parts = Path(path).expanduser().resolve().parts
    except Exception:
        parts = Path(os.path.abspath(path)).parts
    return tuple(str(p).lower() for p in parts)


def _is_proxy_path(path: str) -> bool:
    return "averaged_proxy" in _path_parts_lower(path)


def _read_meta(path: str) -> Dict[str, Any]:
    try:
        _q, _I, _s, meta = read_saxs(path)
    except Exception as exc:
        raise CurveClassifyError(f"Cannot read SAXS curve: {path}") from exc
    if meta is None:
        return {}
    if not isinstance(meta, dict):
        return {}
    return meta


def _autosaxs_true(meta: Dict[str, Any]) -> bool:
    v = meta.get("autoSAXS")
    if v is True:
        return True
    if isinstance(v, str) and v.strip().lower() in ("true", "1", "yes"):
        return True
    return False


def classify_curve_boarding(path: str) -> LiveviewIntakeMode:
    """
    Classify a ``.dat`` path for liveview curve boarding.

    1. Reject proxy (``averaged_proxy/`` or ``type: integrated_proxy_1d``).
    2. If ``autoSAXS: true`` → use ``type`` (``sub`` / ``int``); other types reject.
    3. Else basename prefix ``sub`` → Sub; otherwise → 1D.
    """
    raw = (path or "").strip()
    if not raw:
        raise CurveClassifyError("Empty curve path")
    if not raw.lower().endswith(".dat"):
        raise CurveClassifyError(f"Not a .dat curve: {raw}")
    if _is_proxy_path(raw):
        raise CurveClassifyError("Proxy curves (averaged_proxy) are not accepted")

    meta = _read_meta(raw)
    typ = str(meta.get("type") or "").strip().lower()
    if typ == "integrated_proxy_1d":
        raise CurveClassifyError("Proxy curves (integrated_proxy_1d) are not accepted")

    if _autosaxs_true(meta):
        if typ == "sub":
            return LiveviewIntakeMode.CURVE_SUB
        if typ == "int":
            return LiveviewIntakeMode.CURVE_1D
        raise CurveClassifyError(f"Unsupported autosaxs curve type for boarding: {typ or '(missing)'}")

    base = os.path.basename(raw).lower()
    if base.startswith("sub"):
        return LiveviewIntakeMode.CURVE_SUB
    return LiveviewIntakeMode.CURVE_1D


def try_classify_curve_boarding(path: str) -> Tuple[Optional[LiveviewIntakeMode], Optional[str]]:
    """Return ``(boarding, None)`` or ``(None, error_message)``."""
    try:
        return classify_curve_boarding(path), None
    except CurveClassifyError as exc:
        return None, str(exc)
