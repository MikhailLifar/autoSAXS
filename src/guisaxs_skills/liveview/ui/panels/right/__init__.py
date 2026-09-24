"""Right panel package — lazy export of LiveviewRightPanel."""

from __future__ import annotations

from typing import Any

__all__ = ["LiveviewRightPanel"]


def __getattr__(name: str) -> Any:
    if name != "LiveviewRightPanel":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module("guisaxs_skills.liveview.ui.panels.right.panel"), name)
