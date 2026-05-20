"""Skill-keyed config: bundled defaults, optional user file, merge with entry-point kwargs."""

from __future__ import annotations

import copy
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from ..core.utils import load_config

BUNDLED_CONFIG_FILENAME = "config_base.conf"


def default_config_path() -> Path:
    """Resolve bundled ``config_base.conf`` (installed package or source tree)."""
    try:
        from importlib.resources import files

        return Path(str(files("autosaxs.resources") / BUNDLED_CONFIG_FILENAME))
    except Exception:
        return Path(__file__).resolve().parents[1] / "resources" / BUNDLED_CONFIG_FILENAME


@lru_cache(maxsize=1)
def load_default_config() -> Dict[str, Any]:
    path = default_config_path()
    if not path.is_file():
        raise FileNotFoundError(f"Bundled autosaxs config not found: {path!r}")
    return load_config(str(path))


def load_config_file(path: str) -> Dict[str, Any]:
    return load_config(path)


def resolve_optional_config_path(config_path: Any) -> Optional[str]:
    """
    Normalize optional ``config_path`` from CLI, Python API, or GUI state.

    Returns ``None`` when unset, empty, or a PathField state dict with no text.
    """
    if config_path is None:
        return None
    if isinstance(config_path, dict):
        text = str(config_path.get("text") or "").strip()
        if not text:
            return None
        config_path = text
    elif isinstance(config_path, str):
        text = config_path.strip()
        if not text:
            return None
        config_path = text
    from .common import coerce_config_path_expression

    expr = coerce_config_path_expression(config_path)
    path = str(expr.unwrap()[0])
    if path and not os.path.isfile(path):
        raise FileNotFoundError(f"config_path not found: {path!r}")
    return path


def skill_section(full_cfg: Optional[Dict[str, Any]], skill_name: str) -> Dict[str, Any]:
    """Return the parameter dict for ``skill_name``; missing or empty section → ``{}``."""
    if not full_cfg:
        return {}
    section = full_cfg.get(skill_name)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise TypeError(f"Config section {skill_name!r} must be a mapping, got {type(section).__name__}")
    return copy.deepcopy(section)


def _deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if (
            key in out
            and isinstance(out[key], dict)
            and isinstance(value, dict)
        ):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def merge_skill_params(
    skill_name: str,
    *,
    config_path: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """
    Merge bundled defaults, user config file section, and explicit kwargs.

    Precedence: non-``None`` kwargs > user file section > bundled section.
    """
    merged = skill_section(load_default_config(), skill_name)
    if config_path:
        user_cfg = load_config_file(config_path)
        merged = _deep_merge(merged, skill_section(user_cfg, skill_name))
    for key, value in kwargs.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        merged[key] = value
    return merged
