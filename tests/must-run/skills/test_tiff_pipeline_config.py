"""Unit tests for YAML TIFF→report config parse + sample/buffer pairing."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
import yaml

from autosaxs.skill.tiff_pipeline import (
    DEFAULT_BUFFER_RULES,
    DEFAULT_CALIBRANT_GLOB,
    ClassifiedFrames,
    classify_tiffs,
    load_tiff_pipeline_config,
    pair_sample_buffer_1d,
    resolve_frames_dir_for_tiff_pipeline,
    resolve_tiff_config_path,
)


def _write_conf(path: Path, doc: dict) -> str:
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(path)


def test_load_defaults_when_keys_omitted(tmp_path: Path):
    conf = _write_conf(tmp_path / "config.conf", {"analysis": "mono"})
    cfg = load_tiff_pipeline_config(conf)
    assert cfg.calibrant == DEFAULT_CALIBRANT_GLOB
    assert dict(cfg.buffer_rules) == DEFAULT_BUFFER_RULES
    assert cfg.analysis == "mono"
    assert cfg.config_path == conf


def test_load_overrides_and_rejects_bad_analysis(tmp_path: Path):
    conf = _write_conf(
        tmp_path / "pipe.yaml",
        {
            "calibrant": "*LaB6*.tif",
            "buffer_rules": {"*samp*.tif": "*buf*.tif"},
            "analysis": "poly",
            "subtract": {"q_min": 1.0, "q_max": 2.0},
        },
    )
    cfg = load_tiff_pipeline_config(conf)
    assert cfg.calibrant == "*LaB6*.tif"
    assert dict(cfg.buffer_rules) == {"*samp*.tif": "*buf*.tif"}
    assert cfg.analysis == "poly"

    bad = _write_conf(tmp_path / "bad.conf", {"analysis": "both"})
    with pytest.raises(ValueError, match="analysis"):
        load_tiff_pipeline_config(bad)


def test_resolve_config_defaults_to_frames_dir(tmp_path: Path):
    frames = tmp_path / "frames"
    frames.mkdir()
    conf = frames / "config.conf"
    conf.write_text("analysis: mono\n", encoding="utf-8")
    assert resolve_tiff_config_path(str(frames)) == str(conf.resolve())

    other = tmp_path / "other.yaml"
    other.write_text("analysis: poly\n", encoding="utf-8")
    assert resolve_tiff_config_path(str(frames), str(other)) == str(other.resolve())

    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        resolve_tiff_config_path(str(empty))


def test_classify_calibrant_sample_buffer_ignore(tmp_path: Path):
    frames = tmp_path / "tiffs"
    frames.mkdir()
    for name in (
        "AgBh_calib.tif",
        "Z_AgBh_extra.tif",  # second calibrant match; lex-first wins → AgBh_calib
        "run_sample_01.tif",
        "run_buffer_01.tif",
        "noise.tif",
        "notes.txt",
    ):
        (frames / name).write_bytes(b"")

    cfg = load_tiff_pipeline_config(
        _write_conf(tmp_path / "c.conf", {"analysis": "mono"})
    )
    classified = classify_tiffs(str(frames), cfg)
    assert isinstance(classified, ClassifiedFrames)
    assert Path(classified.calibrant_path).name == "AgBh_calib.tif"
    assert [Path(p).name for p in classified.sample_paths] == ["run_sample_01.tif"]
    assert [Path(p).name for p in classified.buffer_paths] == ["run_buffer_01.tif"]
    assert [Path(p).name for p in classified.ignored_paths] == [
        "noise.tif",
        "Z_AgBh_extra.tif",
    ]


def test_pair_aligned_and_shared_buffer(tmp_path: Path):
    s1 = str(tmp_path / "int_ihs27_sample.dat")
    s2 = str(tmp_path / "int_other_sample.dat")
    b1 = str(tmp_path / "int_ihs27_buffer.dat")
    for p in (s1, s2, b1):
        Path(p).write_text("0 1 0\n", encoding="utf-8")

    pairs = pair_sample_buffer_1d([s1], [b1])
    assert pairs == [(s1, b1)]

    # Shared single buffer for unmatched sample names
    pairs2 = pair_sample_buffer_1d([s1, s2], [b1])
    assert set(pairs2) == {(s1, b1), (s2, b1)}


def test_pair_overlap_raises(tmp_path: Path):
    s = str(tmp_path / "int_ihs27_long_sample.dat")
    b1 = str(tmp_path / "int_ihs27_buffer.dat")
    b2 = str(tmp_path / "int_ihs27_long_buffer.dat")
    for p in (s, b1, b2):
        Path(p).write_text("0 1 0\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="ambiguous|Overlapped"):
        pair_sample_buffer_1d([s], [b1, b2])


def test_process_full_registered_and_cli_description(capsys):
    from autosaxs.cli import main as cli_main
    from autosaxs.skill import SKILL_ORDER, list_skills

    skills = list_skills()
    assert "process_full" in skills
    assert "process_directory" not in skills
    assert "process_full" in SKILL_ORDER
    assert SKILL_ORDER.index("process_full") > SKILL_ORDER.index("process_polydisperse")

    rc = cli_main(["process-full", "--description"])
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "process-full" in out
    assert "calibrate" in out
    assert "analysis" in out

    # Old CLI name must not exist
    with pytest.raises(SystemExit):
        cli_main(["process-directory", "--description"])


def test_process_meta_skills_have_no_frames_dir_kw():
    from autosaxs.skill.process_monodisperse import process_monodisperse
    from autosaxs.skill.process_polydisperse import process_polydisperse

    for fn in (process_monodisperse, process_polydisperse):
        sig = inspect.signature(fn)
        assert "frames_dir" not in sig.parameters
        assert "profile" in sig.parameters
        assert "config_path" in sig.parameters


def test_resolve_frames_dir_inference_rules(tmp_path: Path):
    frames = tmp_path / "run"
    frames.mkdir()
    (frames / "AgBh.tif").write_bytes(b"")
    (frames / "s_sample.tif").write_bytes(b"")
    (frames / "s_buffer.tif").write_bytes(b"")

    # TIFFs present, no config → hard error
    with pytest.raises(FileNotFoundError, match="pipeline config"):
        resolve_frames_dir_for_tiff_pipeline(str(frames))

    conf = frames / "config.conf"
    conf.write_text("analysis: mono\n", encoding="utf-8")
    got = resolve_frames_dir_for_tiff_pipeline(str(frames))
    assert got == str(frames.resolve())

    # Explicit --conf elsewhere also unlocks TIFF mode
    other = tmp_path / "other.yaml"
    other.write_text("analysis: poly\n", encoding="utf-8")
    frames2 = tmp_path / "run2"
    frames2.mkdir()
    (frames2 / "x.tif").write_bytes(b"")
    assert resolve_frames_dir_for_tiff_pipeline(str(frames2), str(other)) == str(
        frames2.resolve()
    )

    # .dat-only directory → no TIFF trigger
    dats = tmp_path / "dats"
    dats.mkdir()
    (dats / "sub_a.dat").write_text("0 1 0\n", encoding="utf-8")
    assert resolve_frames_dir_for_tiff_pipeline(str(dats)) is None

    # Single .dat file → no TIFF trigger
    assert resolve_frames_dir_for_tiff_pipeline(str(dats / "sub_a.dat")) is None

    # Mixed TIFF + .dat with config → TIFF trigger (loose .dats ignored by front-end)
    (frames / "leftover.dat").write_text("0 1 0\n", encoding="utf-8")
    assert resolve_frames_dir_for_tiff_pipeline(str(frames)) == str(frames.resolve())

    # Globs / comma-lists / path lists never trigger
    assert resolve_frames_dir_for_tiff_pipeline(str(frames / "*.tif")) is None
    assert resolve_frames_dir_for_tiff_pipeline(f"{frames},{dats}") is None
    assert resolve_frames_dir_for_tiff_pipeline([str(frames)]) is None


def test_coerce_dat_path_expression_accepts_list(tmp_path: Path):
    from autosaxs.skill.common import coerce_dat_path_expression

    a = tmp_path / "a.dat"
    b = tmp_path / "b.dat"
    a.write_text("0 1 0\n", encoding="utf-8")
    b.write_text("0 1 0\n", encoding="utf-8")
    expr = coerce_dat_path_expression([str(a), str(b)])
    got = expr.unwrap()
    assert set(got) == {str(a), str(b)}
