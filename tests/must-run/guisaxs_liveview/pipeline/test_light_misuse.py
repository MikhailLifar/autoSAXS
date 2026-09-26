"""Commit-gate liveview light misuse cases (non-exhaustive)."""

from __future__ import annotations

import importlib.util
from pathlib import Path as _Path


def _load_liveview_lib():
    _lib_path = _Path(__file__).resolve().parents[1] / "_lib.py"
    _spec = importlib.util.spec_from_file_location("liveview_test_lib", _lib_path)
    assert _spec and _spec.loader
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    return _mod


_lib = _load_liveview_lib()
globals().update({k: getattr(_lib, k) for k in dir(_lib) if not k.startswith("__")})

from pathlib import Path

from PyQt5.QtWidgets import QApplication


def test_non_tif_in_watchdir_does_not_crash():
    """Junk files in watchdir must not take down the app or hang the queue."""
    timeout = min(60.0, _gui_timeout_sec())
    watchdir = _test_watchdir("test_liveview_misuse")
    _rm_tree_contents(watchdir)

    created_app = QApplication.instance() is None
    app = QApplication.instance() or QApplication([])
    from guisaxs_skills.liveview.window import LiveviewMainWindow

    win = LiveviewMainWindow(watchdir=watchdir)
    win.show()
    _process_events(app)
    try:
        junk = watchdir / "readme.txt"
        junk.write_text("not a saxs frame\n", encoding="utf-8")
        _settle_after_idle(1.0)
        assert not _window_gone(win)
        assert _wait_until_queue_idle(app, win, timeout)
        averaged = watchdir / "averaged"
        assert (not averaged.exists()) or (not any(averaged.glob("int_*.dat")))
    finally:
        try:
            win._controller.shutdown()  # noqa: SLF001
        except Exception:
            pass
        try:
            win.close()
        except Exception:
            pass
        if created_app:
            try:
                app.quit()
            except Exception:
                pass


def test_arm_monodisperse_without_buffer_does_not_crash():
    """Opening monodisperse before buffer is set must be safe."""
    timeout = min(60.0, _gui_timeout_sec())
    watchdir = _test_watchdir("test_liveview_misuse2")
    _rm_tree_contents(watchdir)

    created_app = QApplication.instance() is None
    app = QApplication.instance() or QApplication([])
    from guisaxs_skills.liveview.window import LiveviewMainWindow

    win = LiveviewMainWindow(watchdir=watchdir)
    win.show()
    _process_events(app)
    try:
        right = win._right  # noqa: SLF001
        right.show_monodisperse_wizard()
        _process_events(app)
        assert win._state.monodisperse_armed  # noqa: SLF001
        assert not _window_gone(win)
        assert _wait_until(app, lambda: right.monodisperse_wizard is not None, timeout)
    finally:
        try:
            win._controller.shutdown()  # noqa: SLF001
        except Exception:
            pass
        try:
            win.close()
        except Exception:
            pass
        if created_app:
            try:
                app.quit()
            except Exception:
                pass


def test_validation_dir_present_for_pipeline_fixtures():
    """Misuse suite still assumes validation/ exists for related pipeline tests."""
    assert Path(VALIDATION_DIR).is_dir(), VALIDATION_DIR
