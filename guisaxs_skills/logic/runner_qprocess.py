from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml
from PyQt5.QtCore import QObject, QProcess, pyqtSignal

from ..core.models import RunRequest, flatten_artifacts
from ..core.paths import (
    latest_request_path,
    latest_result_path,
    latest_run_dir,
    latest_stderr_path,
    latest_stdout_path,
)
from .result_parser import parse_key_value_stdout


@dataclass(frozen=True)
class RunOutcome:
    success: bool
    exit_code: int
    result: dict


class SkillRunner(QObject):
    started = pyqtSignal(str)  # skill_name
    stdout = pyqtSignal(str)
    stderr = pyqtSignal(str)
    finished = pyqtSignal(object)  # RunOutcome
    cancelled = pyqtSignal()

    def __init__(self, *, workdir: Path) -> None:
        super().__init__()
        self._workdir = workdir
        self._proc: Optional[QProcess] = None
        self._stdout_buf = ""
        self._stderr_buf = ""
        self._skill_name: Optional[str] = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.NotRunning

    def start(self, request: RunRequest) -> None:
        if self.is_running():
            return
        self._skill_name = request.skill_name
        self._stdout_buf = ""
        self._stderr_buf = ""

        latest_run_dir(self._workdir).mkdir(parents=True, exist_ok=True)
        latest_request_path(self._workdir).write_text(
            yaml.safe_dump(
                {
                    "skill": request.skill_name,
                    "positional": request.positional,
                    "options": request.options,
                    "started_at": datetime.utcnow().isoformat() + "Z",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        latest_stdout_path(self._workdir).write_text("", encoding="utf-8")
        latest_stderr_path(self._workdir).write_text("", encoding="utf-8")

        proc = QProcess(self)
        proc.setProgram(sys.executable)
        proc.setArguments(["-m", "autosaxs.cli", *request.cli_argv()])
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        self._proc = proc
        self.started.emit(request.skill_name)
        proc.start()

    def cancel(self) -> None:
        if not self._proc:
            return
        if self._proc.state() == QProcess.NotRunning:
            self._proc = None
            return
        self._proc.terminate()

    def _on_stdout(self) -> None:
        if not self._proc:
            return
        data = bytes(self._proc.readAllStandardOutput()).decode(errors="replace")
        self._stdout_buf += data
        latest_stdout_path(self._workdir).write_text(self._stdout_buf, encoding="utf-8")
        self.stdout.emit(data)

    def _on_stderr(self) -> None:
        if not self._proc:
            return
        data = bytes(self._proc.readAllStandardError()).decode(errors="replace")
        self._stderr_buf += data
        latest_stderr_path(self._workdir).write_text(self._stderr_buf, encoding="utf-8")
        self.stderr.emit(data)

    def _on_finished(self, exit_code: int, status: QProcess.ExitStatus) -> None:  # type: ignore[override]
        proc = self._proc
        self._proc = None
        if proc is None:
            return

        result = parse_key_value_stdout(self._stdout_buf)
        latest_result_path(self._workdir).write_text(yaml.safe_dump(result, sort_keys=True), encoding="utf-8")
        outcome = RunOutcome(success=(status == QProcess.NormalExit and exit_code == 0), exit_code=exit_code, result=result)
        self.finished.emit(outcome)

