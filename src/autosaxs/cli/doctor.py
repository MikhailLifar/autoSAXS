"""``autosaxs doctor`` — post-install health check for beginners and CI."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from autosaxs.skill.skill_wrap import (
    ATSAS_DOWNLOAD_URL,
    RECOMMENDED_ATSAS_VERSION,
    probe_atsas,
)

# Skills decorated with @require_atsas (must have ATSAS on PATH).
SKILLS_NEED_ATSAS: Tuple[str, ...] = (
    "fit_distances",
    "fit_sizes",
    "model_bodies",
    "model_dam",
    "model_mixture",
    "process_monodisperse",
)

# Beamline / analysis skills that run without ATSAS.
SKILLS_WITHOUT_ATSAS: Tuple[str, ...] = (
    "calibrate",
    "integrate",
    "average",
    "integrate_proxy",
    "subtract",
    "plot",
    "plot_2d",
    "fit_guinier",
    "analyze_kratky",
    "model_dr_mc",
    "model_density",
    "report_individual",
    "report_summary",
)

EXAMPLE_EXPECTED_TIFS: Tuple[str, ...] = (
    "AgBh700_96.9_calib.tif",
    "ihs27_buffer.tif",
    "ihs27_sample.tif",
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "error" | "info"
    detail: str
    core: bool = False  # if True and status=="error", exit code is 1


def _status_tag(status: str) -> str:
    return {
        "ok": "[OK]",
        "warn": "[!!]",
        "error": "[XX]",
        "info": "[--]",
    }.get(status, "[??]")


def _try_import(module_name: str) -> Tuple[bool, str]:
    try:
        mod = importlib.import_module(module_name)
        ver = getattr(mod, "__version__", None)
        if ver is None:
            return True, "imported"
        return True, f"imported ({ver})"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _find_example_dir() -> Optional[Path]:
    """Locate examples/monodisperse_protein when running from a source checkout."""
    here = Path(__file__).resolve()
    # src/autosaxs/cli/doctor.py → repo root is parents[3]
    candidates = [
        here.parents[3] / "examples" / "monodisperse_protein",
        Path.cwd() / "examples" / "monodisperse_protein",
    ]
    for cand in candidates:
        if cand.is_dir():
            return cand
    return None


def run_checks() -> List[CheckResult]:
    results: List[CheckResult] = []

    # Python version
    py_ok = sys.version_info >= (3, 10)
    results.append(
        CheckResult(
            name="Python >= 3.10",
            status="ok" if py_ok else "error",
            detail=f"{sys.version.split()[0]} ({sys.executable})",
            core=True,
        )
    )

    # autosaxs
    ok, detail = _try_import("autosaxs")
    if ok:
        try:
            import autosaxs as _asx

            detail = f"version {getattr(_asx, '__version__', 'unknown')}"
        except Exception:
            pass
    results.append(
        CheckResult(
            name="autosaxs",
            status="ok" if ok else "error",
            detail=detail,
            core=True,
        )
    )

    # Core scientific deps
    for mod_name, label in (
        ("pyFAI", "pyFAI"),
        ("fabio", "fabio"),
        ("numpy", "numpy"),
        ("scipy", "scipy"),
        ("matplotlib", "matplotlib"),
    ):
        ok, detail = _try_import(mod_name)
        results.append(
            CheckResult(
                name=label,
                status="ok" if ok else "error",
                detail=detail,
                core=True,
            )
        )

    # Optional PyPI analysis backends
    for mod_name, label, hint in (
        ("denss", "DENSS", "needed for model_density"),
        ("mcsas3", "McSAS3", "needed for model_dr_mc"),
    ):
        ok, detail = _try_import(mod_name)
        results.append(
            CheckResult(
                name=label,
                status="ok" if ok else "warn",
                detail=detail if ok else f"{detail} ({hint})",
                core=False,
            )
        )

    # GUI extras — prefer same env as this Python (sys.executable), not only PATH.
    pyqt_ok, pyqt_detail = _try_import("PyQt5")
    bindir = Path(sys.executable).resolve().parent
    liveview_candidates = [
        bindir / "guisaxs-liveview",
        bindir / "guisaxs-liveview.exe",
    ]
    skills_candidates = [
        bindir / "guisaxs-skills",
        bindir / "guisaxs-skills.exe",
    ]
    liveview_ep = next((str(p) for p in liveview_candidates if p.is_file()), None)
    if liveview_ep is None:
        liveview_ep = shutil.which("guisaxs-liveview")
    skills_ep = next((str(p) for p in skills_candidates if p.is_file()), None)
    if skills_ep is None:
        skills_ep = shutil.which("guisaxs-skills")
    if pyqt_ok and liveview_ep:
        gui_status = "ok"
        gui_detail = f"PyQt5 OK; guisaxs-liveview at {liveview_ep}"
        if skills_ep:
            gui_detail += f"; guisaxs-skills at {skills_ep}"
    elif pyqt_ok:
        gui_status = "warn"
        gui_detail = (
            f"PyQt5 OK but guisaxs-liveview not found next to this Python or on PATH. "
            "Reinstall with: python -m pip install \"autosaxs[gui]\""
        )
    else:
        gui_status = "warn"
        gui_detail = (
            f"{pyqt_detail}. Desktop GUIs need: python -m pip install \"autosaxs[gui]\""
        )
    results.append(
        CheckResult(
            name="Desktop GUI (PyQt5)",
            status=gui_status,
            detail=gui_detail,
            core=False,
        )
    )

    # ATSAS (optional, proprietary)
    version, err = probe_atsas()
    if err:
        results.append(
            CheckResult(
                name="ATSAS (optional)",
                status="warn",
                detail=(
                    "Not found on PATH. Calibration, integration, subtraction, and the live GUI "
                    f"still work. For p(r) / DAMMIF / BODIES / MIXTURE, install ATSAS from "
                    f"{ATSAS_DOWNLOAD_URL} (recommended {RECOMMENDED_ATSAS_VERSION})."
                ),
                core=False,
            )
        )
    elif version is None:
        results.append(
            CheckResult(
                name="ATSAS (optional)",
                status="warn",
                detail="dammif found but version could not be parsed from `dammif -v`.",
                core=False,
            )
        )
    elif version != RECOMMENDED_ATSAS_VERSION:
        results.append(
            CheckResult(
                name="ATSAS (optional)",
                status="warn",
                detail=(
                    f"Detected {version}; recommended {RECOMMENDED_ATSAS_VERSION}. "
                    "Some skills may behave differently."
                ),
                core=False,
            )
        )
    else:
        results.append(
            CheckResult(
                name="ATSAS (optional)",
                status="ok",
                detail=f"version {version}",
                core=False,
            )
        )

    # Writable cwd
    cwd = Path.cwd()
    writable = os.access(cwd, os.W_OK)
    if writable:
        try:
            with tempfile.NamedTemporaryFile(dir=str(cwd), prefix=".autosaxs_doctor_", delete=True):
                pass
        except OSError as exc:
            writable = False
            results.append(
                CheckResult(
                    name="Writable working directory",
                    status="warn",
                    detail=f"{cwd} not writable for temp files: {exc}",
                    core=False,
                )
            )
    if writable:
        results.append(
            CheckResult(
                name="Writable working directory",
                status="ok",
                detail=str(cwd),
                core=False,
            )
        )
    elif not any(r.name == "Writable working directory" for r in results):
        results.append(
            CheckResult(
                name="Writable working directory",
                status="warn",
                detail=f"{cwd} is not writable (guisaxs-liveview needs a writable folder)",
                core=False,
            )
        )

    # Bundled example (source checkout only)
    ex = _find_example_dir()
    if ex is None:
        results.append(
            CheckResult(
                name="Example data",
                status="info",
                detail=(
                    "examples/monodisperse_protein/ not found here "
                    "(normal for a pip-only install; clone the repo for the quickstart)."
                ),
                core=False,
            )
        )
    else:
        missing = [name for name in EXAMPLE_EXPECTED_TIFS if not (ex / name).is_file()]
        if missing:
            results.append(
                CheckResult(
                    name="Example data",
                    status="warn",
                    detail=f"{ex}: missing {', '.join(missing)}",
                    core=False,
                )
            )
        else:
            results.append(
                CheckResult(
                    name="Example data",
                    status="ok",
                    detail=str(ex),
                    core=False,
                )
            )

    return results


def _desktop_liveview_shortcut() -> Optional[Path]:
    """Return path to GUISAXS-LiveView Desktop shortcut if present."""
    home = Path.home()
    candidates: List[Path] = []
    xdg = os.environ.get("XDG_DESKTOP_DIR")
    if xdg:
        candidates.append(Path(xdg) / "GUISAXS-LiveView.desktop")
        candidates.append(Path(xdg) / "GUISAXS-LiveView.lnk")
    candidates.extend(
        [
            home / "Desktop" / "GUISAXS-LiveView.desktop",
            home / "Desktop" / "GUISAXS-LiveView.lnk",
        ]
    )
    for p in candidates:
        if p.is_file():
            return p
    return None


def _format_report(results: Sequence[CheckResult]) -> str:
    lines: List[str] = ["autosaxs doctor", "=" * 16, ""]
    width = max(len(r.name) for r in results)
    for r in results:
        tag = _status_tag(r.status)
        lines.append(f"{tag}  {r.name.ljust(width)}  {r.detail}")

    core_ok = all(r.status != "error" for r in results if r.core)
    gui_ok = any(r.name.startswith("Desktop GUI") and r.status == "ok" for r in results)
    atsas_ok = any(r.name.startswith("ATSAS") and r.status == "ok" for r in results)
    example_ok = any(r.name == "Example data" and r.status == "ok" for r in results)
    shortcut = _desktop_liveview_shortcut()

    lines.extend(["", "Next steps", "-" * 10])
    if not core_ok:
        lines.append(
            "Core checks failed. Reinstall with the double-click installer from INSTALL.md "
            "(or: python -m pip install \"autosaxs[gui]\")."
        )
        return "\n".join(lines) + "\n"

    lines.append("Core install looks good.")
    if shortcut is not None:
        lines.append(f"  • Open GUISAXS-LiveView from the Desktop ({shortcut.name}).")
    elif gui_ok:
        lines.append("  • Launch the live GUI:  guisaxs-liveview")
        lines.append("    (or re-run the installer and enable Create Desktop shortcut).")
        lines.append("    Run it from a writable folder that will hold your data.")
    else:
        lines.append(
            "  • For the desktop GUI: use the installer ZIP in INSTALL.md "
            "or: python -m pip install \"autosaxs[gui]\""
        )

    if example_ok:
        lines.append(
            "  • Optional quickstart:  cd examples/monodisperse_protein"
            "  (see README § Quick start)"
        )

    if not atsas_ok:
        lines.append("")
        lines.append("ATSAS is optional and proprietary (not bundled with autosaxs).")
        lines.append(
            "Without it you can still: "
            + ", ".join(SKILLS_WITHOUT_ATSAS)
            + "."
        )
        lines.append(
            "ATSAS is required for: "
            + ", ".join(SKILLS_NEED_ATSAS)
            + "."
        )
        lines.append(f"Install ATSAS from: {ATSAS_DOWNLOAD_URL}")
        lines.append("Then re-run:  autosaxs doctor")

    return "\n".join(lines) + "\n"


def doctor() -> int:
    """
    Run environment checks and print a human-readable report.

    Returns 0 if core (Python + autosaxs + pyFAI + fabio/numpy/scipy/matplotlib) is usable.
    Returns 1 if any core check failed. ATSAS / GUI gaps never fail the exit code.
    """
    results = run_checks()
    sys.stdout.write(_format_report(results))
    if any(r.core and r.status == "error" for r in results):
        return 1
    return 0
