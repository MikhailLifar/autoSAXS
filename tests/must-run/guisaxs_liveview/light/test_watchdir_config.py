"""Liveview config.conf seed, hierarchy, overlay, and Configuration validation."""

from __future__ import annotations

from pathlib import Path

import yaml

from autosaxs.core.buffer_rules import MatchKind
from guisaxs_skills.liveview.pipeline.buffer_match import resolve_match_for_sample
from guisaxs_skills.liveview.pipeline.plan import plan_for
from guisaxs_skills.liveview.session.api import LiveviewSession
from guisaxs_skills.liveview.session.persistence import (
    load_liveview_session_settings,
    save_liveview_session_settings,
    session_settings_path,
)
from guisaxs_skills.liveview.session.sample import Sample
from guisaxs_skills.liveview.session.state import (
    LiveviewIntakeMode,
    LiveviewSessionState,
)
from guisaxs_skills.liveview.session.watchdir_config import (
    DEFAULT_CONFIG_SEED_TEXT,
    effective_config_path,
    load_effective_buffer_rules,
    seed_root_config_conf,
    validate_config_text,
    write_config_text,
)


def test_seed_root_config_on_bind(tmp_path: Path) -> None:
    wd = tmp_path / "watch"
    wd.mkdir()
    assert not (wd / "config.conf").exists()
    session = LiveviewSession(watchdir=wd, load_settings=True)
    conf = wd / "config.conf"
    assert conf.is_file()
    text = conf.read_text(encoding="utf-8")
    assert "buffer_rules" in text
    assert "#" in text  # comments preserved in seed
    assert session.state.buffer_ready() is True
    assert session.state.root_rules


def test_hierarchy_nearest_wins(tmp_path: Path) -> None:
    wd = tmp_path / "watch"
    child = wd / "runA"
    child.mkdir(parents=True)
    seed_root_config_conf(wd)
    write_config_text(
        child / "config.conf",
        'buffer_rules:\n  "*child*": "child_buf.dat"\n',
    )
    sample = child / "child_sample.tif"
    sample.write_bytes(b"x")
    rules = load_effective_buffer_rules(sample, wd)
    assert rules == (("*child*", "child_buf.dat"),)
    assert effective_config_path(sample, wd) == (child / "config.conf").resolve()


def test_child_without_buffer_rules_falls_through(tmp_path: Path) -> None:
    wd = tmp_path / "watch"
    child = wd / "runA"
    child.mkdir(parents=True)
    seed_root_config_conf(wd)
    (child / "config.conf").write_text("analysis: mono\n", encoding="utf-8")
    sample = child / "foo_sample.tif"
    sample.write_bytes(b"x")
    rules = load_effective_buffer_rules(sample, wd)
    assert rules[0][0] == "*{stem}_sample*"


def test_overlay_beats_rules(tmp_path: Path) -> None:
    wd = tmp_path / "watch"
    wd.mkdir()
    session = LiveviewSession(watchdir=wd)
    buf = wd / "manual_buf.dat"
    buf.write_text("0 1\n", encoding="utf-8")
    sample = Sample.from_path(
        str(wd / "unmatched_thing.tif"),
        boarding=LiveviewIntakeMode.FRAME_2D,
    )
    (wd / "unmatched_thing.tif").write_bytes(b"x")
    before = resolve_match_for_sample(session.state, sample, sample_curve=sample.path)
    assert before.kind == MatchKind.UNMATCHED
    session.set_buffer_for_sample(sample, buf, {"q_min": 0.1})
    after = resolve_match_for_sample(session.state, sample, sample_curve=sample.path)
    assert after.kind == MatchKind.PAIRED
    assert Path(after.buffer).resolve() == buf.resolve()


def test_save_rejects_bad_yaml(tmp_path: Path) -> None:
    assert validate_config_text("buffer_rules: [") is not None
    assert validate_config_text("nope: 1\n") is not None
    assert validate_config_text(DEFAULT_CONFIG_SEED_TEXT) is None
    assert validate_config_text('buffer_rules:\n  "": "x"\n') is not None


def test_comment_roundtrip_smoke(tmp_path: Path) -> None:
    wd = tmp_path / "watch"
    wd.mkdir()
    path = seed_root_config_conf(wd)
    original = path.read_text(encoding="utf-8")
    assert "#" in original
    err = validate_config_text(original)
    assert err is None
    write_config_text(path, original)
    assert path.read_text(encoding="utf-8") == original


def test_session_yaml_has_no_buffer_rules_after_migration(tmp_path: Path) -> None:
    wd = tmp_path / "watch"
    wd.mkdir()
    path = session_settings_path(wd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "version: 1\n"
        "watch_mode: tree\n"
        "intake_mode: frame_2d\n"
        "subtract_options: {q_min: 0.1}\n"
        "buffer_rules:\n"
        "  - sample_expr: '*solo*'\n"
        "    buffer_expr: ''\n",
        encoding="utf-8",
    )
    s = LiveviewSessionState(watchdir=wd)
    assert load_liveview_session_settings(s) is True
    # Migrated into config.conf; session save must not retain buffer_rules.
    save_liveview_session_settings(s)
    raw = yaml.safe_load(session_settings_path(wd).read_text(encoding="utf-8"))
    assert "buffer_rules" not in raw
    conf = (wd / "config.conf").read_text(encoding="utf-8")
    assert "*solo*" in conf


def test_plan_for_uses_overlay(tmp_path: Path) -> None:
    wd = tmp_path / "watch"
    wd.mkdir()
    session = LiveviewSession(watchdir=wd)
    session.state.integrator_dir = wd / "integ"
    session.state.integrator_dir.mkdir()
    buf = wd / "buf.dat"
    buf.write_text("0 1\n", encoding="utf-8")
    tiff = wd / "weird.tif"
    tiff.write_bytes(b"x")
    sample = Sample.from_path(str(tiff), boarding=LiveviewIntakeMode.FRAME_2D)
    session.set_buffer_for_sample(sample, buf, {})
    plan = plan_for(session.state, sample, load_yaml=lambda _p: {})
    names = [s.name for s in plan.steps]
    assert "integrate" in names
    assert "subtract" in names
