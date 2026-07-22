from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path


def _cache_root() -> Path:
    try:
        cache = Path.home() / ".cache" / "autosaxs" / "help" / "guisaxs_liveview"
        cache.parent.mkdir(parents=True, exist_ok=True)
        return cache
    except OSError:
        import tempfile

        return Path(tempfile.gettempdir()) / "autosaxs_guisaxs_liveview_help"


def _needs_refresh(*, src: Path, dest: Path) -> bool:
    if not dest.is_dir():
        return True
    manifest = src / "manifest.yaml"
    cached = dest / "manifest.yaml"
    if manifest.is_file() and cached.is_file():
        try:
            return manifest.stat().st_mtime_ns > cached.stat().st_mtime_ns
        except OSError:
            return True
    return not cached.is_file()


def liveview_help_root() -> Path:
    """
    Return a directory containing manifest.yaml, html/, and style/ for the help viewer.

    Copies bundled package data to a user cache directory so QTextBrowser can load
    pages via file:// URLs (relative links and CSS work without compilation).
    """
    dest = _cache_root()
    pkg = resources.files("autosaxs.resources.help.guisaxs_liveview")
    with resources.as_file(pkg) as src_path:
        src = Path(src_path)
        if _needs_refresh(src=src, dest=dest):
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest)
    return dest


def liveview_help_manifest_path() -> Path:
    root = liveview_help_root()
    path = root / "manifest.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"Help manifest not found: {path}")
    return path
