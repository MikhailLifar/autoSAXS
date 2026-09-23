"""
Persist sample history + analysis arming under the watch directory.

Layout: ``<watchdir>/.guisaxs_liveview/history.yaml``

Entries are ordered ``{path, boarding}``. Skill outputs stay on disk and are
re-read when navigating. Missing source files are dropped on load (and the
file is rewritten) so memory and disk stay aligned.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from ..ingest.sample_revision import is_dat_path, is_tiff_path
from .persistence import SESSION_DIR, _as_rel_if_under, _resolve_saved_path
from .sample import Sample
from .sample_store import SampleStore
from .state import LiveviewIntakeMode, LiveviewSessionState

HISTORY_FILE = "history.yaml"


def history_settings_path(watchdir: Path) -> Path:
    return watchdir.expanduser().resolve() / SESSION_DIR / HISTORY_FILE


def _boarding_matches_path(path: str, boarding: LiveviewIntakeMode) -> bool:
    if boarding == LiveviewIntakeMode.FRAME_2D:
        return is_tiff_path(path)
    if boarding in (LiveviewIntakeMode.CURVE_1D, LiveviewIntakeMode.CURVE_SUB):
        return is_dat_path(path)
    return False


def _parse_entry(
    watchdir: Path, raw: Any
) -> Optional[Tuple[str, LiveviewIntakeMode]]:
    if not isinstance(raw, dict):
        return None
    path = _resolve_saved_path(watchdir, raw.get("path") if raw.get("path") else None)
    if path is None or not path.is_file():
        return None
    key = str(path)
    boarding_raw = raw.get("boarding")
    try:
        if not isinstance(boarding_raw, str) or not boarding_raw.strip():
            return None
        boarding = LiveviewIntakeMode(boarding_raw.strip())
    except ValueError:
        return None
    if not _boarding_matches_path(key, boarding):
        return None
    return key, boarding


def save_liveview_history(*, watchdir: Path, store: SampleStore, state: LiveviewSessionState) -> None:
    """Atomic write of history + arming; no-op if watchdir is unusable."""
    try:
        wd = watchdir.expanduser().resolve()
    except (OSError, RuntimeError):
        return
    try:
        out_dir = wd / SESSION_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / HISTORY_FILE
        entries: List[Dict[str, str]] = []
        for sample in store.history():
            rel = _as_rel_if_under(wd, Path(sample.path))
            if not rel:
                continue
            entries.append({"path": rel, "boarding": sample.boarding.value})
        data: Dict[str, Any] = {
            "version": 1,
            "index": int(store.index) if entries else 0,
            "monodisperse_armed": bool(state.monodisperse_armed),
            "polydisperse_armed": bool(state.polydisperse_armed),
            "entries": entries,
        }
        text = yaml.safe_dump(data, sort_keys=True, allow_unicode=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        pass


def load_liveview_history(*, watchdir: Path, store: SampleStore, state: LiveviewSessionState) -> bool:
    """
    Load history.yaml into ``store`` and arming flags into ``state``.

    Drops broken entries (missing file, bad boarding, path/boarding mismatch,
    duplicates keep first slot with last boarding via SampleStore). Rewrites
    the file when the loaded snapshot differs from disk so state cannot drift.
    Returns True if the file existed and was read.
    """
    try:
        wd = watchdir.expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    path = wd / SESSION_DIR / HISTORY_FILE
    if not path.is_file():
        return False
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, yaml.YAMLError, TypeError):
        return False
    if not isinstance(raw, dict):
        store.clear()
        state.monodisperse_armed = False
        state.polydisperse_armed = False
        save_liveview_history(watchdir=wd, store=store, state=state)
        return True

    raw_entries = raw.get("entries")
    samples: List[Sample] = []
    if isinstance(raw_entries, list):
        for item in raw_entries:
            parsed = _parse_entry(wd, item)
            if parsed is None:
                continue
            key, boarding = parsed
            samples.append(Sample.from_path(key, boarding=boarding))

    saved_index = raw.get("index", 0)
    try:
        index = int(saved_index)
    except (TypeError, ValueError):
        index = 0

    store.replace_history(samples, index=index)

    state.monodisperse_armed = bool(raw.get("monodisperse_armed"))
    state.polydisperse_armed = bool(raw.get("polydisperse_armed"))

    # Normalize disk: drop stale paths / dedupe / clamp index so reload is idempotent.
    save_liveview_history(watchdir=wd, store=store, state=state)
    return True
