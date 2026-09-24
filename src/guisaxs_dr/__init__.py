"""guisaxs-dr entry package."""

from __future__ import annotations

__all__ = ["main"]


def main() -> int:
    from guisaxs_skills.modeling.dr.app import run_dr_app

    return run_dr_app()
