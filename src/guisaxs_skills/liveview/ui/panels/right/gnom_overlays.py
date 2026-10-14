"""Shared GNOM overlay path helpers for P(r) / D(R) plots."""

from __future__ import annotations

import os


def same_gnom_path(a: str, b: str) -> bool:
    if not a or not b:
        return False
    try:
        return os.path.abspath(a) == os.path.abspath(b)
    except OSError:
        return False
