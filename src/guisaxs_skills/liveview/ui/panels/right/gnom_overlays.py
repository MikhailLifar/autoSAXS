"""Shared GNOM overlay path helpers for P(r) / D(R) plots."""

from __future__ import annotations

import os

FORCE_ZERO_OFF_OUT = "gnom_force_zero_off.out"


def resolve_force_zero_off_path(ens_dir: str) -> str:
    """
    Return at most one force-zero-off ``.out`` under ``ens_dir``.

    Prefers the stable skill name ``gnom_force_zero_off.out``; falls back to the
    newest legacy ``*_force_zero_off.out`` if present.
    """
    if not ens_dir or not os.path.isdir(ens_dir):
        return ""
    canonical = os.path.join(ens_dir, FORCE_ZERO_OFF_OUT)
    if os.path.isfile(canonical):
        return canonical
    cands: list[str] = []
    try:
        names = os.listdir(ens_dir)
    except OSError:
        return ""
    for name in names:
        if name.endswith("_force_zero_off.out"):
            cands.append(os.path.join(ens_dir, name))
    if not cands:
        return ""
    cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return cands[0]


def same_gnom_path(a: str, b: str) -> bool:
    if not a or not b:
        return False
    try:
        return os.path.abspath(a) == os.path.abspath(b)
    except OSError:
        return False
