"""Mesh QThread must not be GC/deleteLater'd while still running.

Regression for liveview abort:
``QThread: Destroyed while thread is still running`` when history restore
loads a DAM CIF and the worker.finished handler dropped the Python owner ref
before ``thread.quit`` (GC during matplotlib redraw → abort).
"""

from __future__ import annotations

import gc

import pytest

pytest.importorskip("PyQt5.QtWidgets")

from PyQt5.QtWidgets import QApplication

from guisaxs_skills.ui.saxs_interactive_3d import SaxsInteractive3DWidget


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_mesh_thread_survives_finished_handler_gc(qapp) -> None:
    """Complete a mesh job, force GC during/after finished, then shutdown cleanly."""
    import contextlib

    w = SaxsInteractive3DWidget(embedded=True)
    try:
        assert w.load_bodies_analytical("sphere", {"radius": 2.0}, title="own")
        # Pump until ownership is released after thread.finished (or timeout).
        for _ in range(400):
            qapp.processEvents()
            thr = w._mesh_thread
            if thr is None:
                break
            try:
                if not thr.isRunning():
                    qapp.processEvents()
            except RuntimeError:
                break
        else:
            pytest.fail("mesh worker did not finish in time")
        # The old bug: early ref-drop + GC here aborted with
        # "QThread: Destroyed while thread is still running".
        for _ in range(5):
            gc.collect()
            qapp.processEvents()
        w.shutdown()
        gc.collect()
        qapp.processEvents()
    finally:
        with contextlib.suppress(Exception):
            w.shutdown()
        w.deleteLater()
        qapp.processEvents()
