"""Shared GNOM overlay path helpers for P(r) / D(R) / I(q) plots."""

from __future__ import annotations

import os
import re
from pathlib import Path


def same_gnom_path(a: str, b: str) -> bool:
    if not a or not b:
        return False
    try:
        return os.path.abspath(a) == os.path.abspath(b)
    except OSError:
        return False


def gnom_owned_curve_path(gnom_out_path: str) -> str:
    """
    Intensity curve the GNOM ``.out`` was computed from, if recoverable.

    Owner: the on-disk GNOM result. ``fit_distances`` / ``fit_sizes`` write a
    sibling ``*_atsas.dat`` next to ``gnom_best.out``; that file is the sole
    intensity identity for I(q) overlay and Adjust live preview against that
    ``.out``. Session ``subtracted/sub_*.dat`` may be a newer / differently
    scaled sibling and must not silently replace it.

    Prefers the sibling ``*_atsas.dat``. Falls back to parsing ``Input  1:``
    from the ``.out`` header. Returns ``""`` when neither is available.
    """
    raw = str(gnom_out_path or "").strip()
    if not raw:
        return ""
    try:
        out_p = Path(raw).expanduser().resolve()
    except Exception:
        return ""
    if not out_p.is_file():
        return ""

    parent = out_p.parent
    atsas = [p for p in sorted(parent.glob("*_atsas.dat")) if p.is_file()]
    if len(atsas) == 1:
        return str(atsas[0])
    if len(atsas) > 1:
        stem = out_p.stem
        for prefix in ("_gnom", "gnom_best", "gnom"):
            if stem.endswith(prefix):
                stem = stem[: -len(prefix)]
                break
        stem = stem.strip("_")
        if stem:
            for p in atsas:
                if stem in p.stem:
                    return str(p)
        return str(atsas[0])

    try:
        text = out_p.read_text(errors="replace")
    except OSError:
        return ""
    m = re.search(r"Input\s+1:\s+(\S+)", text)
    if not m:
        return ""
    cand = Path(m.group(1).strip())
    if not cand.is_file():
        # Windows share paths in older outs may not resolve here.
        return ""
    try:
        return str(cand.resolve())
    except Exception:
        return str(cand)
