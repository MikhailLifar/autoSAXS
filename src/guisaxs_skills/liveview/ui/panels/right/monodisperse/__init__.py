"""Monodisperse UI — lazy export so plots can load without wizard chrome."""

from __future__ import annotations

from typing import Any

__all__ = ["MonodisperseWizardWidget"]


def __getattr__(name: str) -> Any:
    if name != "MonodisperseWizardWidget":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(
        importlib.import_module("guisaxs_skills.liveview.ui.panels.right.monodisperse.wizard"),
        name,
    )
