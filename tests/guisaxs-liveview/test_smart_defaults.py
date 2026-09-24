from __future__ import annotations

"""
Tests for path helpers and for :func:`update_session_hints_from_success`.
Session hints are modeled as global shared state—not as an ordered skill pipeline.
"""

from pathlib import Path

from guisaxs_skills.core.models import RunRequest
from guisaxs_skills.logic.session_state import SessionPathHints
from guisaxs_skills.logic.smart_defaults import (
    browse_start_dir_for_resolved_paths,
    common_parent_dir_if_all_files,
    find_calibrant_image_in_workdir,
    find_config_conf_near,
    find_integrator_dir_near,
    find_latest_dat_in_workdir,
    find_mask_near,
    find_single_buffer_dat,
    ingest_shared_mask_and_config_from_request,
    path_expression_paths_fully_exist,
    profile_guess_from_subtract_output,
    session_anchor_dir_from_positional_arg,
    session_hint_for_positional_path,
    session_hint_option_config_path,
    session_hint_option_mask,
    session_hint_soft_buffer_1d,
    update_session_hints_from_success,
)


def test_find_integrator_dir_near_requires_both_json(tmp_path: Path) -> None:
    sub = tmp_path / "integrator"
    sub.mkdir()
    (sub / "detector_params.json").write_text("{}")
    assert find_integrator_dir_near(tmp_path) is None
    (sub / "ai_params.json").write_text("{}")
    assert find_integrator_dir_near(tmp_path) == sub.resolve()


def test_find_integrator_prefers_parent_integrator(tmp_path: Path) -> None:
    inner = tmp_path / "data"
    inner.mkdir()
    integ = tmp_path / "integrator"
    integ.mkdir()
    for name in ("detector_params.json", "ai_params.json"):
        (integ / name).write_text("{}")
    assert find_integrator_dir_near(inner) == integ.resolve()


def test_find_config_conf_prefers_base_over_parent(tmp_path: Path) -> None:
    (tmp_path / "config_a.conf").write_text("x")
    parent = tmp_path.parent
    # avoid clobbering real parent; use nested structure
    sub = tmp_path / "nested"
    sub.mkdir()
    (sub / "config_b.conf").write_text("y")
    assert find_config_conf_near(sub) == (sub / "config_b.conf").resolve()


def test_find_config_conf_falls_back_to_parent(tmp_path: Path) -> None:
    sub = tmp_path / "child"
    sub.mkdir()
    (tmp_path / "config_z.conf").write_text("z")
    assert find_config_conf_near(sub) == (tmp_path / "config_z.conf").resolve()


def test_find_single_buffer_dat_zero_or_many(tmp_path: Path) -> None:
    workdir = tmp_path
    s = tmp_path / "s.dat"
    s.write_text("")
    assert find_single_buffer_dat(str(s), workdir) is None
    (tmp_path / "a_buffer.dat").write_text("")
    assert find_single_buffer_dat(str(s), workdir) == (tmp_path / "a_buffer.dat").resolve()
    (tmp_path / "b_buffer.dat").write_text("")
    assert find_single_buffer_dat(str(s), workdir) is None


def test_profile_guess_from_subtract_output(tmp_path: Path) -> None:
    subdir = tmp_path / "out"
    subdir.mkdir()
    assert profile_guess_from_subtract_output(str(subdir), tmp_path) is None
    (subdir / "z.dat").write_text("")
    (subdir / "a.dat").write_text("")
    g = profile_guess_from_subtract_output(str(subdir), tmp_path)
    assert g is not None
    parts = [p.strip() for p in g.split(",")]
    assert len(parts) == 2
    assert parts[0].endswith("a.dat") and parts[1].endswith("z.dat")


def test_path_expression_paths_fully_exist(tmp_path: Path) -> None:
    f = tmp_path / "a.tif"
    f.write_text("")
    assert path_expression_paths_fully_exist([str(f)], tmp_path) is True
    assert path_expression_paths_fully_exist([str(tmp_path / "missing.tif")], tmp_path) is False


def test_find_mask_near_order(tmp_path: Path) -> None:
    sub = tmp_path / "run"
    sub.mkdir()
    (sub / "mask_b.npy").write_bytes(b"")
    (sub / "mask_a.txt").write_text("")
    assert find_mask_near(sub) == (sub / "mask_a.txt").resolve()
    (sub / "mask.msk").write_text("")
    # txt still sorts before literal mask.msk append order: all txts, all npys, then msk
    assert find_mask_near(sub) == (sub / "mask_a.txt").resolve()


