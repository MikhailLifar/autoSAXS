"""JSON-lines IPC between liveview (parent) and modeling child processes."""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, Optional

from PyQt5.QtCore import QObject, QProcess, pyqtSignal

from ..context import ModelingContext


def encode_message(msg_type: str, **payload: Any) -> bytes:
    body = {"type": str(msg_type), **payload}
    return (json.dumps(body, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def decode_line(line: str) -> Optional[Dict[str, Any]]:
    s = (line or "").strip()
    if not s:
        return None
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


class IpcLineBuffer:
    """Accumulate stdout bytes into newline-delimited JSON messages."""

    def __init__(self) -> None:
        self._buf = ""

    def feed(self, data: str) -> list[Dict[str, Any]]:
        self._buf += data
        out: list[Dict[str, Any]] = []
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            msg = decode_line(line)
            if msg is not None:
                out.append(msg)
        return out


class ModelingIpcChild(QObject):
    """Child-side: read parent stdin lines; write messages to a dedicated out stream."""

    context_received = pyqtSignal(object)  # ModelingContext
    focus_requested = pyqtSignal()
    shutdown_requested = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._buf = IpcLineBuffer()
        self.enabled = False
        self._out = None  # binary writable; set by app bootstrap under --ipc

    def send(self, msg_type: str, **payload: Any) -> None:
        if not self.enabled:
            return
        out = self._out
        if out is None:
            import sys

            out = sys.stdout.buffer
        out.write(encode_message(msg_type, **payload))
        out.flush()

    def send_ready(self) -> None:
        self.send("ready")

    def send_confirmed(self, *, mode: str, options: Dict[str, Any], paths: Dict[str, str]) -> None:
        self.send("confirmed", mode=mode, options=options, paths=paths)

    def send_finished(self, *, success: bool, result: Dict[str, Any]) -> None:
        self.send("finished", success=bool(success), result=result)

    def send_failed(self, *, message: str) -> None:
        self.send("failed", message=str(message))

    def send_busy(self, busy: bool) -> None:
        self.send("busy", busy=bool(busy))

    def send_ready_for_context(self) -> None:
        """Ask parent to re-push ModelingContext after a deferred push during a busy job."""
        self.send("ready_for_context")

    def feed_stdin_chunk(self, data: str) -> None:
        for msg in self._buf.feed(data):
            self._dispatch(msg)

    def _dispatch(self, msg: Dict[str, Any]) -> None:
        t = str(msg.get("type") or "")
        if t == "context":
            self.context_received.emit(ModelingContext.from_dict(msg.get("context")))
        elif t == "focus":
            self.focus_requested.emit()
        elif t == "shutdown":
            self.shutdown_requested.emit()


class ModelingChildHandle(QObject):
    """Parent-side handle for a supervised modeling QProcess."""

    ready = pyqtSignal()
    confirmed = pyqtSignal(dict)
    finished_run = pyqtSignal(dict)
    failed = pyqtSignal(str)
    busy_changed = pyqtSignal(bool)
    ready_for_context = pyqtSignal()
    process_exited = pyqtSignal(int)

    def __init__(self, proc: QProcess, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._proc = proc
        self._buf = IpcLineBuffer()
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        self._stderr_cb: Optional[Callable[[str], None]] = None

    def set_stderr_callback(self, cb: Optional[Callable[[str], None]]) -> None:
        self._stderr_cb = cb

    def is_running(self) -> bool:
        return self._proc.state() != QProcess.NotRunning

    def send_context(self, ctx: ModelingContext) -> None:
        self._write(encode_message("context", context=ctx.to_dict()))

    def send_focus(self) -> None:
        self._write(encode_message("focus"))

    def send_shutdown(self) -> None:
        self._write(encode_message("shutdown"))

    def terminate(self) -> None:
        if self.is_running():
            self._proc.terminate()

    def kill(self) -> None:
        if self.is_running():
            self._proc.kill()

    def wait_for_finished(self, timeout_ms: int = 3000) -> bool:
        """Block until the child exits (or timeout). Safe to call from closeEvent."""
        if not self.is_running():
            return True
        return bool(self._proc.waitForFinished(int(timeout_ms)))

    def shutdown(self, *, grace_ms: int = 2500, term_ms: int = 1500) -> None:
        """Ask child to quit via IPC, then terminate/kill if still running."""
        if not self.is_running():
            return
        try:
            self.send_shutdown()
        except Exception:
            pass
        if self.wait_for_finished(grace_ms):
            return
        try:
            self.terminate()
        except Exception:
            pass
        if self.wait_for_finished(term_ms):
            return
        try:
            self.kill()
        except Exception:
            pass
        self.wait_for_finished(500)

    def _write(self, data: bytes) -> None:
        if not self.is_running():
            return
        self._proc.write(data)

    def _on_stdout(self) -> None:
        raw = bytes(self._proc.readAllStandardOutput()).decode(errors="replace")
        for msg in self._buf.feed(raw):
            t = str(msg.get("type") or "")
            if t == "ready":
                self.ready.emit()
            elif t == "confirmed":
                self.confirmed.emit(msg)
            elif t == "finished":
                self.finished_run.emit(msg)
            elif t == "failed":
                self.failed.emit(str(msg.get("message") or "failed"))
            elif t == "busy":
                self.busy_changed.emit(bool(msg.get("busy")))
            elif t == "ready_for_context":
                self.ready_for_context.emit()

    def _on_stderr(self) -> None:
        raw = bytes(self._proc.readAllStandardError()).decode(errors="replace")
        if self._stderr_cb and raw:
            self._stderr_cb(raw)

    def _on_finished(self, code: int, _status: QProcess.ExitStatus) -> None:  # type: ignore[override]
        self.process_exited.emit(int(code))
