"""Liveview widgets — lazy exports."""

from __future__ import annotations

from typing import Any

__all__ = [
    "DatCurveViewerDialog",
    "Image2DViewerDialog",
    "LiveviewViewer3D",
    "open_compare_curves_dialog",
    "open_dat_curve_dialog",
    "open_image_2d_dialog",
]

_EXPORTS = {
    "DatCurveViewerDialog": (".plots", "DatCurveViewerDialog"),
    "Image2DViewerDialog": (".plots", "Image2DViewerDialog"),
    "open_compare_curves_dialog": (".plots", "open_compare_curves_dialog"),
    "open_dat_curve_dialog": (".plots", "open_dat_curve_dialog"),
    "open_image_2d_dialog": (".plots", "open_image_2d_dialog"),
    "LiveviewViewer3D": (".viewer_3d", "LiveviewViewer3D"),
}


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    mod = importlib.import_module(target[0], __name__)
    return getattr(mod, target[1])
