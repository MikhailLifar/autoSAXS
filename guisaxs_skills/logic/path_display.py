from __future__ import annotations

from pathlib import Path


def contracted_path_label(path: str | Path) -> tuple[str, str]:
    """
    Return (short_label, full_path) for UI: show parent/filename when possible,
    full resolved path for tooltips.
    """
    raw = Path(str(path).strip())
    try:
        p = raw.expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        p = raw.expanduser()
    full = str(p)
    name = p.name
    if not name:
        return (full or "", full or "")
    parts = p.parts
    if len(parts) >= 2:
        parent_seg = parts[-2]
        # Windows: ('C:\\', 'file.txt') — avoid "C:\\/file.txt"
        if len(parts) == 2 and len(parent_seg) == 3 and parent_seg[1] == ":":
            short = name
        else:
            short = f"{parent_seg}/{name}"
    else:
        short = name
    return (short, full)
