from __future__ import annotations

from pathlib import Path


def default_output_dir_for_skill(*, workdir: Path, skill_name: str) -> Path:
    """
    Default output subdir naming follows autosaxs `saxs_controller.py` conventions where applicable.
    """
    mapping = {
        # calibrate is special: by default it writes directly into workdir (no subdir)
        "calibrate": "",
        "integrate": "averaged",
        "integrate_proxy": "averaged",
        "subtract": "subtracted",
        "fit_guinier": "descriptors",
        "analyze_kratky": "analyze_kratky",
        "fit_porod": "fit_porod",
        "analyze_loglog": "analyze_loglog",
        "model_mixture": "mixture",
        "model_bodies": "bodies",
        "model_dam": "dammif",
        "model_density": "denss",
        "fit_distances": "fit_distances",
    }
    sub = mapping.get(skill_name, skill_name)
    return workdir if not sub else (workdir / sub)

