"""Spawn a detached ephemeral script to run pip after the GUI process exits."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List

from .package_update import pip_upgrade_argv

# Self-contained updater source written to a temp file on each launch.
# Intentionally does not import autosaxs so it keeps working mid-upgrade.
_UPDATER_SCRIPT = r'''#!/usr/bin/env python3
"""Ephemeral autosaxs updater (generated at runtime)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        if not ok:
            return False
        return exit_code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _wait_for_pid(pid: int, *, timeout_s: float = 180.0, poll_s: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(poll_s)
    return not _pid_alive(pid)


def _append_log(log_path: Path, text: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")
        fh.flush()


def _can_show_gui() -> bool:
    if os.environ.get("AUTOSAXS_DEFERRED_UPDATE_HEADLESS") == "1":
        return False
    if sys.platform == "win32":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _run_pip_headless(*, pip_argv: list[str], log_path: Path, restart_argv: list[str]) -> int:
    _append_log(log_path, "Running pip upgrade...")
    _append_log(log_path, "$ " + " ".join(pip_argv))
    try:
        with log_path.open("a", encoding="utf-8") as log_fh:
            result = subprocess.run(pip_argv, stdout=log_fh, stderr=subprocess.STDOUT, check=False)
    except FileNotFoundError:
        _append_log(log_path, "Error: could not run pip (interpreter or pip missing).")
        return 1
    code = int(result.returncode)
    if code == 0:
        _append_log(log_path, "Update finished successfully.")
        _try_launch_restart(restart_argv, log_path=log_path)
    else:
        _append_log(log_path, f"Update failed (pip exit code {code}).")
    return code


def _launch_detached(argv: list[str]) -> int | None:
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(argv, **kwargs)
    return proc.pid


def _try_launch_restart(restart_argv: list[str], *, log_path: Path) -> int | None:
    if os.environ.get("AUTOSAXS_DEFERRED_UPDATE_HEADLESS") == "1":
        return None
    if not restart_argv:
        _append_log(log_path, "No restart command configured.")
        return None
    try:
        _append_log(log_path, "Launching " + " ".join(restart_argv) + " ...")
        restart_pid = _launch_detached(restart_argv)
        _append_log(log_path, "guisaxs-liveview started.")
        return restart_pid
    except OSError as exc:
        _append_log(log_path, f"Could not start guisaxs-liveview: {exc}")
        return None


def _schedule_close_after_restart(restart_pid: int, dlg) -> None:
    from PyQt5.QtCore import QTimer

    started = time.monotonic()

    def poll() -> None:
        elapsed = time.monotonic() - started
        if _pid_alive(restart_pid) and elapsed >= 0.75:
            dlg.accept()
            return
        if elapsed >= 4.0:
            dlg.accept()
            return
        if elapsed >= 60.0:
            return
        QTimer.singleShot(200, poll)

    QTimer.singleShot(300, poll)


def _show_update_dialog(
    *,
    log_path: Path,
    pip_argv: list[str] | None,
    restart_argv: list[str],
    error_message: str = "",
) -> int:
    if not _can_show_gui():
        if error_message:
            _append_log(log_path, error_message)
            return 1
        if pip_argv is None:
            return 1
        return _run_pip_headless(pip_argv=pip_argv, log_path=log_path, restart_argv=restart_argv)

    from PyQt5.QtCore import QProcess, Qt
    from PyQt5.QtGui import QTextCursor
    from PyQt5.QtWidgets import (
        QApplication,
        QDialog,
        QDialogButtonBox,
        QLabel,
        QPlainTextEdit,
        QVBoxLayout,
    )

    app = QApplication.instance() or QApplication(sys.argv)

    dlg = QDialog()
    dlg.setWindowTitle("autosaxs update")
    dlg.setMinimumSize(520, 360)

    status = QLabel("Updating autosaxs…")
    status.setWordWrap(True)
    status.setAlignment(Qt.AlignCenter)
    status.setStyleSheet("font-size: 14px; font-weight: 600; padding: 6px;")

    output = QPlainTextEdit()
    output.setReadOnly(True)
    output.setPlaceholderText("pip output will appear here…")

    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    ok_btn = buttons.button(QDialogButtonBox.Ok)
    if ok_btn is not None:
        ok_btn.setEnabled(False)
        ok_btn.setText("OK")
    buttons.accepted.connect(dlg.accept)

    layout = QVBoxLayout(dlg)
    layout.addWidget(status)
    layout.addWidget(output, 1)
    layout.addWidget(buttons)

    exit_code = 1

    def append_output(text: str) -> None:
        if not text:
            return
        output.moveCursor(QTextCursor.End)
        output.insertPlainText(text)
        if not text.endswith("\n"):
            output.insertPlainText("\n")
        _append_log(log_path, text.rstrip("\n"))

    def finish(success: bool, detail: str = "") -> None:
        nonlocal exit_code
        exit_code = 0 if success else 1
        if success:
            status.setText("Updated successfully")
            status.setStyleSheet(
                "font-size: 14px; font-weight: 600; padding: 6px; color: #1b7f3a;"
            )
            if detail:
                append_output(detail)
            restart_pid = _try_launch_restart(restart_argv, log_path=log_path)
            if restart_pid is not None:
                append_output("guisaxs-liveview is starting.")
                _schedule_close_after_restart(restart_pid, dlg)
            else:
                append_output("Start guisaxs-liveview manually to use the new version.")
        else:
            status.setText("Update failed")
            status.setStyleSheet(
                "font-size: 14px; font-weight: 600; padding: 6px; color: #b00020;"
            )
            if detail:
                append_output(detail)
        if ok_btn is not None:
            ok_btn.setEnabled(True)
            ok_btn.setFocus()

    if error_message:
        append_output(error_message)
        finish(False, error_message)
        dlg.exec_()
        return exit_code

    append_output("$ " + " ".join(pip_argv or []))

    proc = QProcess(dlg)
    proc.setProcessChannelMode(QProcess.MergedChannels)

    def on_ready_read() -> None:
        data = proc.readAllStandardOutput()
        if data:
            append_output(bytes(data).decode("utf-8", errors="replace"))

    def on_finished(code: int, _status) -> None:
        if int(code) == 0:
            finish(True)
        else:
            finish(False, f"pip exited with code {code}.")

    proc.readyReadStandardOutput.connect(on_ready_read)
    proc.finished.connect(on_finished)
    proc.start((pip_argv or [""])[0], (pip_argv or [""])[1:])

    dlg.exec_()
    if proc.state() != QProcess.NotRunning:
        proc.kill()
        proc.waitForFinished(3000)
    return exit_code


def _schedule_self_delete(script_path: Path) -> None:
    try:
        if sys.platform == "win32":
            quoted = str(script_path).replace('"', '""')
            subprocess.Popen(
                ["cmd", "/c", f'ping -n 3 127.0.0.1 >nul & del /f /q "{quoted}"'],
                close_fds=True,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            os.remove(script_path)
    except OSError:
        pass


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: updater.py CONFIG.json", file=sys.stderr)
        return 2

    config_path = Path(argv[1])
    config = json.loads(config_path.read_text(encoding="utf-8"))
    wait_pid = int(config["wait_pid"])
    pip_argv: list[str] = list(config["pip_argv"])
    restart_argv: list[str] = list(config.get("restart_argv") or [])
    log_path = Path(config["log"])
    config_path.unlink(missing_ok=True)

    _append_log(log_path, f"autosaxs deferred update started at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    _append_log(log_path, f"Waiting for process {wait_pid} to exit...")

    if not _wait_for_pid(wait_pid):
        msg = "Timed out waiting for the application to exit."
        _append_log(log_path, msg)
        return _show_update_dialog(
            log_path=log_path,
            pip_argv=None,
            restart_argv=restart_argv,
            error_message=msg,
        )

    if sys.platform == "win32":
        time.sleep(2.0)
    else:
        time.sleep(0.5)

    return _show_update_dialog(
        log_path=log_path,
        pip_argv=pip_argv,
        restart_argv=restart_argv,
    )


if __name__ == "__main__":
    script_path = Path(__file__).resolve()
    try:
        raise SystemExit(main(sys.argv))
    finally:
        _schedule_self_delete(script_path)
'''


def guisaxs_liveview_restart_argv() -> List[str]:
    """Return argv to start guisaxs-liveview from the current environment."""
    scripts = Path(sys.executable).resolve().parent
    if sys.platform == "win32":
        exe = scripts / "guisaxs-liveview.exe"
        if exe.is_file():
            return [str(exe)]
    script = scripts / "guisaxs-liveview"
    if script.is_file():
        return [str(script)]
    return [sys.executable, "-m", "guisaxs_liveview"]


def deferred_upgrade_log_path() -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return Path(tempfile.gettempdir()) / f"autosaxs-update-{stamp}-{os.getpid()}.log"


def _write_updater_files(
    *,
    parent_pid: int,
    pip_argv: List[str],
    log_path: Path,
    restart_argv: List[str],
) -> tuple[Path, Path]:
    tmp = Path(tempfile.gettempdir())
    stamp = f"{int(time.time())}-{os.getpid()}"
    script_path = tmp / f"autosaxs-update-runner-{stamp}.py"
    config_path = tmp / f"autosaxs-update-config-{stamp}.json"

    script_path.write_text(_UPDATER_SCRIPT, encoding="utf-8")
    config = {
        "wait_pid": int(parent_pid),
        "pip_argv": pip_argv,
        "restart_argv": restart_argv,
        "log": str(log_path),
    }
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return script_path, config_path


def _spawn_detached(argv: List[str]) -> None:
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        CREATE_NO_WINDOW = 0x08000000
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True

    subprocess.Popen(argv, **kwargs)


def launch_deferred_pip_upgrade(
    *,
    parent_pid: int,
    force: bool = False,
    restart_argv: List[str] | None = None,
) -> Path:
    """Write a temp updater script, spawn it detached, return the log file path."""
    log_path = deferred_upgrade_log_path()
    pip_argv = pip_upgrade_argv(force=force)
    if restart_argv is None:
        restart_argv = guisaxs_liveview_restart_argv()
    script_path, config_path = _write_updater_files(
        parent_pid=parent_pid,
        pip_argv=pip_argv,
        log_path=log_path,
        restart_argv=restart_argv,
    )
    _spawn_detached([sys.executable, str(script_path), str(config_path)])
    return log_path
