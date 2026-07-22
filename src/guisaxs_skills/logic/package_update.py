"""Re-export autosaxs CLI update helpers for GUI entry points."""

from autosaxs.cli.deferred_pip_upgrade import (  # noqa: F401
    deferred_upgrade_log_path,
    guisaxs_liveview_restart_argv,
    launch_deferred_pip_upgrade,
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

# Backward-compatible alias used by the liveview update dialog text.
LIVEVIEW_UPDATE_SPEC = AUTOSAXS_UPDATE_SPEC
