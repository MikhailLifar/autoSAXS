"""guisaxs-shape entry package."""

from __future__ import annotations

__all__ = ["main"]


def main() -> int:
    from guisaxs_skills.modeling.shape.app import run_shape_app

    return run_shape_app()
