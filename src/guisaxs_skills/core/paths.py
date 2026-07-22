from __future__ import annotations

from pathlib import Path


def runs_dir(workdir: Path) -> Path:
    return workdir / "runs"


def latest_run_dir(workdir: Path) -> Path:
    return runs_dir(workdir) / "latest"


def inputs_dir(workdir: Path) -> Path:
    return workdir / "inputs"


def latest_request_path(workdir: Path) -> Path:
    return latest_run_dir(workdir) / "request.yml"


def latest_result_path(workdir: Path) -> Path:
    return latest_run_dir(workdir) / "result.yml"


def latest_stdout_path(workdir: Path) -> Path:
    return latest_run_dir(workdir) / "stdout.log"


def latest_stderr_path(workdir: Path) -> Path:
    return latest_run_dir(workdir) / "stderr.log"

