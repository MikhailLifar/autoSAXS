from __future__ import annotations

import os
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
    request: Optional[RunRequest] = None


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
        self._last_request: Optional[RunRequest] = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.NotRunning

    def start(self, request: RunRequest) -> None:
        if self.is_running():
            return
        self._skill_name = request.skill_name
        self._last_request = request
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
        proc.setWorkingDirectory(str(self._workdir.resolve()))
        proc.setProgram(sys.executable)
        # autosaxs.cli is a package (no __main__.py), so `-m autosaxs.cli` fails.
        # Run the actual CLI module that has `if __name__ == "__main__": ...`.
        proc.setArguments(["-m", "autosaxs.cli.cli", *request.cli_argv()])
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

    def wait_until_idle(self, timeout_ms: int = 8000) -> bool:
        """After ``cancel()``, wait for the subprocess to finish so workdir can be changed safely."""
        if not self._proc:
            return True
        if self._proc.state() == QProcess.NotRunning:
            self._proc = None
            return True
        return bool(self._proc.waitForFinished(int(timeout_ms)))

    def set_workdir(self, workdir: Path) -> None:
        if self.is_running():
            raise RuntimeError("SkillRunner workdir cannot change while a skill is running")
        self._workdir = workdir.expanduser().resolve()

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
        # Enrich artifacts for certain skills (GUI-side only; does not affect computation).
        if self._skill_name == "fit_distances":
            bp = result.get("best_summary_path")
            if isinstance(bp, str) and bp.strip() and os.path.isfile(bp):
                try:
                    data = yaml.safe_load(Path(bp).read_text(encoding="utf-8", errors="replace"))
                    if isinstance(data, dict):
                        for key in ("fit_vs_exp_png_path", "best_pr_png_path"):
                            cur = result.get(key)
                            cur_s = cur.strip().lower() if isinstance(cur, str) else ""
                            if cur_s and cur_s != "none":
                                continue
                            v = data.get(key)
                            if isinstance(v, str) and v.strip():
                                result[key] = v
                except Exception:
                    pass
        if self._skill_name == "calibrate":
            plots_dir = result.get("calibration_plots_dir")
            if isinstance(plots_dir, str) and plots_dir:
                try:
                    p = Path(plots_dir)
                    if p.exists() and p.is_dir():
                        pngs = sorted(str(x) for x in p.rglob("*.png") if x.is_file())
                        if pngs:
                            # Include all generated PNGs (ring analysis plots, curve, mask, etc.)
                            result.setdefault("calibration_pngs", pngs)
                except Exception:
                    pass

        latest_result_path(self._workdir).write_text(yaml.safe_dump(result, sort_keys=True), encoding="utf-8")
        outcome = RunOutcome(
            success=(status == QProcess.NormalExit and exit_code == 0),
            exit_code=exit_code,
            result=result,
            request=self._last_request,
        )
        self.finished.emit(outcome)

