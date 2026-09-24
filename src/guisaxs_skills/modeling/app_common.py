"""Shared argparse + QApplication bootstrap for modeling mini-apps."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Optional

from PyQt5.QtCore import QSocketNotifier
from PyQt5.QtWidgets import QApplication, QMainWindow

from ..ui.style import apply_style
from ..ui.qt_app import install_sigint_quit
from .context import ModelingContext
from .ipc import ModelingIpcChild


def build_common_parser(prog: str, *, modes: list[str]) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=prog)
    p.add_argument("--profile", default="", help="I(q) .dat path")
    p.add_argument("--gnom", default="", help="GNOM/DATGNOM .out path")
    p.add_argument("--output-dir", default="", dest="output_dir", help="Modeling output directory")
    p.add_argument("--mode", default="none", choices=["none", *modes], help="Engine mode")
    p.add_argument(
        "--require-gnom-for-dam",
        action="store_true",
        help="When set (liveview), DAMMIF Confirm requires --gnom",
    )
    p.add_argument(
        "--context-file",
        default="",
        help="JSON ModelingContext from liveview (paths + options); overrides flags when set",
    )
    p.add_argument(
        "--ipc",
        action="store_true",
        help="Enable JSON-lines IPC with parent (liveview) on stdin/stdout",
    )
    return p


def context_from_args(ns: argparse.Namespace) -> ModelingContext:
    ctx = ModelingContext(
        profile_path=str(getattr(ns, "profile", "") or ""),
        gnom_path=str(getattr(ns, "gnom", "") or ""),
        output_dir=str(getattr(ns, "output_dir", "") or ""),
        mode=str(getattr(ns, "mode", "none") or "none").strip().lower() or "none",
        require_gnom_for_dam=bool(getattr(ns, "require_gnom_for_dam", False)),
    )
    path = str(getattr(ns, "context_file", "") or "").strip()
    if not path:
        return ctx
    try:
        raw = Path(path).expanduser().read_text(encoding="utf-8")
        data = json.loads(raw)
    except Exception:
        return ctx
    if not isinstance(data, dict):
        return ctx
    file_ctx = ModelingContext.from_dict(data)
    # File is SSOT from liveview; fill only missing argv fields if file omitted them.
    return ModelingContext(
        profile_path=file_ctx.profile_path or ctx.profile_path,
        gnom_path=file_ctx.gnom_path or ctx.gnom_path,
        output_dir=file_ctx.output_dir or ctx.output_dir,
        mode=(file_ctx.mode if file_ctx.mode and file_ctx.mode != "none" else ctx.mode)
        or "none",
        options=dict(file_ctx.options or {}),
        require_gnom_for_dam=bool(file_ctx.require_gnom_for_dam or ctx.require_gnom_for_dam),
        sample_id=file_ctx.sample_id or ctx.sample_id,
    )


def run_modeling_window(
    *,
    prog: str,
    modes: list[str],
    window_factory: Callable[[ModelingContext, Optional[ModelingIpcChild]], QMainWindow],
    argv: Optional[list[str]] = None,
) -> int:
    parser = build_common_parser(prog, modes=modes)
    ns, _unknown = parser.parse_known_args(argv if argv is not None else sys.argv[1:])
    ctx = context_from_args(ns)

    app = QApplication(sys.argv if argv is None else [prog, *(argv or [])])
    apply_style(app)
    install_sigint_quit(app)

    ipc: Optional[ModelingIpcChild] = None
    notifier: Optional[QSocketNotifier] = None
    if bool(ns.ipc):
        ipc = ModelingIpcChild()
        ipc.enabled = True
        # Keep stdout exclusively for JSON-lines IPC; send prints to stderr.
        ipc._out = sys.stdout.buffer
        sys.stdout = sys.stderr
        notifier = QSocketNotifier(sys.stdin.fileno(), QSocketNotifier.Read, app)

        def _on_stdin(_fd: int) -> None:
            try:
                chunk = sys.stdin.buffer.read1(65536)  # type: ignore[attr-defined]
            except Exception:
                chunk = sys.stdin.buffer.read(65536)
            if not chunk:
                return
            ipc.feed_stdin_chunk(chunk.decode(errors="replace"))

        notifier.activated.connect(_on_stdin)

    win = window_factory(ctx, ipc)
    if ipc is not None:
        ipc.context_received.connect(win.apply_context)
        ipc.focus_requested.connect(lambda: (win.show(), win.raise_(), win.activateWindow()))
        ipc.shutdown_requested.connect(app.quit)
        ipc.send_ready()

    win.show()
    return int(app.exec_())
