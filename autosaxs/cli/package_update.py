from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import List, Tuple

# Upgrade target (git main until PyPI publish).
AUTOSAXS_UPDATE_SPEC = (
    "autosaxs[gui] @ git+http://hpc.nano.sfedu.ru:8080/mikhail/saxsprocessing.git@main"
)


def pip_upgrade_argv(*, force: bool = False) -> List[str]:
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade"]
    if force:
        cmd.append("--force-reinstall")
    cmd.append(AUTOSAXS_UPDATE_SPEC)
    return cmd


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


def run_pip_upgrade(*, force: bool = False) -> int:
    """Upgrade autosaxs[gui] in the current environment; stream pip output."""
    if is_editable_install():
        print(
            "Warning: autosaxs appears to be installed in editable mode. "
            "pip upgrade may not replace your working copy.",
            file=sys.stderr,
        )
    cmd = pip_upgrade_argv(force=force)
    print("$ " + " ".join(cmd), flush=True)
    try:
        result = subprocess.run(cmd)
    except FileNotFoundError:
        print("Error: could not run pip for the current Python interpreter.", file=sys.stderr)
        return 1
    if result.returncode == 0:
        ver = installed_package_version()
        print(f"\nUpdate finished successfully (autosaxs {ver}).", flush=True)
        print("Restart shells and GUI apps to use the new version.", flush=True)
    else:
        print(f"\nUpdate failed (exit code {result.returncode}).", file=sys.stderr, flush=True)
    return int(result.returncode)
