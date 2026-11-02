"""P2 acceptance: light ``import autosaxs`` and lazy skill catalog."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_import_autosaxs_does_not_spawn_dammif() -> None:
    """Fresh interpreter: ``import autosaxs`` must not run ``dammif``."""
    script = r"""
import subprocess
import sys

_orig = subprocess.run
spawned = []

def _run(*a, **k):
    cmd = a[0] if a else k.get("args")
    if isinstance(cmd, (list, tuple)) and cmd and str(cmd[0]) == "dammif":
        spawned.append(list(cmd))
    elif isinstance(cmd, str) and cmd.strip().startswith("dammif"):
        spawned.append(cmd)
    return _orig(*a, **k)

subprocess.run = _run
import autosaxs  # noqa: F401
print("version", autosaxs.__version__)
print("dammif_calls", len(spawned))
sys.exit(0 if not spawned else 2)
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "dammif_calls 0" in proc.stdout


def test_list_skills_does_not_import_skill_bodies() -> None:
    """``list_skills()`` returns lazy entry points without importing implementations."""
    script = r"""
import sys

import autosaxs.skill as skill_mod

before = {m for m in sys.modules if m.startswith("autosaxs.skill.")}
skills = skill_mod.list_skills(include_reports=True)
assert "calibrate" in skills
assert "model_dam" in skills
after = {m for m in sys.modules if m.startswith("autosaxs.skill.")}
# Package + skill_wrap/config may already be present; no leaf skill packages.
leaf_markers = (
    "autosaxs.skill.calibrate",
    "autosaxs.skill.model_dam",
    "autosaxs.skill.fit_distances",
    "autosaxs.skill.subtract",
    "autosaxs.skill.plot",
)
loaded = [m for m in after - before if any(m == mark or m.startswith(mark + ".") for mark in leaf_markers)]
# Also reject if leaves were already loaded somehow via list_skills path.
already = [m for m in after if any(m == mark or m.startswith(mark + ".") for mark in leaf_markers)]
print("loaded_new", loaded)
print("already", already)
sys.exit(0 if not already else 3)
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_console_cold_start_loads_only_selected_skill() -> None:
    """Skills console catalog lists names; only the default skill body is imported."""
    script = r"""
import sys
from pathlib import Path

# Headless Qt.
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication

from guisaxs_skills.core.event_bus import EventBus
from guisaxs_skills.ui.main_window import MainWindow

app = QApplication.instance() or QApplication([])
before = set(sys.modules)
win = MainWindow(bus=EventBus(), workdir=Path.cwd())
after = set(sys.modules)
new = after - before
skill_leaves = sorted(
    m for m in new
    if m.startswith("autosaxs.skill.")
    and m.count(".") >= 3
    and not m.startswith("autosaxs.skill.fit_guinier")  # calibrate may pull guinier helpers? unlikely
)
# Must have loaded calibrate (default selection); must not have loaded all skills.
assert any(m == "autosaxs.skill.calibrate" or m.startswith("autosaxs.skill.calibrate.") for m in new), sorted(
    m for m in new if m.startswith("autosaxs.skill.")
)
forbidden = (
    "autosaxs.skill.model_dam",
    "autosaxs.skill.model_mixture",
    "autosaxs.skill.fit_distances",
    "autosaxs.skill.fit_sizes",
    "autosaxs.skill.model_density",
    "autosaxs.skill.process_full",
    "autosaxs.skill.report_individual",
)
bad = [m for m in forbidden if m in sys.modules or any(x.startswith(m + ".") for x in sys.modules)]
print("forbidden_loaded", bad)
win.close()
sys.exit(0 if not bad else 4)
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(Path.cwd()),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_get_skill_loads_one_module() -> None:
    from autosaxs.skill import get_skill, list_skills

    assert "integrate" in list_skills()
    fn = get_skill("integrate")
    assert callable(fn)
    assert fn.__name__ == "integrate"
