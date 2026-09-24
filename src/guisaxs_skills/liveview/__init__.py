"""Liveview package — lazy exports to avoid heavy imports on submodule access."""

from __future__ import annotations

from typing import Any

__all__ = [
    "Job",
    "JobStep",
    "LiveviewJobExecutor",
    "LiveviewQueueStatus",
    "LiveviewSessionState",
    "LiveviewWatchMode",
    "Sample",
    "SampleStore",
    "load_liveview_session_settings",
    "save_liveview_session_settings",
]

_EXPORTS = {
    "Job": ("guisaxs_skills.liveview.pipeline", "Job"),
    "JobStep": ("guisaxs_skills.liveview.pipeline", "JobStep"),
    "LiveviewJobExecutor": ("guisaxs_skills.liveview.pipeline", "LiveviewJobExecutor"),
    "LiveviewQueueStatus": ("guisaxs_skills.liveview.pipeline", "LiveviewQueueStatus"),
    "LiveviewSessionState": ("guisaxs_skills.liveview.session", "LiveviewSessionState"),
    "LiveviewWatchMode": ("guisaxs_skills.liveview.session", "LiveviewWatchMode"),
    "Sample": ("guisaxs_skills.liveview.session", "Sample"),
    "SampleStore": ("guisaxs_skills.liveview.session", "SampleStore"),
    "load_liveview_session_settings": (
        "guisaxs_skills.liveview.session",
        "load_liveview_session_settings",
    ),
    "save_liveview_session_settings": (
        "guisaxs_skills.liveview.session",
        "save_liveview_session_settings",
    ),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    mod_name, attr = target
    import importlib

    mod = importlib.import_module(mod_name)
    return getattr(mod, attr)
