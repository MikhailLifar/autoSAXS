"""
Persist calibration (integrator dir, curve preview path), buffer overlays /
subtract options, intake/watch mode, and mask paths under the watch directory.

``auto_processing`` (Auto/Manual) is intentionally not persisted — launches always
start in Auto.

``buffer_rules`` live only in hierarchical ``config.conf`` (not here). Legacy
session ``buffer_rules`` are migrated once into the root config on load.

Layout: ``<watchdir>/.guisaxs_liveview/session.yaml``
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from autosaxs.core.buffer_rules import rules_from_session_yaml

from .state import LiveviewIntakeMode, LiveviewSessionState, LiveviewWatchMode
from .watchdir_config import (
    load_buffer_rules_from_config_path,
    migrate_session_rules_into_root_config,
    root_config_path,
    seed_root_config_conf,
)

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


def _norm_sample_key(watchdir: Path, key: str) -> Optional[str]:
    p = _resolve_saved_path(watchdir, key)
    if p is None:
        return None
    try:
        return str(p.resolve())
    except OSError:
        return str(p)


def project_config_rules(state: LiveviewSessionState) -> None:
    """Seed root config if needed and refresh ``root_rules`` / rules cache."""
    try:
        wd = state.watchdir.expanduser().resolve()
    except (OSError, RuntimeError):
        return
    root = seed_root_config_conf(wd)
    state.rules_by_config_dir.clear()
    try:
        rules = load_buffer_rules_from_config_path(root)
    except ValueError:
        rules = None
    if rules is None:
        from autosaxs.core.buffer_rules import DEFAULT_BUFFER_RULES

        rules = tuple(DEFAULT_BUFFER_RULES)
    state.root_rules = rules
    state.rules_by_config_dir[str(root.parent.resolve())] = rules
    # Default subtract options so PAIRED samples can run without Set buffer.
    if state.subtract_options is None and state.root_rules:
        state.subtract_options = {}


def save_liveview_session_settings(state: LiveviewSessionState) -> None:
    """Write session file; no-op if watchdir is unusable. Never writes buffer_rules."""
    try:
        wd = state.watchdir.expanduser().resolve()
    except (OSError, RuntimeError):
        return
    try:
        out_dir = wd / SESSION_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / SESSION_FILE
        overrides: Dict[str, str] = {}
        for key, buf in (state.buffer_overrides or {}).items():
            k_rel = _as_rel_if_under(wd, Path(key)) or key
            b_rel = _as_rel_if_under(wd, Path(buf)) or buf
            overrides[str(k_rel)] = str(b_rel)
        data: Dict[str, Any] = {
            "version": 1,
            "watch_mode": state.watch_mode.value,
            "intake_mode": state.intake_mode.value,
            "integrator_dir": _as_rel_if_under(wd, state.integrator_dir),
            "buffer_dat_path": _as_rel_if_under(wd, state.buffer_dat_path),
            "subtract_options": state.subtract_options,
            "buffer_overrides": overrides,
            "configuration_opened_once": bool(state.configuration_opened_once),
            "calibration_curve_plot_path": _as_rel_if_under(wd, state.calibration_curve_plot_path),
            "calibration_refined_yml_path": _as_rel_if_under(wd, state.calibration_refined_yml_path),
            "mask_path": _as_rel_if_under(wd, state.mask_path),
            "mask_preview_path": _as_rel_if_under(wd, state.mask_preview_path),
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

    Seeds root ``config.conf``, migrates legacy session ``buffer_rules`` once,
    and projects rules into ``state.root_rules``. Drops stale paths.
    """
    try:
        wd = state.watchdir.expanduser().resolve()
    except (OSError, RuntimeError):
        return False

    path = wd / SESSION_DIR / SESSION_FILE
    raw: Dict[str, Any] = {}
    loaded = False
    if path.is_file():
        try:
            parsed = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, yaml.YAMLError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            raw = parsed
            loaded = True

    # Migrate legacy session buffer_rules → root config.conf, then drop on save.
    legacy_rules = None
    if "buffer_rules" in raw:
        try:
            legacy_rules = rules_from_session_yaml(raw.get("buffer_rules"))
        except (TypeError, ValueError):
            legacy_rules = None
        if legacy_rules is not None:
            migrate_session_rules_into_root_config(wd, list(legacy_rules))

    seed_root_config_conf(wd)

    if not loaded and not path.is_file():
        project_config_rules(state)
        return False

    wm = raw.get("watch_mode")
    if wm == LiveviewWatchMode.FLAT.value:
        state.watch_mode = LiveviewWatchMode.FLAT
    else:
        state.watch_mode = LiveviewWatchMode.TREE

    im = raw.get("intake_mode")
    try:
        if isinstance(im, str) and im.strip():
            state.intake_mode = LiveviewIntakeMode(im.strip())
    except ValueError:
        state.intake_mode = LiveviewIntakeMode.FRAME_2D

    integ = _resolve_saved_path(wd, raw.get("integrator_dir") if raw.get("integrator_dir") else None)
    if integ is not None and integ.is_dir():
        state.integrator_dir = integ
    else:
        state.integrator_dir = None

    buf = _resolve_saved_path(wd, raw.get("buffer_dat_path") if raw.get("buffer_dat_path") else None)
    if buf is not None and buf.is_file():
        state.buffer_dat_path = buf
    else:
        state.buffer_dat_path = None

    overrides_raw = raw.get("buffer_overrides")
    state.buffer_overrides = {}
    if isinstance(overrides_raw, dict):
        for k, v in overrides_raw.items():
            sk = _norm_sample_key(wd, str(k))
            bp = _resolve_saved_path(wd, str(v) if v is not None else None)
            if sk and bp is not None and bp.is_file():
                state.buffer_overrides[sk] = str(bp.resolve())

    # Legacy pin without overlays: keep as last-picked hint only (Fixed = config rule).
    if (
        not state.buffer_overrides
        and state.buffer_dat_path is not None
        and legacy_rules is None
        and "buffer_overrides" not in raw
    ):
        # Older sessions used a global pin; migrate Fixed as catch-all into config once.
        from autosaxs.core.buffer_rules import seed_literal_rule_from_buffer_path

        migrate_session_rules_into_root_config(
            wd, list(seed_literal_rule_from_buffer_path(state.buffer_dat_path))
        )

    opts = raw.get("subtract_options")
    if isinstance(opts, dict):
        state.subtract_options = {str(k): v for k, v in opts.items()}
    else:
        state.subtract_options = None

    state.configuration_opened_once = bool(raw.get("configuration_opened_once"))

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

    mask = _resolve_saved_path(wd, raw.get("mask_path") if raw.get("mask_path") else None)
    if mask is not None and mask.is_file():
        state.mask_path = mask
    else:
        state.mask_path = None

    mask_prev = _resolve_saved_path(
        wd, raw.get("mask_preview_path") if raw.get("mask_preview_path") else None
    )
    if mask_prev is not None and mask_prev.is_file():
        state.mask_preview_path = mask_prev
    else:
        state.mask_preview_path = None

    project_config_rules(state)

    # Drop legacy buffer_rules from session.yaml on next save.
    if "buffer_rules" in raw:
        save_liveview_session_settings(state)

    return loaded
