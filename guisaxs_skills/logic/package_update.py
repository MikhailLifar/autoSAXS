from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

# Upgrade target for guisaxs-liveview / autosaxs[gui] (git main until PyPI publish).
LIVEVIEW_UPDATE_SPEC = (
    "autosaxs[gui] @ git+http://hpc.nano.sfedu.ru:8080/mikhail/saxsprocessing.git@main"
)


def pip_upgrade_argv() -> List[str]:
    return [sys.executable, "-m", "pip", "install", "--upgrade", LIVEVIEW_UPDATE_SPEC]


def installed_package_version(dist_name: str = "autosaxs") -> str:
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version(dist_name)
    except PackageNotFoundError:
        return "unknown"
    except Exception:
        return "unknown"


def installed_package_location(dist_name: str = "autosaxs") -> str:
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pip", "show", dist_name],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        for line in (out.stdout or "").splitlines():
            if line.startswith("Location:"):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return "unknown"


def is_editable_install(dist_name: str = "autosaxs") -> bool:
    try:
        out = subprocess.run(
            [sys.executable, "-m", "pip", "show", "-f", dist_name],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        text = (out.stdout or "") + "\n" + (out.stderr or "")
        if "Editable project location:" in text:
            return True
        for line in text.splitlines():
            if line.endswith(".egg-link"):
                return True
    except Exception:
        pass
    try:
        loc = installed_package_location(dist_name)
        if loc != "unknown":
            p = Path(loc)
            if (p.parent / f"{dist_name}.egg-link").is_file():
                return True
            if (p / f"{dist_name}.egg-link").is_file():
                return True
    except Exception:
        pass
    return False


def environment_summary() -> Tuple[str, str, str]:
    return (
        installed_package_version(),
        sys.executable,
        installed_package_location(),
    )