def test_browse_start_dir_for_resolved_paths(tmp_path: Path) -> None:
    f = tmp_path / "x.tif"
    f.write_text("")
    assert browse_start_dir_for_resolved_paths([str(f)], tmp_path) == str(tmp_path.resolve())
    d = tmp_path / "out"
    d.mkdir()
    assert browse_start_dir_for_resolved_paths([str(d)], tmp_path) == str(d.resolve())


def test_session_anchor_dir_from_positional_arg(tmp_path: Path) -> None:
    img = tmp_path / "AgBh.tif"
    img.write_text("")
    assert session_anchor_dir_from_positional_arg(str(img), tmp_path) == str(tmp_path.resolve())
    sub = tmp_path / "d"
    sub.mkdir()
    (sub / "a.dat").write_text("")
    (sub / "b.dat").write_text("")
    expr = f"{sub / 'a.dat'}, {sub / 'b.dat'}"
    assert session_anchor_dir_from_positional_arg(expr, tmp_path) == str(sub.resolve())


def test_plot_profile_reads_global_one_d_profile_dir_hint(tmp_path: Path) -> None:
    """Analysis skills use the same ``one_d_profile_dir`` hint regardless of how it was set."""
    hints = SessionPathHints()
    subdir = tmp_path / "profiles"
    subdir.mkdir()
    hints.one_d_profile_dir = str(subdir.resolve())
    h = session_hint_for_positional_path("plot", "profile", hints, tmp_path)
    assert h == str(subdir.resolve())


def test_subtract_buffer_1d_hint_uses_last_integrated_dat(tmp_path: Path) -> None:
    curve = tmp_path / "run001.dat"
    curve.write_text("")
    hints = SessionPathHints()
    hints.last_integrated_dat_path = str(curve.resolve())
    h = session_hint_for_positional_path("subtract", "buffer_1d", hints, tmp_path)
    assert h == str(curve.resolve())


def test_find_latest_dat_prefers_averaged_over_newer_subtracted(tmp_path: Path) -> None:
    avg = tmp_path / "averaged"
    sub = tmp_path / "subtracted"
    avg.mkdir()
    sub.mkdir()
    integrated = avg / "int_buf.dat"
    integrated.write_text("a")
    newer_sub = sub / "sub_sample.dat"
    newer_sub.write_text("b")
    # Ensure subtracted is newer by mtime.
    import os
    import time

    os.utime(integrated, (time.time() - 10, time.time() - 10))
    os.utime(newer_sub, (time.time(), time.time()))
    assert find_latest_dat_in_workdir(tmp_path) == integrated.resolve()


def test_session_hint_soft_buffer_ignores_applied_buffer_dat(tmp_path: Path) -> None:
    applied = tmp_path / "old_buffer.dat"
    latest = tmp_path / "averaged" / "int_new.dat"
    latest.parent.mkdir()
    applied.write_text("")
    latest.write_text("")
    hints = SessionPathHints()
    hints.buffer_dat_path = str(applied.resolve())
    hints.last_integrated_dat_path = str(latest.resolve())
    assert session_hint_soft_buffer_1d(hints, tmp_path) == str(latest.resolve())
    assert session_hint_for_positional_path("subtract", "buffer_1d", hints, tmp_path) == str(
        applied.resolve()
    )


def test_profile_positional_prefers_preferred_dat_file_over_dir(tmp_path: Path) -> None:
    subdir = tmp_path / "subtracted"
    subdir.mkdir()
    dat = subdir / "curve.dat"
    dat.write_text("")
    hints = SessionPathHints()
    hints.one_d_profile_dir = str(subdir.resolve())
    hints.preferred_profile_dat_path = str(dat.resolve())
    h = session_hint_for_positional_path("fit_distances", "profile", hints, tmp_path)
    assert h == str(dat.resolve())


def test_common_parent_dir_if_all_files(tmp_path: Path) -> None:
    sub = tmp_path / "d"
    sub.mkdir()
    (sub / "a.dat").write_text("")
    (sub / "b.dat").write_text("")
    assert common_parent_dir_if_all_files([str(sub / "a.dat"), str(sub / "b.dat")], tmp_path) == sub.resolve()
    (tmp_path / "other.dat").write_text("")
    assert common_parent_dir_if_all_files([str(sub / "a.dat"), str(tmp_path / "other.dat")], tmp_path) is None


