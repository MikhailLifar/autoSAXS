"""Panel package — lazy exports so plot widgets can import without left/right chrome."""

from __future__ import annotations

from typing import Any

__all__ = ["LiveviewLeftPanel", "LiveviewMiddlePanel", "LiveviewRightPanel"]

_EXPORTS = {
    "LiveviewLeftPanel": ("guisaxs_skills.liveview.ui.panels.left", "LiveviewLeftPanel"),
    "LiveviewMiddlePanel": ("guisaxs_skills.liveview.ui.panels.middle", "LiveviewMiddlePanel"),
    "LiveviewRightPanel": ("guisaxs_skills.liveview.ui.panels.right.panel", "LiveviewRightPanel"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    mod = importlib.import_module(target[0])
    return getattr(mod, target[1])
