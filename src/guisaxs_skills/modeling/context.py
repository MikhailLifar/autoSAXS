"""Path/pref bundle handed from liveview (or CLI) into modeling apps — not execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional


@dataclass
class ModelingContext:
    """Semantic paths + engine prefs for PathField hints / Confirm."""

    profile_path: str = ""
    gnom_path: str = ""
    output_dir: str = ""
    # Shape mini-app: bodies | dammif | denss (default dammif when empty/unknown).
    # DR mini-app: mixture. Liveview slim panes may still use session "none" (= do not launch).
    mode: str = "dammif"
    options: Dict[str, Any] = field(default_factory=dict)
    # When True, DAMMIF Confirm must use gnom_path (no profile-only fallback).
    require_gnom_for_dam: bool = False
    sample_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ModelingContext":
        if not isinstance(data, dict):
            return cls()
        opts = data.get("options")
        if not isinstance(opts, dict):
            opts = {}
        return cls(
            profile_path=str(data.get("profile_path") or ""),
            gnom_path=str(data.get("gnom_path") or ""),
            output_dir=str(data.get("output_dir") or ""),
            mode=str(data.get("mode") or "").strip().lower(),
            options=dict(opts),
            require_gnom_for_dam=bool(data.get("require_gnom_for_dam", False)),
            sample_id=str(data.get("sample_id") or ""),
        )
