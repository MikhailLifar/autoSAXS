from __future__ import annotations

import shutil
from pathlib import Path


def calibration_subdir(watchdir: Path) -> Path:
    out = (watchdir / "calibration").resolve()
    out.mkdir(parents=True, exist_ok=True)
    return out


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def ensure_tiff_in_calibration(watchdir: Path, src_path: str) -> str:
    """
    Return a path to a calibration TIFF under ``watchdir/calibration/``.

    If ``src_path`` is already inside that folder, return it unchanged.
    Otherwise copy the file into ``calibration/`` (same basename) and return the copy.
    """
    src = Path((src_path or "").strip()).expanduser()
    if not src.is_file():
        raise FileNotFoundError(f"Not a file: {src}")
    src_r = src.resolve()
    cal = calibration_subdir(watchdir)
    if _is_under(src_r, cal):
        return str(src_r)
    dest = cal / src_r.name
    shutil.copy2(src_r, dest)
    return str(dest.resolve())


def ensure_path_in_calibration(
    watchdir: Path,
    dest_path: str,
    *,
    default_name: str = "mask.txt",
) -> Path:
    """
    Resolve a destination path and ensure it lives under ``watchdir/calibration/``.
    """
    raw = (dest_path or "").strip()
    p = Path(raw) if raw else Path(default_name)
    if not p.is_absolute():
        p = watchdir / p
    p = p.resolve()
    cal = calibration_subdir(watchdir)
    if _is_under(p, cal):
        return p
    return (cal / p.name).resolve()
