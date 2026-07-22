from __future__ import annotations

from urllib.parse import unquote, urlparse


def normalize_pathish(value: str) -> str:
    """
    Normalize user-entered path-like strings.

    - Accepts plain paths (returned unchanged).
    - Accepts file URIs (e.g. file:///home/user/x.conf) and converts to /home/user/x.conf.
    """
    s = (value or "").strip()
    if s.startswith("file://"):
        p = urlparse(s)
        if p.scheme == "file":
            return unquote(p.path)
    return s

