from __future__ import annotations

from pathlib import Path


def default_output_dir_for_skill(*, workdir: Path, skill_name: str) -> Path:
    """
    Default output subdir naming follows autosaxs `saxs_controller.py` conventions where applicable.
    """
    mapping = {
        "calibrate": "calibration",
        "integrate": "averaged",
        "integrate_proxy": "averaged",
        "subtract": "subtracted",
        "plot": "plots",
        "guinier_analysis": "descriptors",
        "fit_mixture": "mixture",
        "fit_bodies": "bodies",
        "fit_dammif": "dammif",
    }
    sub = mapping.get(skill_name, skill_name)
    return workdir / sub

