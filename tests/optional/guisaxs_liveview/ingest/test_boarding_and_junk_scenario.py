"""Launch A — ingest / boarding / invalid SAXS files (optional exhaustive).

Agent: run when ingest / watch / intake boarding changed significantly.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import QApplication

from .._scenario_support import (
    consume_slot_exception,
    install_slot_exception_guard,
    load_liveview_lib,
    peek_slot_exception,
    record_finding,
    soft_patch_show_tiff_for_attack,
)

_lib = load_liveview_lib()
globals().update({k: getattr(_lib, k) for k in dir(_lib) if not k.startswith("__")})


def test_boarding_and_junk_scenario():
    """
    One liveview launch: junk → invalid .tif → valid uncalibrated → calib →
    valid 2D flow → invalid .tif again → intake switches → invalid .dat → Sub.
    """
    install_slot_exception_guard()
    soft_patch_show_tiff_for_attack(launch="ingest")
    timeout = _gui_timeout_sec()
    watchdir = Path(WORKSPACE_ROOT) / "test_liveview_opt_ingest"
    _rm_tree_contents(watchdir)

    created_app = QApplication.instance() is None
    app = QApplication.instance() or QApplication([])
    from guisaxs_skills.liveview.window import LiveviewMainWindow

    win = LiveviewMainWindow(watchdir=watchdir)
    win.show()
    win.raise_()
    win.activateWindow()
    _process_events(app)

    findings_local = []

    def note(phase, observed, expected, severity="ux", cause="", fix=""):
        findings_local.append(phase)
        record_finding(
            launch="ingest",
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

        # --- 1. Junk extensions ---
        _phase("1 junk txt/png")
        (watchdir / "readme.txt").write_text("not saxs\n", encoding="utf-8")
        _atomic_write_into_watchdir(watchdir, "noise.png", b"\x89PNG\r\n\x1a\nnotreally")
        _settle_after_idle(1.5)
        assert not abort(), "App crashed on junk files"
        assert _wait_until_queue_idle(app, win, min(60.0, timeout))
        averaged = watchdir / "averaged"
        assert (not averaged.exists()) or (not any(averaged.glob("int_*.dat")))

        # --- 2. Invalid .tif (pre-calib) ---
        _phase("2 invalid tif pre-calib")
        _atomic_write_into_watchdir(watchdir, "bad_frame.tif", b"not a tiff payload")
        warn = _wait_user_warning(app, win, timeout_sec=min(90.0, timeout))
        _dismiss_modal_dialogs(app)
        assert not abort(), "App crashed on invalid .tif"
        assert _wait_until_queue_idle(app, win, min(120.0, timeout))
        bad_int = list((watchdir / "averaged").glob("int_bad_frame*.dat")) if averaged.exists() else []
        assert not bad_int, f"Unexpected integrate output for invalid tif: {bad_int}"
        if not warn.get("has_toast"):
            note(
                "2 invalid tif toast",
                f"No Toast; dialog={warn.get('dialog_texts')!r} log_hit={warn.get('log_hit')} "
                f"tail={warn.get('log_tail', '')[-200:]!r}",
                "Toast that .tif is invalid; skip sample; continue queue",
                severity="ux",
                cause="Pipeline errors use QMessageBox/log (controller.on_error), not Toast",
                fix="Emit Toast (or shared toast helper) for unreadable TIFF/DAT ingest failures "
                "in addition to or instead of modal QMessageBox",
            )
        if not warn.get("ok"):
            note(
                "2 invalid tif warning",
                "No toast, dialog, or Error: log after corrupt .tif",
                "User-visible invalid-file warning",
                severity="crash" if abort() else "ux",
                cause="Ingest may silently skip or fail without user feedback",
                fix="Surface invalid TIFF via toast + app log when skill/read fails",
            )

        # --- 3. Valid sample while uncalibrated ---
        _phase("3 valid sample uncalibrated")
        sample_src = Path(VALIDATION_RAW) / "ihs27_95.9_sample.tif"
        assert sample_src.is_file()
        _atomic_copy_into_watchdir(sample_src, watchdir)
        # proxy or integrate path — wait for some averaged_proxy or averaged output
        proxy = watchdir / "averaged_proxy" / "int_ihs27_95.9_sample.dat"
        integ = watchdir / "averaged" / "int_ihs27_95.9_sample.dat"
        ok_out = _wait_until(
            app,
            lambda: (proxy.is_file() and proxy.stat().st_size > 0)
            or (integ.is_file() and integ.stat().st_size > 0),
            timeout,
            abort_if=abort,
        )
        assert ok_out and not abort(), "Valid uncalibrated sample produced no 1D output"
        assert _wait_until_queue_idle(app, win, timeout)
        _settle_after_idle(1.0)

        # --- 4. Calibrate ---
        _phase("4 calibrate")
        _calibrate_via_ui(app, win, left, timeout=timeout)

        # --- 5. Valid 2D buffer + sample (no buffer set → no subtract) ---
        _phase("5 valid buffer+sample no subtract")
        _set_intake_mode(app, right, "2d")
        buf_src = Path(VALIDATION_RAW) / "ihs27_buffer.tif"
        _atomic_copy_into_watchdir(buf_src, watchdir)
        int_buf = watchdir / "averaged" / "int_ihs27_buffer.dat"
        ok_buf = _wait_until(
            app,
            lambda: int_buf.is_file() and int_buf.stat().st_size > 0,
            timeout,
            abort_if=abort,
        )
        assert ok_buf and not abort(), f"Buffer integrate missing {int_buf}"
        assert _wait_until_queue_idle(app, win, timeout)
        # Do not set session buffer — sample should integrate only
        _atomic_copy_into_watchdir(sample_src, watchdir)
        # May overwrite prior int — wait idle
        assert _wait_until_queue_idle(app, win, timeout)
        sub = watchdir / "subtracted" / "sub_ihs27_95.9_sample.dat"
        if sub.is_file():
            note(
                "5 unexpected subtract",
                f"Found {sub} without buffer set",
                "No subtract when buffer_ready is false",
                severity="wrong_science",
                cause="plan_for or stale buffer fact",
                fix="Ensure buffer_ready gates subtract in plan_for",
            )

        # --- 6. Invalid .tif post-calib + valid recovery ---
        _phase("6 invalid tif post-calib then valid")
        consume_slot_exception()
        _atomic_write_into_watchdir(watchdir, "bad_frame2.tif", b"II*\x00corrupt")
        # Short poll: corrupt TIFF with TIFF magic can crash middle imshow (product bug).
        warn2 = {"has_toast": False, "dialog_texts": [], "log_hit": False, "ok": False}
        try:
            warn2 = _wait_user_warning(app, win, timeout_sec=min(25.0, timeout))
            _dismiss_modal_dialogs(app)
        except Exception as exc:  # noqa: BLE001
            note(
                "6 invalid tif exception",
                f"Exception while waiting for warning: {exc!r}",
                "No crash; toast; skip; continue",
                severity="crash",
                cause="Middle show_tiff / sync_middle paints unreadable TIFF",
                fix="Guard Image2DPlot.show_tiff for empty/non-2D arrays; toast+skip before paint",
            )
        slot_exc = peek_slot_exception() or consume_slot_exception()
        if slot_exc or abort():
            note(
                "6 invalid tif crash",
                f"Post-calib corrupt .tif crashed or raised in UI: {slot_exc!r} "
                f"window_gone={abort()}",
                "No crash; Toast that .tif is invalid; skip sample; queue continues",
                severity="crash",
                cause="history.sync_middle → middle.show_image → imshow Invalid shape (0,)",
                fix="Validate TIFF before paint; toast+skip; do not board unreadable frames",
            )
            print("Aborting remaining ingest phases after crash finding (launch unsafe)", flush=True)
            return
        # Drop another valid sample (ihs28) to prove continue
        s28 = Path(VALIDATION_RAW) / "ihs28_95.2_sample.tif"
        _atomic_copy_into_watchdir(s28, watchdir)
        int28 = watchdir / "averaged" / "int_ihs28_95.2_sample.dat"
        ok28 = _wait_until(
            app,
            lambda: int28.is_file() and int28.stat().st_size > 0,
            timeout,
            abort_if=abort,
        )
        if not (ok28 and not abort()):
            note(
                "6 valid after invalid",
                "Valid sample after invalid .tif did not integrate / window gone",
                "Queue continues; subsequent valid TIFF integrates",
                severity="crash",
                cause="Invalid TIFF left pipeline/UI in bad state",
                fix="Skip invalid revision cleanly without poisoning middle paint",
            )
            return
        assert _wait_until_queue_idle(app, win, timeout)
        if not warn2.get("has_toast"):
            note(
                "6 invalid tif toast post-calib",
                f"No Toast; dialog={warn2.get('dialog_texts')!r} log_hit={warn2.get('log_hit')}",
                "Toast that .tif is invalid",
                severity="ux",
                cause="Same as phase 2 — QMessageBox/log path",
                fix="Use Toast for invalid TIFF after calibrate as well",
            )

        # --- 7. Valid .dat while intake 2D → auto-switch ---
        _phase("7 dat drop while intake 2d")
        _set_intake_mode(app, right, "2d")
        # Prefer an existing integrated curve as valid .dat
        valid_dat = int_buf if int_buf.is_file() else integ
        if valid_dat.is_file():
            dest = watchdir / "dropped_curve.dat"
            dest.write_bytes(valid_dat.read_bytes())
            # nudge mtime via replace
            _settle_after_idle(2.0)
            mode = str(getattr(win._state.intake_mode, "value", win._state.intake_mode))  # noqa: SLF001
            if mode == "frame_2d":
                note(
                    "7 intake auto-switch",
                    f"intake still {mode} after root .dat drop",
                    "Auto-switch 2D → 1D/Sub per guisaxs_liveview_spec §4.3",
                    severity="ux",
                    cause="Drop may require middle drop MIME path; filesystem watch may not auto-switch",
                    fix="Apply same classify+switch on watch-ingest as on DnD",
                )

        # --- 8. Intake 1D + TIFF ---
        _phase("8 intake 1d then tiff")
        _set_intake_mode(app, right, "1d")
        _atomic_copy_into_watchdir(
            Path(VALIDATION_RAW) / "ihs29_94.6_sample.tif", watchdir
        )
        _settle_after_idle(2.0)
        mode = str(getattr(win._state.intake_mode, "value", win._state.intake_mode))  # noqa: SLF001
        # Spec: .tif in 1D → switch to 2D
        if mode != "frame_2d":
            note(
                "8 tiff switches intake to 2d",
                f"intake={mode}",
                "frame_2d after TIFF while in 1D",
                severity="ux",
                cause="Watch path may not auto-switch intake",
                fix="Mirror DnD Option A classify for watcher ingress",
            )
        assert _wait_until_queue_idle(app, win, timeout)

        # --- 9. Invalid .dat ---
        _phase("9 invalid dat")
        _set_intake_mode(app, right, "1d")
        _atomic_write_into_watchdir(watchdir, "bogus.dat", b"not saxs columns\n")
        warn3 = _wait_user_warning(app, win, timeout_sec=min(90.0, timeout))
        _dismiss_modal_dialogs(app)
        assert not abort(), "App crashed on invalid .dat"
        assert _wait_until_queue_idle(app, win, min(120.0, timeout))
        if not warn3.get("has_toast"):
            note(
                "9 invalid dat toast",
                f"No Toast; dialog={warn3.get('dialog_texts')!r} log_hit={warn3.get('log_hit')}",
                "Toast that .dat is invalid; continue",
                severity="ux",
                cause="Error path uses QMessageBox/log",
                fix="Toast for unreadable .dat boarding failures",
            )
        # Valid recovery: copy a good .dat into averaged/ style or root
        if int_buf.is_file():
            good = watchdir / "good_curve.dat"
            good.write_bytes(int_buf.read_bytes())
            _settle_after_idle(2.0)
            assert not abort()
            assert _wait_until_queue_idle(app, win, timeout)

        # --- 10. Sub intake ---
        _phase("10 intake sub")
        _set_intake_mode(app, right, "sub")
        snap = _snapshot_session(win)
        assert snap["intake_mode"] in ("curve_sub", "sub") or "sub" in snap["intake_mode"]
        print(f"session snapshot end: {snap}", flush=True)
        print(f"ingest findings noted: {len(findings_local)}", flush=True)

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
