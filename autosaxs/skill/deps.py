"""
Internal dependency surface for `autosaxs.skill.*`.

The goal is to keep individual skill modules lightweight and consistent by importing
shared runtime utilities from *within* the `autosaxs.skill` package.
"""

from __future__ import annotations

# Eventing / progress
from ..core.event_bus import EventBus, EventType  # noqa: F401

# Wrappers / caching
from .skill_wrap import (  # noqa: F401
    CACHE_FILENAME,
    apply_batch,
    check_output_integrity,
    compute_input_hash,
    read_cache,
    run_with_cache,
    write_cache,
    _strip_sub_int_prefix,
)

# Core algorithms / processing
from .calibrate.autocalib import autocalib_ring_analysis  # noqa: F401
from .fit_guinier.guinier import run_guinier_analysis  # noqa: F401
from ..core.processor import (  # noqa: F401
    IntegratorExtended,
    integrate_2d_to_1d,
    subtract_buffer,
)

# IO + misc helpers
from ..core.utils import (  # noqa: F401
    calc_chi2,
    compute_dammif_descriptors,
    ensure_q_nm,
    load_config,
    load_saxs_1d_any,
    read_bodies_cif,
    read_from_tiff,
    read_saxs,
    write_data,
    write_saxs,
    write_saxs_atsas_format,
)

# Plotting helpers
from ..core.viewer import PLTViewer  # noqa: F401

