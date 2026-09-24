"""Launch B — preprocess / session order (optional exhaustive).

Agent: run when calib/buffer/Stop–Resume/plan_for ordering changed significantly.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import QApplication

from .._scenario_support import install_slot_exception_guard, load_liveview_lib, record_finding

_lib = load_liveview_lib()
globals().update({k: getattr(_lib, k) for k in dir(_lib) if not k.startswith("__")})


def test_order_and_session_scenario():
    install_slot_exception_guard()
    timeout = _gui_timeout_sec()
    q_min, q_max = _subtract_q_window_from_validation_config()
    watchdir = Path(WORKSPACE_ROOT) / "test_liveview_opt_preprocess"
    _rm_tree_contents(watchdir)

    created_app = QApplication.instance() is None
    app = QApplication.instance() or QApplication([])
    from guisaxs_skills.liveview.window import LiveviewMainWindow

    win = LiveviewMainWindow(watchdir=watchdir)
    win.show()
    win.raise_()
    win.activateWindow()
    _process_events(app)

    def note(phase, observed, expected, severity="ux", cause="", fix=""):
        record_finding(
            launch="preprocessing",
            phase=phase,
            observed=observed,
            expected=expected,
            likely_cause=cause,
            candidate_fix=fix,
            severity=severity,
        )

    try:
        left = win._left  # noqa: SLF001
        right = win._right  # noqa: SLF001
        abort = lambda: _window_gone(win)

        _phase("1 calibrate then Stop; drop while Manual; Resume")
        _calibrate_via_ui(app, win, left, timeout=timeout)
        _stop_auto_via_mono(app, right, win)
        assert not win._state.is_auto_processing()  # noqa: SLF001

        buf = Path(VALIDATION_RAW) / "ihs27_buffer.tif"
        sam = Path(VALIDATION_RAW) / "ihs27_95.9_sample.tif"
        _atomic_copy_into_watchdir(buf, watchdir)
        _settle_after_idle(2.0)
        int_buf = watchdir / "averaged" / "int_ihs27_buffer.dat"
        # Manual: auto jobs should not advance — file may sit or queue without running
        if int_buf.is_file():
            note(
                "1 manual still integrated",
                f"{int_buf} appeared while auto_processing=False",
                "Auto jobs held while Manual (Stop); Resume then process",
                severity="ux",
                cause="Executor may still run auto jobs when paused, or Stop only cancels current",
                fix="Confirm LiveviewJobExecutor paused path blocks auto enqueue/start",
            )
        _resume_auto_via_mono(app, right, win)
        ok_buf = _wait_until(
            app,
            lambda: int_buf.is_file() and int_buf.stat().st_size > 0,
            timeout,
            abort_if=abort,
        )
        assert ok_buf and not abort(), "Buffer did not integrate after Resume"
        assert _wait_until_queue_idle(app, win, timeout)

        _phase("2 set buffer after sample already integrated without buffer")
        # Integrate sample without buffer first
        _atomic_copy_into_watchdir(sam, watchdir)
        int_sam = watchdir / "averaged" / "int_ihs27_95.9_sample.dat"
        ok_sam = _wait_until(
            app,
            lambda: int_sam.is_file() and int_sam.stat().st_size > 0,
            timeout,
            abort_if=abort,
        )
        assert ok_sam
        assert _wait_until_queue_idle(app, win, timeout)
        sub_before = watchdir / "subtracted" / "sub_ihs27_95.9_sample.dat"
        had_sub_before = sub_before.is_file()
        _set_buffer_and_subtract_options(
            app, win, left, int_buf=int_buf, q_min=q_min, q_max=q_max, timeout=timeout
        )
        _settle_after_idle(2.0)
        # Finished job should not be forced to re-subtract
        if not had_sub_before and sub_before.is_file():
            # Could be Process or replan — note if unexpected automatic
            note(
                "2 retroactive subtract",
                f"{sub_before} appeared after buffer set without new sample drop",
                "Buffer changes apply to subsequent auto jobs only (spec §4.4 / §9)",
                severity="ux",
                cause="Possible replan of current/history sample on buffer set",
                fix="Do not replan completed samples on set_buffer unless user Process",
            )
        # New sample should subtract
        sam28 = Path(VALIDATION_RAW) / "ihs28_95.2_sample.tif"
        buf28 = Path(VALIDATION_RAW) / "ihs28_buffer.tif"
        _atomic_copy_into_watchdir(buf28, watchdir)
        int_buf28 = watchdir / "averaged" / "int_ihs28_buffer.dat"
        _wait_until(app, lambda: int_buf28.is_file(), timeout, abort_if=abort)
        assert _wait_until_queue_idle(app, win, timeout)
        # Update buffer to ihs28 for pairing clarity
        _set_buffer_and_subtract_options(
            app, win, left, int_buf=int_buf28, q_min=q_min, q_max=q_max, timeout=timeout
        )
        _atomic_copy_into_watchdir(sam28, watchdir)
        sub28 = watchdir / "subtracted" / "sub_ihs28_95.2_sample.dat"
        ok_sub = _wait_until(
            app,
            lambda: sub28.is_file() and sub28.stat().st_size > 0,
            timeout,
            abort_if=abort,
        )
        assert ok_sub and not abort(), "New sample did not subtract with buffer set"
        assert _wait_until_queue_idle(app, win, timeout)

        _phase("3 buffer reset disarms analysis")
        _arm_monodisperse(right)
        assert win._state.monodisperse_armed  # noqa: SLF001
        _reset_buffer_via_ui(app, win, left, timeout)
        assert not win._state.monodisperse_armed  # noqa: SLF001

        _phase("4 sample before buffer with mono armed")
        _set_buffer_and_subtract_options(
            app, win, left, int_buf=int_buf28, q_min=q_min, q_max=q_max, timeout=timeout
        )
        # Clear buffer again then arm then drop sample without buffer
        _reset_buffer_via_ui(app, win, left, timeout)
        _arm_monodisperse(right)
        _configure_monodisperse_shape(app, right, win, enable_dammif=False)
        sam29 = Path(VALIDATION_RAW) / "ihs29_94.6_sample.tif"
        _atomic_copy_into_watchdir(sam29, watchdir)
        int29 = watchdir / "averaged" / "int_ihs29_94.6_sample.dat"
        ok29 = _wait_until(
            app,
            lambda: int29.is_file() and int29.stat().st_size > 0,
            timeout,
            abort_if=abort,
        )
        assert ok29 and not abort(), "Sample before buffer crashed or hung"
        assert _wait_until_queue_idle(app, win, timeout)
        _dismiss_modal_dialogs(app)

        _phase("5 two samples queued quickly (FIFO)")
        _set_buffer_and_subtract_options(
            app, win, left, int_buf=int_buf, q_min=q_min, q_max=q_max, timeout=timeout
        )
        _arm_monodisperse(right)
        _configure_monodisperse_shape(app, right, win, enable_dammif=False)
        # Re-drop ihs27 and ihs28 samples rapidly
        _atomic_copy_into_watchdir(sam, watchdir)
        _atomic_copy_into_watchdir(sam28, watchdir)
        assert _wait_until_queue_idle(app, win, timeout)
        assert not abort()

        _phase("6 open calib/buffer wizards and cancel")
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest

        snap_before = _snapshot_session(win)
        QTest.mouseClick(left._cal_open, Qt.LeftButton)  # noqa: SLF001
        wiz = left._cal_wizard  # noqa: SLF001
        assert wiz is not None
        wiz.close()
        _wait_until(app, lambda: not wiz.isVisible(), 3.0)
        QTest.mouseClick(left._buf_open, Qt.LeftButton)  # noqa: SLF001
        bw = left._buf_wizard  # noqa: SLF001
        assert bw is not None
        bw.close()
        _wait_until(app, lambda: not bw.isVisible(), 3.0)
        snap_after = _snapshot_session(win)
        if snap_before["calibrated"] != snap_after["calibrated"] or (
            snap_before["buffer_ready"] != snap_after["buffer_ready"]
        ):
            note(
                "6 cancel wizards mutated session",
                f"before={snap_before} after={snap_after}",
                "Cancel without apply leaves session facts unchanged",
                severity="ux",
                cause="Wizard close may apply or clear facts",
                fix="Close-without-Apply must not mutate session",
            )
        print(f"preprocess snapshot: {snap_after}", flush=True)

    finally:
        _dismiss_modal_dialogs(app)
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
