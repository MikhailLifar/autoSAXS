from __future__ import annotations

import copy
from typing import Any, Optional

from .....logic.skill_catalog import discover_skills


def strip_fit_distances_profile_from_saved_form(
    saved: Optional[dict[str, Any]], meta_fit: Any
) -> Optional[dict[str, Any]]:
    """Do not persist profile path in the liveview session snapshot (options only)."""
    if saved is None or meta_fit is None:
        return saved
    out = copy.deepcopy(saved)
    pos = list(out.get("positional") or [])
    for i, p in enumerate(meta_fit.positional_params):
        if p.name != "profile" or i >= len(pos):
            continue
        prev = pos[i] if isinstance(pos[i], dict) else {}
        pos[i] = {
            "text": "",
            "dropped_paths": [],
            "mode": prev.get("mode", "any"),
        }
    out["positional"] = pos
    return out


def discover_fit_skill_meta() -> tuple[Any, Any, Any]:
    skills = {m.name: m for m in discover_skills()}
    return (
        skills.get("fit_distances"),
        skills.get("fit_sizes"),
        skills.get("fit_mixture"),
    )
