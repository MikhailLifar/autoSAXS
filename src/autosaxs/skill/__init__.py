"""
autosaxs.skill

Skill-oriented API surface and discovery helpers.

This is a package (not a single file) so that each skill can live in a dedicated module.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, Dict, List

# Re-export wrappers and cache helpers from skill_wrap directly (not via deps) so
# ``import autosaxs.skill`` does not touch the deps lazy hub or heavy core modules.
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

_SKILL_IMPORTS: Dict[str, str] = {
    "calibrate": "autosaxs.skill.calibrate",
    "integrate": "autosaxs.skill.integrate",
    "average": "autosaxs.skill.average",
    "integrate_proxy": "autosaxs.skill.integrate_proxy",
    "subtract": "autosaxs.skill.subtract",
    "plot": "autosaxs.skill.plot",
    "plot_2d": "autosaxs.skill.plot_2d",
    "fit_guinier": "autosaxs.skill.fit_guinier",
    "analyze_kratky": "autosaxs.skill.analyze_kratky",
    "fit_distances": "autosaxs.skill.fit_distances",
    "fit_sizes": "autosaxs.skill.fit_sizes",
    "model_dr_mc": "autosaxs.skill.model_dr_mc",
    "calc_profile": "autosaxs.skill.calc_profile",
    "model_lc": "autosaxs.skill.model_lc",
    "model_mixture": "autosaxs.skill.model_mixture",
    "model_bodies": "autosaxs.skill.model_bodies",
    "model_dam": "autosaxs.skill.model_dam",
    "model_density": "autosaxs.skill.model_density",
    "process_monodisperse": "autosaxs.skill.process_monodisperse",
    "process_polydisperse": "autosaxs.skill.process_polydisperse",
    "process_full": "autosaxs.skill.process_full",
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
    "analyze_kratky",
    "fit_distances",
    "fit_sizes",
    "model_dr_mc",
    "calc_profile",
    "model_lc",
    "model_mixture",
    "model_bodies",
    "model_dam",
    "model_density",
    "process_monodisperse",
    "process_polydisperse",
    "process_full",
    "report_individual",
    "report_summary",
]


def _is_report_skill(name: str) -> bool:
    return name.startswith("report_") or name in ("report_individual", "report_summary")


def _make_lazy_skill_entrypoint(name: str, mod_path: str) -> Callable[..., Any]:
    """
    Create a thin callable wrapper that imports the real skill function on demand.

    This avoids a Python import pitfall:
    - `autosaxs.skill` is a package that also contains submodules/packages named
      `calibrate`, `integrate`, ...
    - Without this, `from autosaxs.skill import calibrate` resolves the *submodule*
      (a module object) instead of the callable skill function.
    """

    def _entrypoint(*args: Any, **kwargs: Any) -> Any:
        return get_skill(name)(*args, **kwargs)

    _entrypoint.__name__ = name
    _entrypoint.__qualname__ = name
    _entrypoint.__doc__ = f"Lazy-loaded entry point for `{name}` from `{mod_path}`."
    return _entrypoint


# Pre-populate public names so `from autosaxs.skill import X` resolves to a callable
# even when `X` also exists as a submodule package.
for _name, _mod_path in _SKILL_IMPORTS.items():
    globals()[_name] = _make_lazy_skill_entrypoint(_name, _mod_path)


def get_skill(name: str) -> Callable[..., Any]:
    """Import one skill module and return its public callable (eager for that name only)."""
    mod_path = _SKILL_IMPORTS.get(name)
    if mod_path is None:
        raise KeyError(name)
    mod = importlib.import_module(mod_path)
    return getattr(mod, name)


def list_skill_names(*, include_reports: bool = True) -> List[str]:
    """Ordered public skill names without importing skill implementation modules."""
    names: List[str] = []
    seen: set[str] = set()
    for name in SKILL_ORDER:
        if name not in _SKILL_IMPORTS:
            continue
        if not include_reports and _is_report_skill(name):
            continue
        names.append(name)
        seen.add(name)
    for name in _SKILL_IMPORTS:
        if name in seen:
            continue
        if not include_reports and _is_report_skill(name):
            continue
        names.append(name)
    return names


def list_skills(*, include_reports: bool = True) -> Dict[str, Callable[..., Any]]:
    """
    Return a mapping of public skill name -> callable entry point.

    Entry points are lazy: this does **not** import skill implementation modules.
    Use ``get_skill(name)`` when a real function object (signature/doc) is required.
    This function is the single source of truth for automatic skill discovery
    in CLI and GUI.
    """
    skills: Dict[str, Callable[..., Any]] = {}
    for name in list_skill_names(include_reports=include_reports):
        # Prefer the package-level lazy entrypoint when present.
        skills[name] = globals().get(name) or _make_lazy_skill_entrypoint(
            name, _SKILL_IMPORTS[name]
        )
    return skills


def __getattr__(name: str) -> Any:
    """
    Lazy-export public skill functions.
    """
    mod_path = _SKILL_IMPORTS.get(name)
    if mod_path is None:
        raise AttributeError(name)
    return get_skill(name)
