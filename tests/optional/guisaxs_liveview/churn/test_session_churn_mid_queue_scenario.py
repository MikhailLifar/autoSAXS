"""Launch E — session churn mid-queue (optional exhaustive).

Agent: run when buffer/intake/Stop/mono arming mid-flight behavior changed.
"""

from __future__ import annotations

import json
from pathlib import Path

from PyQt5.QtWidgets import QApplication

from .._scenario_support import (
    PipelineDiag,
    install_slot_exception_guard,
    load_liveview_lib,
    record_finding,
    soft_patch_show_tiff_for_attack,
)

_lib = load_liveview_lib()
globals().update({k: getattr(_lib, k) for k in dir(_lib) if not k.startswith("__")})

# Interest stems for A/B/C diagnosis (phase 2 swallow + phase 3 hang).
_DIAG_INTEREST = ("ihs30_94.0_sample", "ihs28_buffer", "dropped_int_curve")


def _wait_with_probes(
    app,
    win,
    diag: PipelineDiag,
    watchdir: Path,
    stems: list,
    predicate,
    timeout: float,
    *,
    abort_if,
    probe_every: float = 15.0,
) -> bool:
    """Bounded wait that dumps stem probes periodically (avoid silent hang)."""
    import time

    t0 = time.monotonic()
    last_probe = t0
    while (time.monotonic() - t0) < timeout:
        if abort_if is not None and abort_if():
            return False
        if predicate():
            return True
        now = time.monotonic()
        if (now - last_probe) >= probe_every:
            last_probe = now
            for st in stems:
                diag.probe_stem(watchdir, st, label=f"wait@{now - t0:.0f}s")
        _process_events(app)
        time.sleep(0.05)
    for st in stems:
        diag.probe_stem(watchdir, st, label=f"wait_timeout@{timeout:.0f}s")
    return False


