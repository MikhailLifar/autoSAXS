"""GUI-facing package update helpers (autosaxs CLI + liveview relaunch defaults)."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from autosaxs.cli.deferred_pip_upgrade import (  # noqa: F401
    deferred_upgrade_log_path,
    launch_deferred_pip_upgrade as _launch_deferred_pip_upgrade,
)
from autosaxs.cli.package_update import (  # noqa: F401
    AUTOSAXS_UPDATE_SPEC,
    environment_summary,
    installed_package_location,
    installed_package_version,
    is_editable_install,
    pip_upgrade_argv,
    run_pip_upgrade,
)

from .app_relaunch import (  # noqa: F401
    guisaxs_liveview_restart_argv,
    launch_guisaxs_liveview,
    spawn_detached,
)

# Backward-compatible alias used by the liveview update dialog text.
LIVEVIEW_UPDATE_SPEC = AUTOSAXS_UPDATE_SPEC


def launch_deferred_pip_upgrade(
    *,
    parent_pid: int,
    force: bool = False,
    restart_argv: Optional[List[str]] = None,
) -> Path:
    """Spawn the deferred pip updater; default restart argv is liveview relaunch."""
    if restart_argv is None:
        restart_argv = guisaxs_liveview_restart_argv()
    return _launch_deferred_pip_upgrade(
        parent_pid=parent_pid,
        force=force,
        restart_argv=restart_argv,
    )
