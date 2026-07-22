"""Interactive pipeline orchestration (CLI/GUI interfaces, controller, viewer).

Deprecated: prefer ``autosaxs.skill`` and the ``autosaxs`` / ``guisaxs-*`` entry points.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "autosaxs.pipeline is deprecated and will be removed in a future release; "
    "use autosaxs.skill and the autosaxs / guisaxs-skills / guisaxs-liveview entry points instead.",
    DeprecationWarning,
    stacklevel=2,
)
