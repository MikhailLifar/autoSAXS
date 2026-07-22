from __future__ import annotations

from pathlib import Path

from ...core.models import RunRequest


def resolved_profile_path(profile_arg: str, *, watchdir: Path) -> Path:
    p = Path((profile_arg or "").strip().split(",")[0].strip()).expanduser()
    return p.resolve() if p.is_absolute() else (watchdir / p).resolve()


def resolve_under_watchdir(path_str: str, *, watchdir: Path) -> str:
    p = Path((path_str or "").strip()).expanduser()
    if not str(p):
        raise ValueError("Empty path")
    if p.is_absolute():
        return str(p.resolve())
    return str((watchdir / p).resolve())


def profile_path_exists(req: RunRequest, *, watchdir: Path) -> bool:
    if not req.positional:
        return False
    raw = (req.positional[0] or "").strip()
    if not raw:
        return False
    return resolved_profile_path(raw, watchdir=watchdir).is_file()


def normalize_fit_request(
    req: RunRequest,
    *,
    watchdir: Path,
    default_output_subdir: str,
) -> RunRequest:
    opts = dict(req.options)
    opts.pop("use_cache", None)
    od = opts.get("output_dir", "")
    opts["output_dir"] = (
        resolve_under_watchdir(str(od), watchdir=watchdir)
        if (isinstance(od, str) and od.strip())
        else str((watchdir / default_output_subdir).resolve())
    )
    opts["use_cache"] = False
    positional: list[str] = []
    for p in req.positional:
        raw = (p or "").strip()
        if "," in raw:
            positional.append(raw)
        else:
            positional.append(resolve_under_watchdir(raw, watchdir=watchdir))
    return RunRequest(skill_name=req.skill_name, positional=positional, options=opts)
