# autosaxs: interactive SAXS pipeline core (controller, processor, skill, utils, guinier, event_bus, cli, viewer, context, gui, gui_interface, api, polydispfit, report)
__version__ = "2.7.0"
import re
import subprocess
import warnings

_RECOMMENDED_ATSAS_VERSION = "3.2.1"
_ATSAS_DOWNLOAD_URL = "https://www.embl-hamburg.de/biosaxs/download.html"


def _check_atsas_installed():
    """Verify ATSAS is installed; warn on version mismatch (see dammif -v)."""
    try:
        result = subprocess.run(
            ["dammif", "-v"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        out = (result.stdout or "") + (result.stderr or "")
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        raise RuntimeError(
            "Apparently ATSAS package, on which autosaxs module relies, is not installed. "
            f"Install ATSAS here: {_ATSAS_DOWNLOAD_URL}"
        )
    match = re.search(r"ATSAS\s+(\d+\.\d+\.\d+)", out)
    if not match:
        warnings.warn(
            "ATSAS appears to be installed (dammif found), but its version could not be parsed from "
            "`dammif -v` output. Some autosaxs functions may not work as expected.",
            RuntimeWarning,
            stacklevel=2,
        )
        print("ATSAS installed - autosaxs is ready for use!")
        return

    installed_version = match.group(1)
    if installed_version != _RECOMMENDED_ATSAS_VERSION:
        warnings.warn(
            f"ATSAS version mismatch: autosaxs was developed/tested with ATSAS "
            f"{_RECOMMENDED_ATSAS_VERSION}, but detected ATSAS {installed_version}. "
            "Some autosaxs functions may not work due to the mismatch.",
            RuntimeWarning,
            stacklevel=2,
        )

    print(f"ATSAS {installed_version} installed - autosaxs is ready for use!")


_check_atsas_installed()

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
]
