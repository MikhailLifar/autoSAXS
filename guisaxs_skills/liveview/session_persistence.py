"""
Persist calibration (integrator dir, curve preview path) and buffer/subtract options
under the watch directory so a restart restores them without re-entering wizards.

Layout: ``<watchdir>/.guisaxs_liveview/session.yaml``
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .state import LiveviewSessionState, LiveviewWatchMode

SESSION_DIR = ".guisaxs_liveview"
SESSION_FILE = "session.yaml"


def session_settings_path(watchdir: Path) -> Path:
    return watchdir.expanduser().resolve() / SESSION_DIR / SESSION_FILE


def _as_rel_if_under(watchdir: Path, p: Optional[Path]) -> Optional[str]:
    if p is None:
        return None
    try:
        pr = p.expanduser().resolve()
        rel = pr.relative_to(watchdir)
        return rel.as_posix()
    except (OSError, ValueError, RuntimeError):
        return str(p.expanduser().resolve())


def _resolve_saved_path(watchdir: Path, s: Optional[str]) -> Optional[Path]:
    if not isinstance(s, str):
        return None
    t = s.strip()
    if not t:
        return None
    raw = Path(t).expanduser()
    out = raw.resolve() if raw.is_absolute() else (watchdir / raw).resolve()
    return out


def save_liveview_session_settings(state: LiveviewSessionState) -> None:
    """Write session file; no-op if watchdir is unusable."""
    try:
        wd = state.watchdir.expanduser().resolve()
    except (OSError, RuntimeError):
        return
    try:
        out_dir = wd / SESSION_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / SESSION_FILE
        data: Dict[str, Any] = {
            "version": 1,
            "watch_mode": state.watch_mode.value,
            "integrator_dir": _as_rel_if_under(wd, state.integrator_dir),
            "buffer_dat_path": _as_rel_if_under(wd, state.buffer_dat_path),
            "subtract_options": state.subtract_options,
            "calibration_curve_plot_path": _as_rel_if_under(wd, state.calibration_curve_plot_path),
            "calibration_refined_yml_path": _as_rel_if_under(wd, state.calibration_refined_yml_path),
        }
        text = yaml.safe_dump(data, sort_keys=True, allow_unicode=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        pass


def load_liveview_session_settings(state: LiveviewSessionState) -> bool:
    """
    Load from ``.guisaxs_liveview/session.yaml`` into ``state``.
    Drops values whose paths are missing (stale session).
    Returns True if the file existed and was read (even if all fields were cleared).
    """
    try:
        wd = state.watchdir.expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    path = wd / SESSION_DIR / SESSION_FILE
    if not path.is_file():
        return False
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, yaml.YAMLError, TypeError):
        return False
    if not isinstance(raw, dict):
        return True

    wm = raw.get("watch_mode")
    if wm == LiveviewWatchMode.TREE.value:
        state.watch_mode = LiveviewWatchMode.TREE
    else:
        state.watch_mode = LiveviewWatchMode.FLAT

    integ = _resolve_saved_path(wd, raw.get("integrator_dir") if raw.get("integrator_dir") else None)
    if integ is not None and integ.is_dir():
        state.integrator_dir = integ
    else:
        state.integrator_dir = None

    buf = _resolve_saved_path(wd, raw.get("buffer_dat_path") if raw.get("buffer_dat_path") else None)
    opts = raw.get("subtract_options")
    if buf is not None and buf.is_file() and isinstance(opts, dict):
        state.buffer_dat_path = buf
        state.subtract_options = {str(k): v for k, v in opts.items()}
    else:
        state.buffer_dat_path = None
        state.subtract_options = None

    cal_png = _resolve_saved_path(
        wd, raw.get("calibration_curve_plot_path") if raw.get("calibration_curve_plot_path") else None
    )
    if cal_png is not None and cal_png.is_file():
        state.calibration_curve_plot_path = cal_png
    else:
        state.calibration_curve_plot_path = None

    refined = _resolve_saved_path(
        wd, raw.get("calibration_refined_yml_path") if raw.get("calibration_refined_yml_path") else None
    )
    if refined is not None and refined.is_file():
        state.calibration_refined_yml_path = refined
    else:
        state.calibration_refined_yml_path = None

    if state.calibration_refined_yml_path is None and state.integrator_dir is not None:
        cand = state.integrator_dir.parent / "refined.yml"
        if cand.is_file():
            state.calibration_refined_yml_path = cand

    return True
