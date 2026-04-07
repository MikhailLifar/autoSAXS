# autosaxs: interactive SAXS pipeline core (controller, processor, skill, utils, guinier, event_bus, cli, viewer, context, gui, gui_interface, api, polydispfit, report)
__version__ = "1.0.7"
import re
import subprocess

_REQUIRED_ATSAS_VERSION = "3.2.1"
_ATSAS_DOWNLOAD_URL = "https://www.embl-hamburg.de/biosaxs/download.html"


def _check_atsas_installed():
    """Verify ATSAS is installed and version is 3.2.1 (see dammif -v)."""
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
            f"Apparently ATSAS {_REQUIRED_ATSAS_VERSION} package, on which autosaxs module relies, "
            f"is not installed. Install ATSAS {_REQUIRED_ATSAS_VERSION} here: {_ATSAS_DOWNLOAD_URL}"
        )
    match = re.search(r"ATSAS\s+(\d+\.\d+\.\d+)", out)
    if not match or match.group(1) != _REQUIRED_ATSAS_VERSION:
        raise RuntimeError(
            f"Apparently ATSAS {_REQUIRED_ATSAS_VERSION} package, on which autosaxs module relies, "
            f"is not installed. Install ATSAS {_REQUIRED_ATSAS_VERSION} here: {_ATSAS_DOWNLOAD_URL}"
        )
    print(f"ATSAS {_REQUIRED_ATSAS_VERSION}. installed - autosaxs is ready for use!")


_check_atsas_installed()

from .event_bus import EventBus, EventType
from .context import Context
from . import cli_interface
from . import processor
from . import utils
from . import guinier
from . import viewer
from . import api
from . import polydispfit
from . import report
from . import skill
from .saxs_controller import Controller

__all__ = [
    "EventBus",
    "EventType",
    "Context",
    "Controller",
    "cli_interface",
    "processor",
    "utils",
    "guinier",
    "viewer",
    "api",
    "polydispfit",
    "report",
    "skill",
]
