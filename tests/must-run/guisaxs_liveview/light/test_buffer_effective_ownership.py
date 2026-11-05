"""Effective buffer ownership: coach skips Set buffer; wizard uses resolved path."""

from __future__ import annotations

from pathlib import Path

from autosaxs.core.buffer_rules import MatchKind
from guisaxs_skills.liveview.pipeline.buffer_match import (
    effective_buffer_path_for_sample,
    resolve_liveview_match,
)
from guisaxs_skills.liveview.session.api import LiveviewSession
from guisaxs_skills.liveview.session.sample import Sample
from guisaxs_skills.liveview.session.state import LiveviewIntakeMode
from guisaxs_skills.liveview.session.watchdir_config import write_config_text


def _paired_watch(tmp_path: Path) -> tuple[LiveviewSession, Sample, Path]:
    wd = tmp_path / "watch"
    wd.mkdir()
    averaged = wd / "averaged"
    averaged.mkdir()
    # Rules claim int_*_sample.dat → int_*_buffer.dat (basename match).
    write_config_text(
        wd / "config.conf",
        "buffer_rules:\n"
        '  "*{stem}_sample*": "*{stem}_buffer*"\n',
    )
    buf = averaged / "int_ihs29_buffer.dat"
    buf.write_text("0.1 1.0 0.01\n", encoding="utf-8")
    (averaged / "int_ihs29_sample.dat").write_text("0.1 2.0 0.01\n", encoding="utf-8")
    tiff = wd / "ihs29_sample.tif"
    tiff.write_bytes(b"II*\x00")
    session = LiveviewSession(watchdir=wd, load_settings=True)
    session.state.integrator_dir = wd / "integ"
    session.state.integrator_dir.mkdir()
    # No manual Set buffer — buffer_dat_path stays unset.
    assert session.state.buffer_dat_path is None
    assert session.state.buffer_ready() is True
    sample = Sample.from_path(str(tiff), boarding=LiveviewIntakeMode.FRAME_2D)
    return session, sample, buf


def test_liveview_match_pairs_via_planned_integrated_curve(tmp_path: Path) -> None:
    session, sample, buf = _paired_watch(tmp_path)
    match = resolve_liveview_match(session.state, sample)
    assert match.kind == MatchKind.PAIRED
    assert Path(match.buffer).resolve() == buf.resolve()
    # Source TIFF alone would still pair under these rules, but the important
    # contract is: effective path exists without buffer_dat_path hint.
    assert session.state.buffer_dat_path is None
    assert effective_buffer_path_for_sample(session.state, sample) == buf.resolve()


def test_unmatched_buffer_tiff_is_not_paired(tmp_path: Path) -> None:
    """Buffer TIFFs stay UNMATCHED under sample→buffer rules (no false PAIRED)."""
    session, _sample, _buf = _paired_watch(tmp_path)
    buffer_tiff = session.state.watchdir / "ihs29_buffer.tif"
    buffer_tiff.write_bytes(b"II*\x00")
    sample = Sample.from_path(str(buffer_tiff), boarding=LiveviewIntakeMode.FRAME_2D)
    assert resolve_liveview_match(session.state, sample).kind == MatchKind.UNMATCHED
    assert effective_buffer_path_for_sample(session.state, sample) is None


def test_coach_skips_set_buffer_when_none_rule(tmp_path: Path) -> None:
    wd = tmp_path / "watch"
    wd.mkdir()
    write_config_text(
        wd / "config.conf",
        "buffer_rules:\n"
        '  "*calibrant*": ""\n'
        '  "*{stem}_sample*": "*{stem}_buffer*"\n',
    )
    session = LiveviewSession(watchdir=wd, load_settings=True)
    cal = wd / "AgBh_calibrant.tif"
    cal.write_bytes(b"II*\x00")
    sample = Sample.from_path(str(cal), boarding=LiveviewIntakeMode.FRAME_2D)
    assert resolve_liveview_match(session.state, sample).kind == MatchKind.NONE
    assert effective_buffer_path_for_sample(session.state, sample) is None


def test_wizard_gate_accepts_effective_buffer_without_hint(tmp_path: Path) -> None:
    """Subtraction wizard must not require last-picked buffer_dat_path."""
    session, sample, buf = _paired_watch(tmp_path)
    assert session.state.buffer_dat_path is None
    effective = effective_buffer_path_for_sample(session.state, sample)
    assert effective is not None
    assert effective == buf.resolve()
    # Same predicate window._open_subtraction_wizard uses after the fix.
    has_buffer = session.state.buffer_ready() and effective is not None
    assert has_buffer is True


def test_dual_probe_pairs_integrated_when_tiff_basename_unmatched(tmp_path: Path) -> None:
    """Rules on int_*.dat must cover TIFF samples for wizard (plan order)."""
    from guisaxs_skills.liveview.pipeline.buffer_match import resolve_match_for_sample

    wd = tmp_path / "watch"
    wd.mkdir()
    averaged = wd / "averaged"
    averaged.mkdir()
    write_config_text(
        wd / "config.conf",
        "buffer_rules:\n"
        '  "int_{stem}.dat": "int_{stem}_buffer.dat"\n',
    )
    buf = averaged / "int_ihs29_buffer.dat"
    buf.write_text("0.1 1.0 0.01\n", encoding="utf-8")
    (averaged / "int_ihs29.dat").write_text("0.1 2.0 0.01\n", encoding="utf-8")
    tiff = wd / "ihs29.tif"
    tiff.write_bytes(b"II*\x00")
    session = LiveviewSession(watchdir=wd, load_settings=True)
    session.state.integrator_dir = wd / "integ"
    session.state.integrator_dir.mkdir()
    sample = Sample.from_path(str(tiff), boarding=LiveviewIntakeMode.FRAME_2D)

    source_only = resolve_match_for_sample(
        session.state, sample, sample_curve=sample.path
    )
    assert source_only.kind == MatchKind.UNMATCHED

    match = resolve_liveview_match(session.state, sample)
    assert match.kind == MatchKind.PAIRED
    assert Path(match.buffer).resolve() == buf.resolve()
    assert effective_buffer_path_for_sample(session.state, sample) == buf.resolve()


def test_left_coach_never_targets_set_buffer_button(tmp_path: Path) -> None:
    """After Configuration, coach must not pulse Set buffer for PAIRED or UNMATCHED."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication, QPushButton

    app = QApplication.instance() or QApplication([])

    from guisaxs_skills.liveview.session.sample_store import SampleStore
    from guisaxs_skills.liveview.ui.panels.left import LiveviewLeftPanel

    session, paired, _buf = _paired_watch(tmp_path)
    session.state.configuration_opened_once = True
    store = SampleStore()
    store.append_history(paired)

    panel = LiveviewLeftPanel(session=session)
    panel.bind_sample_store(store)
    panel.set_coach_peers(middle=None, right=None)
    panel.refresh_attention_coach()
    targets = list(panel._attention._targets)  # noqa: SLF001
    assert not any(
        isinstance(t, QPushButton) and t.text() == "Set buffer" for t in targets
    ), targets

    buffer_tiff = session.state.watchdir / "ihs29_buffer.tif"
    buffer_tiff.write_bytes(b"II*\x00")
    store.clear()
    store.append_history(
        Sample.from_path(str(buffer_tiff), boarding=LiveviewIntakeMode.FRAME_2D)
    )
    panel.refresh_attention_coach()
    targets = list(panel._attention._targets)  # noqa: SLF001
    assert not any(
        isinstance(t, QPushButton) and t.text() == "Set buffer" for t in targets
    ), targets

    app.processEvents()