def test_session_churn_mid_queue_scenario():
    install_slot_exception_guard()
    soft_patch_show_tiff_for_attack(launch="churn")
    timeout = _gui_timeout_sec()
    # Cap long waits so diagnostics always finish (prior hang burned 1800s).
    phase_t = min(90.0, timeout)
    q_min, q_max = _subtract_q_window_from_validation_config()
    watchdir = Path(WORKSPACE_ROOT) / "test_liveview_opt_churn"
    _rm_tree_contents(watchdir)

    created_app = QApplication.instance() is None
    app = QApplication.instance() or QApplication([])
    from guisaxs_skills.liveview.window import LiveviewMainWindow

    win = LiveviewMainWindow(watchdir=watchdir)
    win.show()
    win.raise_()
    win.activateWindow()
    _process_events(app)

    diag = PipelineDiag(win, interest=_DIAG_INTEREST, tick_every=40)
    diag.install()

    def note(phase, observed, expected, severity="ux", cause="", fix=""):
        record_finding(
            launch="churn",
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

        _phase("0 setup TREE + calib + buffer + mono")
        diag.mark("phase0_start")
        _assert_watch_mode_tree(win)
        _calibrate_via_ui(app, win, left, timeout=timeout)
        buf_stem = "ihs27_buffer"
        _atomic_copy_into_tree(
            Path(VALIDATION_RAW) / f"{buf_stem}.tif", watchdir, stem=buf_stem
        )
        int_buf = _tree_int_dat(watchdir, buf_stem)
        assert _wait_until(
            app, lambda: int_buf.is_file(), timeout, abort_if=abort
        )
        assert _wait_until_queue_idle(app, win, timeout)
        _set_buffer_and_subtract_options(
            app, win, left, int_buf=int_buf, q_min=q_min, q_max=q_max, timeout=timeout
        )
        _arm_monodisperse(right)
        _configure_monodisperse_shape(app, right, win, enable_dammif=False)

        seed = ["ihs27_95.9_sample", "ihs28_95.2_sample"]
        _phase("0b seed short queue")
        _burst_copy_samples_into_tree(watchdir, seed)
        # Do not wait for Idle — churn while busy when possible
        _settle_after_idle(1.0)

        _phase("1 buffer reset mid-queue → disarm; re-set + re-arm")
        diag.mark("phase1_start")
        _reset_buffer_via_ui(app, win, left, timeout)
        assert not win._state.monodisperse_armed  # noqa: SLF001
        assert win._state.buffer_dat_path is None  # noqa: SLF001
        # Let any in-flight jobs finish without requiring subtract
        assert _wait_until_queue_idle(app, win, timeout)
        # Re-set buffer (may need fresh int if still present)
        if not int_buf.is_file():
            _atomic_copy_into_tree(
                Path(VALIDATION_RAW) / f"{buf_stem}.tif", watchdir, stem=buf_stem
            )
            assert _wait_until(app, lambda: int_buf.is_file(), timeout, abort_if=abort)
            assert _wait_until_queue_idle(app, win, timeout)
        _set_buffer_and_subtract_options(
            app, win, left, int_buf=int_buf, q_min=q_min, q_max=q_max, timeout=timeout
        )
        _arm_monodisperse(right)
        _configure_monodisperse_shape(app, right, win, enable_dammif=False)
        next_stem = "ihs29_94.6_sample"
        _atomic_copy_into_tree(
            Path(VALIDATION_RAW) / f"{next_stem}.tif", watchdir, stem=next_stem
        )
        ok_next = _wait_until(
            app,
            lambda: _tree_sub_dat(watchdir, next_stem).is_file(),
            timeout,
            abort_if=abort,
        )
        if not (ok_next and not abort()):
            note(
                "1 next sample no subtract",
                f"no sub after re-set buffer for {next_stem}",
                "After buffer re-set + re-arm, new sample subtracts",
                severity="wrong_science",
                cause="plan_for buffer_ready / arming",
                fix="Ensure set_buffer + arm update plan_for for subsequent promotes",
            )
        assert _wait_until_queue_idle(app, win, timeout)

        _phase("2 intake flip while queue non-empty")
        diag.mark("phase2_before_ihs30_drop")
        _burst_copy_samples_into_tree(watchdir, ["ihs30_94.0_sample"])
        diag.probe_stem(watchdir, "ihs30_94.0_sample", label="after_ihs30_drop")
        _settle_after_idle(0.5)
        diag.mark("phase2_before_intake_1d")
        _set_intake_mode(app, right, "1d")
        diag.probe_stem(watchdir, "ihs30_94.0_sample", label="after_intake_1d")
        snap1 = _snapshot_session(win)
        _set_intake_mode(app, right, "sub")
        diag.probe_stem(watchdir, "ihs30_94.0_sample", label="after_intake_sub")
        snap2 = _snapshot_session(win)
        _set_intake_mode(app, right, "2d")
        diag.probe_stem(watchdir, "ihs30_94.0_sample", label="after_intake_2d_baseline")
        assert not abort(), "App crashed on intake flips"
        if snap2["intake_mode"] not in ("curve_sub",):
            note(
                "2 intake flip",
                f"after Sub click intake={snap2['intake_mode']!r} (was 1d={snap1['intake_mode']!r})",
                "Intake buttons update session.intake_mode",
                severity="ux",
                cause="UI/session intake wiring",
                fix="Sync intake toggle to LiveviewSession.set_intake",
            )
        # Root .dat while 2D (auto-switch contract)
        if int_buf.is_file():
            root_dat = watchdir / "dropped_int_curve.dat"
            root_dat.write_bytes(int_buf.read_bytes())
            diag.mark("phase2_root_dat_written")
            _settle_after_idle(2.0)
            mode = str(getattr(win._state.intake_mode, "value", win._state.intake_mode))  # noqa: SLF001
            if mode == "frame_2d":
                note(
                    "2 dat while 2d no switch",
                    f"intake still {mode} after root .dat",
                    "Auto-switch 2D → 1D/Sub per spec §4.3",
                    severity="ux",
                    cause="Watch path may not auto-switch (DnD does)",
                    fix="Apply classify+switch on watch-ingest of root .dat",
                )
        diag.probe_stem(watchdir, "ihs30_94.0_sample", label="phase2_end")
        assert _wait_until_queue_idle(app, win, timeout)

        _phase("3 Stop → change buffer → Resume")
        diag.mark("phase3_before_stop")
        _stop_auto_via_mono(app, right, win)
        diag.mark("phase3_after_stop", auto=win._state.is_auto_processing())  # noqa: SLF001
        buf28_stem = "ihs28_buffer"
        _atomic_copy_into_tree(
            Path(VALIDATION_RAW) / f"{buf28_stem}.tif", watchdir, stem=buf28_stem
        )
        diag.probe_stem(watchdir, buf28_stem, label="after_buf28_drop_while_manual")
        _settle_after_idle(1.0)
        diag.probe_stem(watchdir, buf28_stem, label="settle1s_after_buf28_drop")
        _resume_auto_via_mono(app, right, win)
        diag.mark("phase3_after_resume", auto=win._state.is_auto_processing())  # noqa: SLF001
        diag.probe_stem(watchdir, buf28_stem, label="after_resume")
        diag.probe_stem(watchdir, "ihs30_94.0_sample", label="ihs30_after_resume")
        int_buf28 = _tree_int_dat(watchdir, buf28_stem)
        ok_buf28 = _wait_with_probes(
            app,
            win,
            diag,
            watchdir,
            [buf28_stem, "ihs30_94.0_sample"],
            lambda: int_buf28.is_file(),
            phase_t,
            abort_if=abort,
            probe_every=12.0,
        )
        if not (ok_buf28 and not abort()):
            sum28 = diag.summarize_stem(buf28_stem)
            sum30 = diag.summarize_stem("ihs30_94.0_sample")
            note(
                "3 buffer integrate after Resume",
                f"no {int_buf28} within {phase_t}s after Stop/drop/Resume "
                f"(tiff present={(_tree_sample_dir(watchdir, buf28_stem) / (buf28_stem + '.tif')).is_file()}; "
                f"diag28={json.dumps(sum28)}; diag30={json.dumps(sum30)})",
                "Resume promotes TREE buffer TIFF to integrate promptly",
                severity="ux",
                cause="See _pipeline_diag.jsonl A/B/C classification",
                fix="(observe only this pass)",
            )
            # Fall back to existing ihs27 buffer for remaining phases
            int_buf28 = int_buf
        else:
            assert _wait_until_queue_idle(app, win, phase_t)
        _set_buffer_and_subtract_options(
            app, win, left, int_buf=int_buf28, q_min=q_min, q_max=q_max, timeout=timeout
        )
        _arm_monodisperse(right)
        late = "ihs31_93.1_sample"
        _atomic_copy_into_tree(
            Path(VALIDATION_RAW) / f"{late}.tif", watchdir, stem=late
        )
        ok_late = _wait_until(
            app,
            lambda: _tree_sub_dat(watchdir, late).is_file(),
            phase_t,
            abort_if=abort,
        )
        if not (ok_late and not abort()):
            note(
                "3 late sample after buffer change",
                f"no sub for {late} after Stop/Resume + new buffer",
                "Later promotes use updated buffer session fact",
                severity="ux",
                cause="plan_for / buffer_ready after Resume",
                fix="Replan or next promote must read current session buffer",
            )
        _wait_until_queue_idle(app, win, phase_t)

        _phase("4 mono arm → close → re-arm; history current-only")
        if not win._state.monodisperse_armed:  # noqa: SLF001
            _arm_monodisperse(right)
            _configure_monodisperse_shape(app, right, win, enable_dammif=False)
        _close_monodisperse(app, right, win)
        assert not win._state.monodisperse_armed  # noqa: SLF001
        closed_stem = "ihs32_92.5_sample"
        _atomic_copy_into_tree(
            Path(VALIDATION_RAW) / f"{closed_stem}.tif", watchdir, stem=closed_stem
        )
        ok_closed = _wait_until(
            app,
            lambda: _tree_sub_dat(watchdir, closed_stem).is_file(),
            phase_t,
            abort_if=abort,
        )
        if not ok_closed:
            note(
                "4 closed-stem no subtract",
                f"no sub for {closed_stem} within {phase_t}s while disarmed",
                "Integrate/subtract still runs without mono arm",
                severity="ux",
                cause="TREE admit stall (see phase 3) or plan gap",
                fix="Same as TREE promote after churn",
            )
        _wait_until_queue_idle(app, win, phase_t)
        g_closed = _tree_guinier_results(watchdir, closed_stem)
        if g_closed.is_file():
            note(
                "4 analysis while disarmed",
                f"{g_closed} exists after mono closed",
                "No Guinier after close until re-arm",
                severity="wrong_science",
                cause="plan_for analysis_enabled still true",
                fix="Dialog close must keep disarm through subsequent promotes",
            )
        _arm_monodisperse(right)
        _configure_monodisperse_shape(app, right, win, enable_dammif=False)
        rearm_stem = "ihs33_91.8_sample"
        _atomic_copy_into_tree(
            Path(VALIDATION_RAW) / f"{rearm_stem}.tif", watchdir, stem=rearm_stem
        )
        _wait_until(
            app,
            lambda: _tree_sub_dat(watchdir, rearm_stem).is_file(),
            phase_t,
            abort_if=abort,
        )
        _wait_until(
            app,
            lambda: _tree_guinier_results(watchdir, rearm_stem).is_file(),
            phase_t,
            abort_if=abort,
        )
        _wait_until_queue_idle(app, win, phase_t)
        if mid._btn_hist_prev.isEnabled():  # noqa: SLF001
            label_a = mid._history_label.text()  # noqa: SLF001
            _history_step(app, mid, -1)
            label_b = mid._history_label.text()  # noqa: SLF001
            if label_a == label_b:
                note(
                    "4 history nav",
                    f"label stayed {label_a!r}",
                    "History < changes current sample",
                    severity="ux",
                    cause="History nav / single sample",
                    fix="history_step updates SampleStore.current",
                )
            _history_step(app, mid, 1)
        print(f"churn snapshot: {_snapshot_session(win)}", flush=True)
        print(
            "diag summaries: "
            f"ihs30={json.dumps(diag.summarize_stem('ihs30_94.0_sample'))} "
            f"ihs28_buffer={json.dumps(diag.summarize_stem('ihs28_buffer'))}",
            flush=True,
        )

    finally:
        try:
            diag.probe_stem(watchdir, "ihs30_94.0_sample", label="finally")
            diag.probe_stem(watchdir, "ihs28_buffer", label="finally")
            diag.mark("scenario_end")
        except Exception:
            pass
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
