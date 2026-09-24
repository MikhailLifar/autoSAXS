"""Spawn / focus supervised modeling child processes from liveview."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence

from PyQt5.QtCore import QObject, QProcess

from .context import ModelingContext
from .ipc import ModelingChildHandle


def _console_script_argv(script: str) -> List[str]:
    scripts = Path(sys.executable).resolve().parent
    if sys.platform == "win32":
        exe = scripts / f"{script}.exe"
        if exe.is_file():
            return [str(exe)]
    path = scripts / script
    if path.is_file():
        return [str(path)]
    mod = "guisaxs_shape" if script == "guisaxs-shape" else "guisaxs_dr"
    return [sys.executable, "-m", mod]


def _write_context_file(context: ModelingContext) -> str:
    """Persist full context (incl. options) so the child loads it before IPC."""
    fd, name = tempfile.mkstemp(prefix="guisaxs_modeling_ctx_", suffix=".json")
    path = Path(name)
    try:
        with open(fd, "w", encoding="utf-8") as fp:
            json.dump(context.to_dict(), fp, ensure_ascii=False)
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        return ""
    return str(path)


def start_modeling_child(
    *,
    app: str,
    context: ModelingContext,
    parent: Optional[QObject] = None,
    cwd: Optional[Path] = None,
    extra_argv: Optional[Sequence[str]] = None,
) -> ModelingChildHandle:
    """
    Start ``guisaxs-shape`` or ``guisaxs-dr`` with ``--ipc`` and optional context argv.

    ``app`` is ``\"shape\"`` or ``\"dr\"``.

    ``parent`` must outlive the child (typically ``ModelingChildManager``), not the
    liveview main window — otherwise closing liveview destroys the QProcess while
    guisaxs-shape is still running (segfault / \"Destroyed while process is still running\").
    """
    script = "guisaxs-shape" if app == "shape" else "guisaxs-dr"
    argv = _console_script_argv(script)
    argv = list(argv) + ["--ipc"]
    ctx_file = _write_context_file(context)
    if ctx_file:
        argv.extend(["--context-file", ctx_file])
    if context.profile_path:
        argv.extend(["--profile", context.profile_path])
    if context.gnom_path:
        argv.extend(["--gnom", context.gnom_path])
    if context.output_dir:
        argv.extend(["--output-dir", context.output_dir])
    if context.mode and context.mode != "none":
        argv.extend(["--mode", context.mode])
    if context.require_gnom_for_dam:
        argv.append("--require-gnom-for-dam")
    if extra_argv:
        argv.extend(list(extra_argv))

    proc = QProcess(parent)
    if cwd is not None:
        proc.setWorkingDirectory(str(Path(cwd).expanduser().resolve()))
    proc.setProgram(argv[0])
    proc.setArguments(argv[1:])
    proc.setProcessChannelMode(QProcess.SeparateChannels)
    handle = ModelingChildHandle(proc, parent=parent)

    def _on_ready() -> None:
        handle.send_context(context)

    def _cleanup_ctx_file(_code: int) -> None:
        if not ctx_file:
            return
        try:
            Path(ctx_file).unlink(missing_ok=True)
        except Exception:
            pass

    handle.ready.connect(_on_ready)
    handle.process_exited.connect(_cleanup_ctx_file)
    proc.start()
    return handle
