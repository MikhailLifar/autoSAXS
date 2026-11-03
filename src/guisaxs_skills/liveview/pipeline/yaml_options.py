"""Shared YAML option loader for liveview plan / manual job builders."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml


def load_yaml_options(path: Optional[Path]) -> dict:
    """Load a skill options YAML file; empty dict on missing/invalid input."""
    if path is None:
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict):
            return {str(k): v for k, v in data.items()}
    except Exception:
        return {}
    return {}
