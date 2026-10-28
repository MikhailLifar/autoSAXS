"""Liveview helpers: buffer candidate discovery for ``plan_for`` matching."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

from ..session.state import LiveviewSessionState


def collect_buffer_candidates(
    state: LiveviewSessionState,
    *,
    sample_output_root: Optional[Path] = None,
    exclude: Sequence[str] | None = None,
) -> List[str]:
    """
    Discover ``.dat`` buffer candidates under the watchdir / averaged trees.

    Non-recursive under watchdir root and ``averaged/``. Also includes the
    session ``buffer_dat_path`` pin when present. ``exclude`` paths (the sample
    curve itself) are omitted.
    """
    exclude_keys = {_norm_key(p) for p in (exclude or ()) if p}
    found: dict[str, str] = {}

    def _add(path: Path) -> None:
        try:
            if not path.is_file():
                return
            if path.suffix.lower() != ".dat":
                return
            key = _norm_key(str(path))
            if key in exclude_keys:
                return
            found[key] = str(path.resolve())
        except OSError:
            return

    wd = state.watchdir.expanduser().resolve()
    roots: List[Path] = [wd, wd / "averaged"]
    if sample_output_root is not None:
        try:
            root = sample_output_root.expanduser().resolve()
            roots.append(root)
            roots.append(root / "averaged")
        except OSError:
            pass

    for root in roots:
        try:
            if not root.is_dir():
                continue
            for p in sorted(root.iterdir()):
                _add(p)
        except OSError:
            continue

    if state.buffer_dat_path is not None:
        _add(Path(state.buffer_dat_path))

    return list(found.values())


def _norm_key(path: str) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(path).strip()
