"""ModelingChildManager Confirm protocol: open+Auto gate, pending only while busy."""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from guisaxs_skills.liveview.modeling_children import ModelingChildManager
from guisaxs_skills.liveview.session.state import LiveviewSessionState
from guisaxs_skills.modeling.context import ModelingContext


class _FakeHandle(QObject):
    """Minimal ModelingChildHandle stand-in that records send_confirm / send_context order."""

    confirmed = pyqtSignal(dict)
    finished_run = pyqtSignal(dict)
    busy_changed = pyqtSignal(bool)
    auto_changed = pyqtSignal(bool)
    ready_for_context = pyqtSignal()
    process_exited = pyqtSignal(int)
    ready = pyqtSignal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.confirms: List[str] = []
        self._running = True
        self.contexts: List[Any] = []
        self.events: List[Any] = []

    def is_running(self) -> bool:
        return bool(self._running)

    def send_confirm(self) -> None:
        self.confirms.append("confirm")
        self.events.append("confirm")

    def send_context(self, ctx: ModelingContext) -> None:
        self.contexts.append(ctx)
        self.events.append(("context", str(getattr(ctx, "sample_id", "") or "")))

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


def test_case1_closed_child_ignores_confirm(tmp_path: Path):
    """Child closed / not running → ignore (no pending)."""
    mgr = _manager(tmp_path)
    mgr.request_confirm_shape()
    assert mgr._shape_confirm_pending is False  # noqa: SLF001

    fake = _attach_fake_shape(mgr, sample_id="samp-1", tmp_path=tmp_path)
    fake._running = False
    fake.auto_changed.emit(True)
    mgr.request_confirm_shape()
    assert fake.confirms == []
    assert mgr._shape_confirm_pending is False  # noqa: SLF001


def test_case2_open_manual_ignores_confirm(tmp_path: Path):
    """Open + Auto off → ignore (no pending)."""
    mgr = _manager(tmp_path)
    fake = _attach_fake_shape(mgr, sample_id="samp-2", tmp_path=tmp_path)
    assert mgr._shape_auto is False  # noqa: SLF001

    mgr.request_confirm_shape()
    assert fake.confirms == []
    assert mgr._shape_confirm_pending is False  # noqa: SLF001


def test_case3_open_auto_idle_sends_immediately(tmp_path: Path):
    """Open + Auto on + idle → send_confirm() immediately."""
    mgr = _manager(tmp_path)
    fake = _attach_fake_shape(mgr, sample_id="samp-3", tmp_path=tmp_path)
    fake.auto_changed.emit(True)

    mgr.request_confirm_shape()
    assert fake.confirms == ["confirm"]
    assert mgr._shape_confirm_pending is False  # noqa: SLF001


def test_case4_open_auto_busy_defers_then_flushes_once(tmp_path: Path):
    """Open + Auto on + busy → pending; busy=False flushes once; finished_run does not."""
    mgr = _manager(tmp_path)
    fake = _attach_fake_shape(mgr, sample_id="samp-4", tmp_path=tmp_path)
    fake.auto_changed.emit(True)
    fake.busy_changed.emit(True)

    mgr.request_confirm_shape()
    mgr.request_confirm_shape()  # latest wins — still one slot
    assert fake.confirms == []
    assert mgr._shape_confirm_pending is True  # noqa: SLF001

    fake.finished_run.emit({"success": True, "result": {}})
    assert fake.confirms == []
    assert mgr._shape_confirm_pending is True  # noqa: SLF001

    fake.busy_changed.emit(False)
    assert fake.confirms == ["confirm"]
    assert mgr._shape_confirm_pending is False  # noqa: SLF001

    # Second idle flush must not re-send.
    fake.busy_changed.emit(False)
    assert fake.confirms == ["confirm"]


def test_discard_pending_on_manual(tmp_path: Path):
    """Auto → Manual discards pending; idle flush must not send."""
    mgr = _manager(tmp_path)
    fake = _attach_fake_shape(mgr, sample_id="samp-5", tmp_path=tmp_path)
    fake.auto_changed.emit(True)
    fake.busy_changed.emit(True)
    mgr.request_confirm_shape()
    assert mgr._shape_confirm_pending is True  # noqa: SLF001

    fake.auto_changed.emit(False)
    assert mgr._shape_confirm_pending is False  # noqa: SLF001
    assert mgr._shape_auto is False  # noqa: SLF001

    fake.busy_changed.emit(False)
    assert fake.confirms == []


def test_discard_pending_on_close(tmp_path: Path):
    """Child exit discards pending; no flush-on-ready / pending-when-closed."""
    mgr = _manager(tmp_path)
    fake = _attach_fake_shape(mgr, sample_id="samp-6", tmp_path=tmp_path)
    fake.auto_changed.emit(True)
    fake.busy_changed.emit(True)
    mgr.request_confirm_shape()
    assert mgr._shape_confirm_pending is True  # noqa: SLF001

    fake.process_exited.emit(0)
    assert mgr._shape is None  # noqa: SLF001
    assert mgr._shape_confirm_pending is False  # noqa: SLF001
    assert mgr._shape_auto is False  # noqa: SLF001
    assert fake.confirms == []


def test_flush_applies_latest_context_before_confirm(tmp_path: Path):
    """Busy→idle pending flush: set_context (latest push) then Confirm — never Confirm on stale sample."""
    mgr = _manager(tmp_path)
    fake = _attach_fake_shape(mgr, sample_id="file1", tmp_path=tmp_path)
    fake.auto_changed.emit(True)
    fake.busy_changed.emit(True)

    # Liveview moved to a later sample while shape was still busy on file1.
    mgr._shape_push = {  # noqa: SLF001
        "profile_path": str(tmp_path / "sub_file2.dat"),
        "gnom_path": str(tmp_path / "datgnom_best.out"),
        "output_root": tmp_path,
        "stem": "file2",
        "sample_id": "file2",
    }
    mgr.request_confirm_shape()
    assert fake.confirms == []
    assert fake.events == []
    assert mgr._shape_confirm_pending is True  # noqa: SLF001

    fake.busy_changed.emit(False)
    assert fake.confirms == ["confirm"]
    assert mgr._shape_confirm_pending is False  # noqa: SLF001
    assert len(fake.contexts) == 1
    assert str(fake.contexts[0].sample_id) == "file2"
    assert fake.events == [("context", "file2"), "confirm"]
