# autosaxs: SAXS processing skills, core I/O, and CLI entry points
from __future__ import annotations

import importlib
import logging
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from typing import Any

try:
    __version__ = _pkg_version("autosaxs")
except PackageNotFoundError:
    # Editable/source tree without an installed distribution metadata entry.
    __version__ = "0.0.0+local"

# pyFAI logs API deprecations (set_wavelength, AzimuthalIntegrator, …) via this
# logger with full stack traces; keep calling the same APIs but don't spam users.
logging.getLogger("pyFAI.DEPRECATION").setLevel(logging.ERROR)

# Heavy / optional re-exports stay lazy: ``import autosaxs`` must not probe ATSAS
# (``dammif``), import skill implementations, or pull pyFAI/matplotlib stacks.
__all__ = [
    "EventBus",
    "EventType",
    "Context",
    "integrator",
    "processor",
    "utils",
    "viewer",
    "skill",
    "__version__",
]

_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "EventBus": ("autosaxs.core.event_bus", "EventBus"),
    "EventType": ("autosaxs.core.event_bus", "EventType"),
    "Context": ("autosaxs.core.context", "Context"),
    "integrator": ("autosaxs.core", "integrator"),
    "processor": ("autosaxs.core", "processor"),
    "utils": ("autosaxs.core", "utils"),
    "viewer": ("autosaxs.core", "viewer"),
    "skill": ("autosaxs", "skill"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr = target
    if name == "skill":
        mod = importlib.import_module("autosaxs.skill")
        globals()[name] = mod
        return mod
    mod = importlib.import_module(mod_name)
    value = getattr(mod, attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
