# autosaxs: interactive SAXS pipeline core (controller, processor, skill, utils, guinier, event_bus, cli, viewer, context, gui, gui_interface, api, polydispfit, report)
from __future__ import annotations

import logging
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("autosaxs")
except PackageNotFoundError:
    # Editable/source tree without an installed distribution metadata entry.
    __version__ = "0.0.0+local"

# pyFAI logs API deprecations (set_wavelength, AzimuthalIntegrator, …) via this
# logger with full stack traces; keep calling the same APIs but don't spam users.
logging.getLogger("pyFAI.DEPRECATION").setLevel(logging.ERROR)

from .skill.skill_wrap import warn_atsas_on_import

warn_atsas_on_import()

from .core.event_bus import EventBus, EventType
from .core.context import Context
from .pipeline import cli_interface
from .core import integrator
from .core import processor
from .core import utils
from .core import viewer
from .pipeline import api
from . import skill
from .pipeline.saxs_controller import Controller

__all__ = [
    "EventBus",
    "EventType",
    "Context",
    "Controller",
    "cli_interface",
    "integrator",
    "processor",
    "utils",
    "viewer",
    "api",
    "skill",
    "__version__",
]
