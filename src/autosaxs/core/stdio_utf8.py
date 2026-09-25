"""UTF-8 console stdio helpers (Windows cp1251-safe printing)."""

from __future__ import annotations

import sys
from typing import Any, Optional, TextIO


def reconfigure_stdio_utf8() -> None:
    """Reconfigure stdout/stderr to UTF-8 with ``errors='replace'`` when supported."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def safe_print(
    *args: Any,
    sep: str = " ",
    end: str = "\n",
    file: Optional[TextIO] = None,
    flush: bool = False,
) -> None:
    """Like ``print``, but never raises on encode / stream write failures."""
    target: TextIO = sys.stdout if file is None else file
    try:
        print(*args, sep=sep, end=end, file=target, flush=flush)
        return
    except Exception:
        pass
    try:
        text = sep.join(str(a) for a in args) + end
        encoding = getattr(target, "encoding", None) or "utf-8"
        raw = text.encode(encoding, errors="replace")
        buffer = getattr(target, "buffer", None)
        if buffer is not None:
            buffer.write(raw)
            if flush:
                buffer.flush()
        else:
            target.write(raw.decode(encoding, errors="replace"))
            if flush:
                target.flush()
    except Exception:
        pass