def test_global_hints_updated_after_calibrate_success(tmp_path: Path) -> None:
    hints = SessionPathHints()
    integ = tmp_path / "integrator"
    integ.mkdir()
    for name in ("detector_params.json", "ai_params.json"):
        (integ / name).write_text("{}")
    calib = tmp_path / "AgBh.tif"
    calib.write_text("")
    update_session_hints_from_success(
        hints,
        workdir=tmp_path,
        skill_name="calibrate",
        result={"integrator_dir": str(integ)},
        request=RunRequest(
            skill_name="calibrate",
            positional=[str(calib), "config.conf"],
            options={},
        ),
    )
    assert hints.integrator_dir == str(integ.resolve())
    assert hints.two_d_tif_dir == str(tmp_path.resolve())


def test_global_hints_updated_after_integrate_success(tmp_path: Path) -> None:
    hints = SessionPathHints()
    avg = tmp_path / "averaged"
    avg.mkdir()
    curve = avg / "x.dat"
    curve.write_text("")
    tif = tmp_path / "in.tif"
    tif.write_text("")
    update_session_hints_from_success(
        hints,
        workdir=tmp_path,
        skill_name="integrate",
        result={"integrated_1d": [str(curve)]},
        request=RunRequest(
            skill_name="integrate",
            positional=[str(tif), "integrator"],
            options={"output_dir": "other"},
        ),
    )
    assert hints.integrate_output_dir == str(avg.resolve())
    assert hints.one_d_profile_dir == str(avg.resolve())
    assert hints.two_d_tif_dir == str(tmp_path.resolve())


def test_global_hints_updated_after_subtract_success(tmp_path: Path) -> None:
    hints = SessionPathHints()
    subd = tmp_path / "subtracted"
    subd.mkdir()
    subf = subd / "sub_x.dat"
    subf.write_text("")
    update_session_hints_from_success(
        hints,
        workdir=tmp_path,
        skill_name="subtract",
        result={"subtracted_1d": str(subf)},
        request=RunRequest(skill_name="subtract", positional=[], options={}),
    )
    assert hints.subtract_output_dir == str(subd.resolve())
    assert hints.one_d_profile_dir == str(subd.resolve())


def test_ingest_shared_mask_and_config_from_options(tmp_path: Path) -> None:
    hints = SessionPathHints()
    m = tmp_path / "mask.txt"
    m.write_text("")
    c = tmp_path / "fit.conf"
    c.write_text("")
    req = RunRequest(
        skill_name="integrate_proxy",
        positional=[str(tmp_path / "img.tif")],
        options={"mask": str(m), "config_path": str(c)},
    )
    ingest_shared_mask_and_config_from_request(hints, req, "integrate_proxy", tmp_path)
    assert hints.mask_file_path == str(m.resolve())
    assert hints.config_file_path == str(c.resolve())


def test_ingest_calibrate_positional_config_wins_over_option(tmp_path: Path) -> None:
    hints = SessionPathHints()
    pos_cfg = tmp_path / "pos.conf"
    pos_cfg.write_text("")
    opt_cfg = tmp_path / "opt.conf"
    opt_cfg.write_text("")
    req = RunRequest(
        skill_name="calibrate",
        positional=[str(tmp_path / "img.tif"), str(pos_cfg)],
        options={"config_path": str(opt_cfg)},
    )
    ingest_shared_mask_and_config_from_request(hints, req, "calibrate", tmp_path)
    assert hints.config_file_path == str(pos_cfg.resolve())


def test_session_hint_option_mask_and_config_path(tmp_path: Path) -> None:
    hints = SessionPathHints()
    hints.mask_file_path = "/nonexistent"
    hints.config_file_path = "/also_missing"
    assert session_hint_option_mask(hints, tmp_path) is None
    assert session_hint_option_config_path(hints, tmp_path) is None
    m = tmp_path / "m.npy"
    m.write_text("")
    c = tmp_path / "c.conf"
    c.write_text("")
    hints.mask_file_path = str(m)
    hints.config_file_path = str(c)
    assert session_hint_option_mask(hints, tmp_path) == str(m.resolve())
    assert session_hint_option_config_path(hints, tmp_path) == str(c.resolve())
