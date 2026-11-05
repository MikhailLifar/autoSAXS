"""Soft-wait buffer matching: park 0-hit samples; rematch without hard hold."""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt5.QtWidgets import QApplication

from autosaxs.core.buffer_rules import DEFAULT_BUFFER_RULES, MatchKind
from guisaxs_skills.liveview.ingest.stability import FileStatSnapshot
from guisaxs_skills.liveview.pipeline import LiveviewJobExecutor, LiveviewQueueStatus
from guisaxs_skills.liveview.pipeline.plan import BufferAwaiting, plan_for
from guisaxs_skills.liveview.pipeline.queue import QueueItem
from guisaxs_skills.liveview.session.sample import Sample
from guisaxs_skills.liveview.session.sample_store import SampleStore
from guisaxs_skills.liveview.session.state import LiveviewIntakeMode, LiveviewSessionState
from guisaxs_skills.liveview.session.watchdir_config import seed_root_config_conf
from guisaxs_skills.liveview.ui.panels.middle import LiveviewMiddlePanel


@pytest.fixture
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class _DummySignal:
    def connect(self, _fn):
        return None

    def emit(self, *args, **kwargs):
        return None


class _DummyRunner:
    def __init__(self):
        self.finished = _DummySignal()

    def is_running(self) -> bool:
        return False

    def cancel(self) -> None:
        return None

    def start(self, _req) -> None:
        return None


def _buffer_ready_state(tmp_path: Path) -> LiveviewSessionState:
    seed_root_config_conf(tmp_path)
    state = LiveviewSessionState(watchdir=tmp_path)
    state.root_rules = tuple(DEFAULT_BUFFER_RULES)
    state.subtract_options = {"q_min": 0.01}
    state.intake_mode = LiveviewIntakeMode.CURVE_1D
    state.set_auto_processing(True)
    return state


def _queue_item(path: Path) -> QueueItem:
    return QueueItem(
        path=str(path.resolve()),
        detected_at_monotonic=0.0,
        observed_stat=FileStatSnapshot(size=path.stat().st_size, mtime_ns=0),
    )


def test_plan_for_raises_buffer_awaiting_on_zero_hits(tmp_path: Path) -> None:
    state = _buffer_ready_state(tmp_path)
    sample_path = tmp_path / "prot_sample.dat"
    sample_path.write_text("0 1 0\n", encoding="utf-8")
    sample = Sample.from_path(str(sample_path), boarding=LiveviewIntakeMode.CURVE_1D)
    try:
        plan_for(state, sample, load_yaml=lambda _p: {})
        raise AssertionError("expected BufferAwaiting")
    except BufferAwaiting as exc:
        assert exc.result.kind == MatchKind.AWAITING_BUFFER


def test_promote_parks_awaiting_without_dispatch_hold(tmp_path: Path) -> None:
    state = _buffer_ready_state(tmp_path)
    sample_path = tmp_path / "prot_sample.dat"
    sample_path.write_text("0 1 0\n", encoding="utf-8")
    other = tmp_path / "other_sample.dat"
    other.write_text("0 1 0\n", encoding="utf-8")
    # Buffer for *other* so that sample remains 0-hit awaiting; other pairs.
    (tmp_path / "other_buffer.dat").write_text("0 1 0\n", encoding="utf-8")

    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=_DummyRunner())  # type: ignore[arg-type]
    with ex._incoming_lock:  # noqa: SLF001
        ex._incoming.put(_queue_item(sample_path))  # noqa: SLF001
        ex._incoming.put(_queue_item(other))  # noqa: SLF001

    ex._promote_incoming_to_jobs()  # noqa: SLF001

    key = str(sample_path.resolve())
    assert key in ex._awaiting_buffer  # noqa: SLF001
    assert ex.awaiting_buffer_count == 1
    assert not ex.dispatch_hold
    # Paired *other* became a job; awaiting sample did not block promote.
    assert len(ex._jobs) == 1  # noqa: SLF001
    job = ex._jobs.get_nowait()  # noqa: SLF001
    assert job is not None
    assert "other_sample" in str(job.context.get("source_path") or "")


def test_rematch_releases_fifo_when_buffer_appears(tmp_path: Path) -> None:
    state = _buffer_ready_state(tmp_path)
    first = tmp_path / "alpha_sample.dat"
    second = tmp_path / "beta_sample.dat"
    first.write_text("0 1 0\n", encoding="utf-8")
    second.write_text("0 1 0\n", encoding="utf-8")

    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=_DummyRunner())  # type: ignore[arg-type]
    with ex._incoming_lock:  # noqa: SLF001
        ex._incoming.put(_queue_item(first))  # noqa: SLF001
        ex._incoming.put(_queue_item(second))  # noqa: SLF001
    ex._promote_incoming_to_jobs()  # noqa: SLF001
    assert ex.awaiting_buffer_count == 2

    (tmp_path / "alpha_buffer.dat").write_text("0 1 0\n", encoding="utf-8")
    (tmp_path / "beta_buffer.dat").write_text("0 1 0\n", encoding="utf-8")
    ex.rematch_awaiting_buffers()

    assert ex.awaiting_buffer_count == 0
    with ex._incoming_lock:  # noqa: SLF001
        a = ex._incoming.get_nowait()  # noqa: SLF001
        b = ex._incoming.get_nowait()  # noqa: SLF001
        c = ex._incoming.get_nowait()  # noqa: SLF001
    assert a is not None and Path(a.path).name == "alpha_sample.dat"
    assert b is not None and Path(b.path).name == "beta_sample.dat"
    assert c is None


def test_hard_conflict_still_holds(tmp_path: Path) -> None:
    state = _buffer_ready_state(tmp_path)
    write = tmp_path / "config.conf"
    write.write_text(
        'buffer_rules:\n  "*a*_sample*": "*a*_buffer*"\n  "*b*_sample*": "*b*_buffer*"\n',
        encoding="utf-8",
    )
    state.root_rules = (("*a*_sample*", "*a*_buffer*"), ("*b*_sample*", "*b*_buffer*"))
    sample_path = tmp_path / "ab_sample.dat"
    sample_path.write_text("0 1 0\n", encoding="utf-8")
    (tmp_path / "ab_buffer.dat").write_text("0 1 0\n", encoding="utf-8")

    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=_DummyRunner())  # type: ignore[arg-type]
    with ex._incoming_lock:  # noqa: SLF001
        ex._incoming.put(_queue_item(sample_path))  # noqa: SLF001
    ex._promote_incoming_to_jobs()  # noqa: SLF001

    assert ex.dispatch_hold
    assert ex.awaiting_buffer_count == 0


def test_middle_status_idle_with_waiters(qapp, tmp_path: Path) -> None:
    panel = LiveviewMiddlePanel()
    panel.set_queue_status(
        LiveviewQueueStatus(
            queue_size=0,
            current_path="",
            last_processed_path="",
            avg_seconds_per_item=0.0,
            remaining=0,
            awaiting_buffer_count=2,
        )
    )
    assert panel._status_line.text() == "Not all integrated. Waiting for buffer..."  # noqa: SLF001
    assert panel._queue_bar.minimum() == 0 and panel._queue_bar.maximum() == 0  # noqa: SLF001
