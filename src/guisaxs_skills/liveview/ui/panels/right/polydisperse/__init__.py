"""Polydisperse UI — lazy exports."""

from __future__ import annotations

from typing import Any

__all__ = ["PolydisperseCoordinator", "PolydisperseWindowWidget"]

_EXPORTS = {
    "PolydisperseCoordinator": (".coordinator", "PolydisperseCoordinator"),
    "PolydisperseWindowWidget": (".window_widget", "PolydisperseWindowWidget"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    mod = importlib.import_module(target[0], __name__)
    return getattr(mod, target[1])
