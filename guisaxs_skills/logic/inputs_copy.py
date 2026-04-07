from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import List

from ..core.models import RunRequest
from ..core.paths import inputs_dir
from .path_normalize import normalize_pathish


def _looks_like_glob(s: str) -> bool:
    # Simple heuristic: if user typed wildcard chars, treat as glob expression.
    # We must not attempt to copy it as a concrete file path.
    return any(ch in s for ch in ("*", "?", "["))


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _dedup_target(target: Path) -> Path:
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for i in range(1, 10000):
        cand = target.with_name(f"{stem}_{i}{suffix}")
        if not cand.exists():
            return cand
    raise RuntimeError(f"Could not deduplicate target: {target}")


def maybe_copy_inputs(*, request: RunRequest, workdir: Path, enabled: bool) -> RunRequest:
    if not enabled:
        # Still normalize file:// URIs even if copying is disabled.
        new_pos = [normalize_pathish(p) for p in request.positional]
        new_opts = {k: (normalize_pathish(v) if isinstance(v, str) else v) for k, v in request.options.items()}
        return RunRequest(skill_name=request.skill_name, positional=new_pos, options=new_opts)

    inp_dir = inputs_dir(workdir)
    inp_dir.mkdir(parents=True, exist_ok=True)

    new_pos: List[str] = []
    for p in request.positional:
        p = normalize_pathish(p)
        if _looks_like_glob(p):
            # Keep glob expressions as-is (skills/CLI may expand them).
            new_pos.append(p)
            continue
        src = Path(os.path.expanduser(p))
        if _is_under(src, workdir):
            new_pos.append(str(src))
            continue
        dst = _dedup_target(inp_dir / src.name)
        shutil.copy2(str(src), str(dst))
        new_pos.append(str(dst))

    new_opts = dict(request.options)
    # Copy path-like options when they exist and are strings. Minimal heuristic for v1.
    for k, v in list(new_opts.items()):
        if not isinstance(v, str):
            continue
        if k in ("output_dir",):
            continue
        v = normalize_pathish(v)
        if _looks_like_glob(v):
            continue
        src = Path(os.path.expanduser(v))
        if not src.exists():
            continue
        if _is_under(src, workdir):
            continue
        if src.is_dir():
            continue  # v1: do not copy directories
        dst = _dedup_target(inp_dir / src.name)
        shutil.copy2(str(src), str(dst))
        new_opts[k] = str(dst)

    return RunRequest(skill_name=request.skill_name, positional=new_pos, options=new_opts)

