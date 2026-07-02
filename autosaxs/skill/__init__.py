"""
autosaxs.skill

Skill-oriented API surface and discovery helpers.

This is a package (not a single file) so that each skill can live in a dedicated module.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Dict

# Re-export wrappers and cache helpers (tests and callers import these from autosaxs.skill)
from .deps import (  # noqa: F401
    CACHE_FILENAME,
    apply_batch,
    check_output_integrity,
    compute_input_hash,
    read_cache,
    run_with_cache,
    write_cache,
    _strip_sub_int_prefix,
)

_SKILL_IMPORTS: Dict[str, str] = {
    "calibrate": "autosaxs.skill.calibrate",
    "integrate": "autosaxs.skill.integrate",
    "average": "autosaxs.skill.average",
    "integrate_proxy": "autosaxs.skill.integrate_proxy",
    "subtract": "autosaxs.skill.subtract",
    "plot": "autosaxs.skill.plot",
    "plot_2d": "autosaxs.skill.plot_2d",
    "fit_guinier": "autosaxs.skill.fit_guinier",
    "fit_distances": "autosaxs.skill.fit_distances",
    "fit_sizes": "autosaxs.skill.fit_sizes",
    "fit_mixture": "autosaxs.skill.fit_mixture",
    "fit_bodies": "autosaxs.skill.fit_bodies",
    "fit_dammif": "autosaxs.skill.fit_dammif",
    "report_individual": "autosaxs.skill.report_individual",
    "report_summary": "autosaxs.skill.report_summary",
}

SKILL_ORDER = [
    "calibrate",
    "integrate",
    "average",
    "integrate_proxy",
    "subtract",
    "plot",
    "plot_2d",
    "fit_guinier",
    "fit_distances",
    "fit_sizes",
    "fit_mixture",
    "fit_bodies",
    "fit_dammif",
    "report_individual",
    "report_summary",
]


def list_skills(*, include_reports: bool = True) -> Dict[str, Callable[..., Any]]:
    """
    Return a mapping of public skill name -> callable entry point.

    This function is the single source of truth for automatic skill discovery
    in CLI and GUI.
    """
    skills: Dict[str, Callable[..., Any]] = {}
    for name, mod_path in _SKILL_IMPORTS.items():
        if not include_reports and (name.startswith("report_") or name in ("report_individual", "report_summary")):
            continue
        mod = importlib.import_module(mod_path)
        skills[name] = getattr(mod, name)
    if not include_reports:
        return skills
    return skills


def __getattr__(name: str) -> Any:
    """
    Lazy-export public skill functions without shadowing submodules.

    This avoids the common Python package pitfall where `autosaxs.skill.plot_2d` could
    refer to either the submodule or the function, which breaks monkeypatching and
    submodule imports.
    """
    mod_path = _SKILL_IMPORTS.get(name)
    if mod_path is None:
        raise AttributeError(name)
    mod = importlib.import_module(mod_path)
    return getattr(mod, name)

