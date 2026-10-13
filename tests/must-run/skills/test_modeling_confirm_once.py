"""Regression: pipeline Confirm must not re-run modeling after a Confirm already started."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from guisaxs_skills.liveview.modeling_children import ModelingChildManager
from guisaxs_skills.liveview.session.state import LiveviewSessionState
from guisaxs_skills.modeling.context import ModelingContext


class _FakeHandle(QObject):
    """Minimal ModelingChildHandle stand-in that records send_confirm calls."""

    confirmed = pyqtSignal(dict)
    finished_run = pyqtSignal(dict)
    busy_changed = pyqtSignal(bool)
    ready_for_context = pyqtSignal()
    process_exited = pyqtSignal(int)
    ready = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.confirms: List[str] = []
        self._running = True
        self.contexts: List[Any] = []

    def is_running(self) -> bool:
        return bool(self._running)

    def send_confirm(self) -> None:
        self.confirms.append("confirm")

    def send_context(self, ctx: ModelingContext) -> None:
        self.contexts.append(ctx)

    def send_focus(self) -> None:
        return None

    def shutdown(self) -> None:
        self._running = False


def _manager(tmp_path: Path) -> ModelingChildManager:
    state = LiveviewSessionState(watchdir=tmp_path)
    return ModelingChildManager(state=state)


def _attach_fake_shape(mgr: ModelingChildManager, *, sample_id: str, tmp_path: Path) -> _FakeHandle:
    fake = _FakeHandle(mgr)
    mgr._shape = fake  # noqa: SLF001
    mgr._wire_shape_handle(fake)  # noqa: SLF001
    mgr._shape_push = {  # noqa: SLF001
        "profile_path": str(tmp_path / "sub_sample.dat"),
        "gnom_path": str(tmp_path / "datgnom_best.out"),
        "output_root": tmp_path,
        "stem": "sample",
        "sample_id": sample_id,
    }
    return fake


def test_user_confirm_clears_pending_pipeline_confirm(tmp_path: Path):
    """Trace A: pending Confirm + user Confirm must not send_confirm again on idle."""
    mgr = _manager(tmp_path)
    fake = _attach_fake_shape(mgr, sample_id="samp-1", tmp_path=tmp_path)

    # Pipeline asked while child looked busy → pending kept.
    mgr._shape_busy = True  # noqa: SLF001
    mgr.request_confirm_shape()
    assert mgr._shape_confirm_pending_key == "samp-1"  # noqa: SLF001
    assert fake.confirms == []

    # User Confirm starts a run (child emits confirmed) — pending must be consumed.
    fake.confirmed.emit({"mode": "dammif", "options": {"n_runs": 1}})
    assert mgr._shape_confirm_pending_key is None  # noqa: SLF001

    # Finish cycle previously double-flushed; must not send another Confirm.
    fake.busy_changed.emit(False)
    fake.finished_run.emit({"success": True, "result": {}})
    assert fake.confirms == []


def test_pipeline_confirm_skipped_after_same_analysis_confirmed(tmp_path: Path):
    """Trace C: pipeline confirm after user Confirm for the same fingerprint is a no-op."""
    mgr = _manager(tmp_path)
    fake = _attach_fake_shape(mgr, sample_id="samp-2", tmp_path=tmp_path)

    fake.confirmed.emit({"mode": "dammif", "options": {"n_runs": 1}})
    mgr.request_confirm_shape()
    assert fake.confirms == []
    assert mgr._shape_confirm_pending_key is None  # noqa: SLF001


def test_new_analysis_fingerprint_allows_another_confirm(tmp_path: Path):
    """New GNOM path is a new analysis — pipeline Confirm may fire once."""
    mgr = _manager(tmp_path)
    fake = _attach_fake_shape(mgr, sample_id="samp-3", tmp_path=tmp_path)

    fake.confirmed.emit({"mode": "dammif", "options": {"n_runs": 1}})
    assert fake.confirms == []

    # Updated GNOM → different fingerprint → one pipeline Confirm allowed.
    assert mgr._shape_push is not None  # noqa: SLF001
    mgr._shape_push["gnom_path"] = str(tmp_path / "datgnom_refined.out")  # noqa: SLF001
    mgr.request_confirm_shape()
    assert fake.confirms == ["confirm"]


def test_finished_run_does_not_flush_confirm(tmp_path: Path):
    """finished_run alone must not send_confirm (busy=False owns idle flush)."""
    mgr = _manager(tmp_path)
    fake = _attach_fake_shape(mgr, sample_id="samp-4", tmp_path=tmp_path)

    mgr._shape_confirm_pending_key = "samp-4"  # noqa: SLF001
    mgr._shape_busy = False  # noqa: SLF001
    fake.finished_run.emit({"success": True, "result": {}})
    assert fake.confirms == []
    # Pending still there for busy=False to deliver (or confirmed to clear).
    assert mgr._shape_confirm_pending_key == "samp-4"  # noqa: SLF001

    fake.busy_changed.emit(False)
    assert fake.confirms == ["confirm"]
