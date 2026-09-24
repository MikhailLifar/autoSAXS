"""Launch C — monodisperse arming / Adjust / history (optional exhaustive).

Agent: run when mono analysis arming, Adjust GNOM, or history presenters changed significantly.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import QApplication

from .._scenario_support import install_slot_exception_guard, load_liveview_lib, record_finding

_lib = load_liveview_lib()
globals().update({k: getattr(_lib, k) for k in dir(_lib) if not k.startswith("__")})


def test_mono_arming_and_adjust_scenario():
    install_slot_exception_guard()
    timeout = _gui_timeout_sec()
    q_min, q_max = _subtract_q_window_from_validation_config()
    watchdir = Path(WORKSPACE_ROOT) / "test_liveview_opt_analysis"
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
            launch="analysis",
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
        mid = win._middle  # noqa: SLF001
        abort = lambda: _window_gone(win)

        buf = Path(VALIDATION_RAW) / "ihs27_buffer.tif"
        sam = Path(VALIDATION_RAW) / "ihs27_95.9_sample.tif"
        token = "ihs27_95.9_sample"

        _phase("1 calib+buffer+pair without mono armed")
        _calibrate_via_ui(app, win, left, timeout=timeout)
        _atomic_copy_into_watchdir(buf, watchdir)
        int_buf = watchdir / "averaged" / "int_ihs27_buffer.dat"
        assert _wait_until(app, lambda: int_buf.is_file(), timeout, abort_if=abort)
        assert _wait_until_queue_idle(app, win, timeout)
        _set_buffer_and_subtract_options(
            app, win, left, int_buf=int_buf, q_min=q_min, q_max=q_max, timeout=timeout
        )
        # Ensure mono closed/disarmed
        _close_monodisperse(app, right, win)
        assert not win._state.monodisperse_armed  # noqa: SLF001
        _atomic_copy_into_watchdir(sam, watchdir)
        sub = watchdir / "subtracted" / f"sub_{token}.dat"
        assert _wait_until(
            app,
            lambda: sub.is_file() and sub.stat().st_size > 0,
            timeout,
            abort_if=abort,
        )
        assert _wait_until_queue_idle(app, win, timeout)
        g_dir = watchdir / "guinier_mono" / token
        fd_dir = watchdir / "fit_distances" / token
        if g_dir.exists() or fd_dir.exists():
            note(
                "1 analysis without arm",
                f"guinier={g_dir.exists()} fit_distances={fd_dir.exists()}",
                "No Guinier/GNOM dirs when mono disarmed",
                severity="wrong_science",
                cause="analysis_enabled true without arming, or sticky arm from prior session",
                fix="Cold start and closed mono window must keep analysis_enabled false",
            )

        _phase("2 arm mono after subtract; Process / replan")
        _arm_monodisperse(right)
        _configure_monodisperse_shape(app, right, win, enable_dammif=False)
        # Trigger Process on current history sample
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest

        if mid._btn_process.isEnabled():  # noqa: SLF001
            QTest.mouseClick(mid._btn_process, Qt.LeftButton)  # noqa: SLF001
        _wait_mono_analysis_artifacts(
            app, win, watchdir, sample_token=token, expect_dam=False, timeout=timeout
        )
        assert _wait_until_queue_idle(app, win, timeout)

        _phase("3 arm before sample drop (ihs28)")
        buf28 = Path(VALIDATION_RAW) / "ihs28_buffer.tif"
        sam28 = Path(VALIDATION_RAW) / "ihs28_95.2_sample.tif"
        token28 = "ihs28_95.2_sample"
        _reset_buffer_via_ui(app, win, left, timeout)
        _atomic_copy_into_watchdir(buf28, watchdir)
        int_buf28 = watchdir / "averaged" / "int_ihs28_buffer.dat"
        assert _wait_until(app, lambda: int_buf28.is_file(), timeout, abort_if=abort)
        assert _wait_until_queue_idle(app, win, timeout)
        _set_buffer_and_subtract_options(
            app, win, left, int_buf=int_buf28, q_min=q_min, q_max=q_max, timeout=timeout
        )
        _arm_monodisperse(right)
        _configure_monodisperse_shape(app, right, win, enable_dammif=False)
        _atomic_copy_into_watchdir(sam28, watchdir)
        _wait_mono_analysis_artifacts(
            app, win, watchdir, sample_token=token28, expect_dam=False, timeout=timeout
        )
        assert _wait_until_queue_idle(app, win, timeout)

        _phase("4 Adjust GNOM refine for ihs27 if knobs exist")
        # Navigate history to ihs27 if needed then Adjust
        refine = _load_mono_refine_params("ihs27")
        if refine is not None:
            # Select ihs27 via history if possible
            for _ in range(5):
                label = (mid._history_label.text() or "")  # noqa: SLF001
                if token in label or "ihs27" in label:
                    break
                if mid._btn_hist_prev.isEnabled():  # noqa: SLF001
                    _history_step(app, mid, -1)
                else:
                    break
            _adjust_gnom_via_ui(app, win, right, params=refine, timeout=timeout)
            _wait_mono_analysis_artifacts(
                app, win, watchdir, sample_token=token, expect_dam=False, timeout=timeout
            )
            assert _wait_until_queue_idle(app, win, timeout)
        else:
            note(
                "4 skip adjust",
                "No refine knobs in reference_mono for ihs27",
                "Adjust when refine knobs exist",
                severity="none",
            )

        _phase("5 buffer reset while mono open → disarm")
        _arm_monodisperse(right)
        assert win._state.monodisperse_armed  # noqa: SLF001
        _reset_buffer_via_ui(app, win, left, timeout)
        assert not win._state.monodisperse_armed  # noqa: SLF001

        _phase("6 history nav current-sample presenters")
        # Restore buffer and ensure two samples in history
        _set_buffer_and_subtract_options(
            app, win, left, int_buf=int_buf28, q_min=q_min, q_max=q_max, timeout=timeout
        )
        _arm_monodisperse(right)
        # Step history between samples
        if mid._btn_hist_prev.isEnabled():  # noqa: SLF001
            label_a = mid._history_label.text()  # noqa: SLF001
            _history_step(app, mid, -1)
            label_b = mid._history_label.text()  # noqa: SLF001
            if label_a == label_b:
                note(
                    "6 history label unchanged",
                    f"label stayed {label_a!r}",
                    "History < changes current sample label",
                    severity="ux",
                    cause="History nav not wired or single sample",
                    fix="Ensure history_step updates SampleStore.current + present_right",
                )
            _history_step(app, mid, 1)

        _phase("7 close mono → disarm; next sample no analysis unless reopened")
        _close_monodisperse(app, right, win)
        assert not win._state.monodisperse_armed  # noqa: SLF001
        buf29 = Path(VALIDATION_RAW) / "ihs29_buffer.tif"
        sam29 = Path(VALIDATION_RAW) / "ihs29_94.6_sample.tif"
        token29 = "ihs29_94.6_sample"
        _atomic_copy_into_watchdir(buf29, watchdir)
        int_buf29 = watchdir / "averaged" / "int_ihs29_buffer.dat"
        assert _wait_until(app, lambda: int_buf29.is_file(), timeout, abort_if=abort)
        assert _wait_until_queue_idle(app, win, timeout)
        _set_buffer_and_subtract_options(
            app, win, left, int_buf=int_buf29, q_min=q_min, q_max=q_max, timeout=timeout
        )
        _atomic_copy_into_watchdir(sam29, watchdir)
        sub29 = watchdir / "subtracted" / f"sub_{token29}.dat"
        assert _wait_until(app, lambda: sub29.is_file(), timeout, abort_if=abort)
        assert _wait_until_queue_idle(app, win, timeout)
        g29 = watchdir / "guinier_mono" / token29
        if g29.exists():
            note(
                "7 analysis after close",
                f"{g29} exists while mono disarmed",
                "No analysis artifacts for sample after mono window closed",
                severity="wrong_science",
                cause="analysis_enabled still true after close",
                fix="Closing mono wizard must disarm and keep plan_for analysis off",
            )
        print("analysis scenario done", flush=True)

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
