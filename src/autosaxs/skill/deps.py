"""
Internal dependency surface for `autosaxs.skill.*`.

Keep this hub thin: eager imports are only the light event bus, skill wrappers,
and skill-keyed config. Heavier symbols (integrator, guinier, gnom, utils I/O,
viewer) resolve via ``__getattr__`` so a leaf skill that imports ``run_with_cache``
does not transitively load matplotlib/pyFAI stacks it never uses.
"""

from __future__ import annotations

import importlib
from typing import Any

# Eventing / progress (light)
from ..core.event_bus import EventBus, EventType  # noqa: F401

# Wrappers / caching (no ATSAS probe, no plotting)
from .skill_wrap import (  # noqa: F401
    CACHE_FILENAME,
    apply_batch,
    check_output_integrity,
    compute_input_hash,
    ensure_atsas_installed,
    read_cache,
    require_atsas,
    run_with_cache,
    write_cache,
    _strip_sub_int_prefix,
)

# Skill-keyed config
from .config import (  # noqa: F401
    load_config_file,
    load_default_config,
    merge_skill_params,
    skill_section,
)

# Absolute module paths for lazy attrs (avoid dragging these on `import deps`).
_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "IntegratorExtended": ("autosaxs.core.integrator", "IntegratorExtended"),
    "run_guinier_analysis": ("autosaxs.skill.fit_guinier.guinier", "run_guinier_analysis"),
    "parse_gnom_out": ("autosaxs.core.gnom", "parse_gnom_out"),
    "PLTViewer": ("autosaxs.core.viewer", "PLTViewer"),
    "calc_chi2": ("autosaxs.core.utils", "calc_chi2"),
    "compute_dammif_descriptors": ("autosaxs.core.utils", "compute_dammif_descriptors"),
    "ensure_q_nm": ("autosaxs.core.utils", "ensure_q_nm"),
    "load_config": ("autosaxs.core.utils", "load_config"),
    "load_saxs_1d_any": ("autosaxs.core.utils", "load_saxs_1d_any"),
    "read_bodies_cif": ("autosaxs.core.utils", "read_bodies_cif"),
    "read_from_tiff": ("autosaxs.core.utils", "read_from_tiff"),
    "read_saxs": ("autosaxs.core.utils", "read_saxs"),
    "write_data": ("autosaxs.core.utils", "write_data"),
    "write_saxs": ("autosaxs.core.utils", "write_saxs"),
    "write_saxs_atsas_format": ("autosaxs.core.utils", "write_saxs_atsas_format"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr = target
    value = getattr(importlib.import_module(mod_name), attr)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_ATTRS))
