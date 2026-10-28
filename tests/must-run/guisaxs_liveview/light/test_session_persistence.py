"""Session persistence for liveview calibration / buffer under watchdir."""

from __future__ import annotations

from pathlib import Path

from guisaxs_skills.liveview.session import (
    LiveviewSessionState,
    load_liveview_session_settings,
    save_liveview_session_settings,
    session_settings_path,
)


def test_save_load_roundtrip_relative_paths(tmp_path: Path) -> None:
    wd = tmp_path / "watch"
    wd.mkdir()
    (wd / "calibration" / "integ").mkdir(parents=True)
    integ = wd / "calibration" / "integ"
    buf = wd / "buffer.dat"
    buf.write_text("0 0\n")
    png = wd / "calibration" / "curve.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")

    s = LiveviewSessionState(watchdir=wd)
    s.integrator_dir = integ
    s.buffer_dat_path = buf
    s.subtract_options = {"method": "point_match", "q_min": 0.01}
    s.buffer_rules = [("*{stem}_sample*", "*{stem}_buffer*"), ("*solo*", "")]
    s.calibration_curve_plot_path = png
    save_liveview_session_settings(s)

    assert session_settings_path(wd).is_file()
    text = session_settings_path(wd).read_text(encoding="utf-8")
    assert "buffer_rules" in text

    s2 = LiveviewSessionState(watchdir=wd)
    assert load_liveview_session_settings(s2) is True
    assert s2.integrator_dir == integ.resolve()
    assert s2.buffer_dat_path == buf.resolve()
    assert s2.subtract_options == {"method": "point_match", "q_min": 0.01}
    assert s2.buffer_rules == [("*{stem}_sample*", "*{stem}_buffer*"), ("*solo*", "")]
    assert s2.calibration_curve_plot_path == png.resolve()
    assert s2.buffer_ready() is True


def test_load_migrates_buffer_path_to_literal_rule(tmp_path: Path) -> None:
    wd = tmp_path / "watch"
    wd.mkdir()
    buf = wd / "my_buffer.dat"
    buf.write_text("0 0\n")
    path = session_settings_path(wd)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "version: 1\n"
        "watch_mode: tree\n"
        "intake_mode: frame_2d\n"
        "buffer_dat_path: my_buffer.dat\n"
        "subtract_options: {q_min: 0.1}\n",
        encoding="utf-8",
    )
    s = LiveviewSessionState(watchdir=wd)
    assert load_liveview_session_settings(s) is True
    assert s.buffer_dat_path == buf.resolve()
    assert s.buffer_rules == [("*", "my_buffer.dat")]
    assert s.buffer_ready() is True


def test_auto_processing_not_persisted(tmp_path: Path) -> None:
    wd = tmp_path / "watch"
    wd.mkdir()
    s = LiveviewSessionState(watchdir=wd)
    s.set_auto_processing(False)
    save_liveview_session_settings(s)

    text = session_settings_path(wd).read_text(encoding="utf-8")
    assert "auto_processing" not in text

    # Stale yaml from older builds must not force Manual on launch.
    path = session_settings_path(wd)
    path.write_text(text + "auto_processing: false\n", encoding="utf-8")

    s2 = LiveviewSessionState(watchdir=wd)
    assert s2.is_auto_processing() is True
    load_liveview_session_settings(s2)
    assert s2.is_auto_processing() is True


def test_load_drops_missing_buffer(tmp_path: Path) -> None:
    wd = tmp_path / "w"
    wd.mkdir()
    s = LiveviewSessionState(watchdir=wd)
    s.buffer_dat_path = None
    s.subtract_options = None
    save_liveview_session_settings(s)

    path = session_settings_path(wd)
    text = path.read_text(encoding="utf-8")
    text = text.replace("buffer_dat_path: null", "buffer_dat_path: missing.dat")
    path.write_text(text, encoding="utf-8")

    s2 = LiveviewSessionState(watchdir=wd)
    load_liveview_session_settings(s2)
    assert s2.buffer_dat_path is None
    assert s2.subtract_options is None
