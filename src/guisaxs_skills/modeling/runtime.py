"""Dedicated SkillRunner wrapper for MODELING_SKILLS only (Confirm path)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from ..core.models import RunRequest
from ..logic.runner_qprocess import RunOutcome, SkillRunner
from .skills import MODELING_SKILLS


class ModelingRuntime(QObject):
    """Single-flight runner for model_* skills; used only by modeling mini-apps."""

    started = pyqtSignal(str)
    stdout = pyqtSignal(str)
    stderr = pyqtSignal(str)
    finished = pyqtSignal(object)  # RunOutcome
    cancelled = pyqtSignal()

    def __init__(self, *, workdir: Path, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._runner = SkillRunner(workdir=workdir)
        self._runner.started.connect(self.started.emit)
        self._runner.stdout.connect(self.stdout.emit)
        self._runner.stderr.connect(self.stderr.emit)
        self._runner.finished.connect(self.finished.emit)
        self._runner.cancelled.connect(self.cancelled.emit)

    def is_running(self) -> bool:
        return self._runner.is_running()

    def set_workdir(self, workdir: Path) -> None:
        self._runner.set_workdir(workdir)

    def start(
        self,
        skill_name: str,
        positional: list[str],
        options: Optional[Dict[str, Any]] = None,
    ) -> None:
        name = str(skill_name).strip()
        if name not in MODELING_SKILLS:
            raise ValueError(f"ModelingRuntime refuses non-modeling skill: {name!r}")
        if self.is_running():
            return
        req = RunRequest(name, list(positional), dict(options or {}))
        self._runner.start(req)

    def cancel(self) -> None:
        self._runner.cancel()
