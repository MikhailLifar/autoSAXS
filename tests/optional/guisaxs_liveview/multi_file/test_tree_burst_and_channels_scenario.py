"""Launch D — TREE multi-file burst + interleaved channels (optional exhaustive).

Agent: run when TREE ingest, admit queue, DnD, Process, or poll reprocess changed.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import QApplication

from .._scenario_support import (
    install_slot_exception_guard,
    load_liveview_lib,
    record_finding,
    soft_patch_show_tiff_for_attack,
)

_lib = load_liveview_lib()
globals().update({k: getattr(_lib, k) for k in dir(_lib) if not k.startswith("__")})


def test_tree_burst_and_channels_scenario():
    install_slot_exception_guard()
    soft_patch_show_tiff_for_attack(launch="multi_file")
    timeout = _gui_timeout_sec()
    q_min, q_max = _subtract_q_window_from_validation_config()
    watchdir = Path(WORKSPACE_ROOT) / "test_liveview_opt_multi_file"
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
            launch="multi_file",
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
        _assert_watch_mode_tree(win)
        _calibrate_via_ui(app, win, left, timeout=timeout)
        buf_stem = "ihs27_buffer"
        buf_src = Path(VALIDATION_RAW) / f"{buf_stem}.tif"
        buf_dest = _atomic_copy_into_tree(buf_src, watchdir, stem=buf_stem)
        int_buf = _tree_int_dat(watchdir, buf_stem)
        assert _wait_until(
            app,
            lambda: int_buf.is_file() and int_buf.stat().st_size > 0,
            timeout,
            abort_if=abort,
        ), f"buffer integrate missing {int_buf}"
        assert _wait_until_queue_idle(app, win, timeout)
        _set_buffer_and_subtract_options(
            app, win, left, int_buf=int_buf, q_min=q_min, q_max=q_max, timeout=timeout
        )
        _arm_monodisperse(right)
        _configure_monodisperse_shape(app, right, win, enable_dammif=False)
        assert win._state.monodisperse_armed  # noqa: SLF001

        burst_stems = list(_IHS_SAMPLE_STEMS[:5])
        _phase(f"1 burst {len(burst_stems)} TREE samples")
        dests = _burst_copy_samples_into_tree(watchdir, burst_stems)
        assert len(dests) == len(burst_stems)
        ok_burst = _wait_tree_subs(app, win, watchdir, burst_stems, timeout=timeout)
        if not (ok_burst and not abort()):
            missing = [s for s in burst_stems if not _tree_sub_dat(watchdir, s).is_file()]
            note(
                "1 burst incomplete",
                f"missing subtracted for {missing}; abort={abort()}",
                "All burst stems produce sub_*.dat (FIFO complete)",
                severity="crash" if abort() else "ux",
                cause="TREE settle/admit/plan or Stop stuck",
                fix="Inspect RevisionIngress + executor promote under TREE burst",
            )
        else:
            assert _wait_until_queue_idle(app, win, timeout)
        _settle_after_idle(1.0)

        _phase("2 same-path rewrite while queued/settling")
        # Drop a fresh stem then immediately rewrite with another sample's bytes
        rewrite_stem = "ihs33_91.8_sample"
        first = _atomic_copy_into_tree(
            Path(VALIDATION_RAW) / f"{rewrite_stem}.tif", watchdir, stem=rewrite_stem
        )
        _settle_after_idle(0.15)
        other = Path(VALIDATION_RAW) / "ihs34_91.2_sample.tif"
        _rewrite_file_in_place(first, other)
        ok_rw = _wait_until(
            app,
            lambda: _tree_sub_dat(watchdir, rewrite_stem).is_file(),
            timeout,
            abort_if=abort,
        )
        if not (ok_rw and not abort()):
            note(
                "2 rewrite incomplete",
                f"no sub for {rewrite_stem} after in-place rewrite",
                "Newer revision wins; single subtract for stem",
                severity="ux",
                cause="Settle/coalesce may drop rewrite or stall",
                fix="Ensure TREE note_path_stat + admit replace handles mtime bump",
            )
        assert _wait_until_queue_idle(app, win, timeout)

        _phase("3 multi-path DnD while TREE idle/busy")
        dnd_a = Path(VALIDATION_RAW) / "ihs35_90.5_sample.tif"
        dnd_b = Path(VALIDATION_RAW) / "ihs36_89.9_sample.tif"
        _ingest_dnd_paths(win, [dnd_a, dnd_b])
        # DnD copies to watchdir root — roots use FLAT-style output layout under watchdir
        dnd_stems = [dnd_a.stem, dnd_b.stem]
        def _dnd_ready() -> bool:
            for s in dnd_stems:
                root_sub = watchdir / "subtracted" / f"sub_{s}.dat"
                tree_sub = _tree_sub_dat(watchdir, s)
                if not (
                    (root_sub.is_file() and root_sub.stat().st_size > 0)
                    or (tree_sub.is_file() and tree_sub.stat().st_size > 0)
                ):
                    return False
            return True

        ok_dnd = _wait_until(app, _dnd_ready, timeout, abort_if=abort)
        if not (ok_dnd and not abort()):
            note(
                "3 dnd incomplete",
                f"DnD stems {dnd_stems} missing subtract outs",
                "Multi-path DnD boards and completes both samples",
                severity="ux",
                cause="accept_manual / copy_into_watchdir / plan",
                fix="Trace ingest_dropped_files for multi-path lists",
            )
        assert _wait_until_queue_idle(app, win, timeout)

        _phase("4 Stop with depth then Resume")
        _stop_auto_via_mono(app, right, win)
        stop_stems = ["ihs37_89.3_sample", "ihs38_88.7_sample", "ihs39_88.2_sample"]
        _burst_copy_samples_into_tree(watchdir, stop_stems)
        _settle_after_idle(2.0)
        # While Manual, auto should not finish new subtracts quickly — allow some settle only
        advanced = [s for s in stop_stems if _tree_sub_dat(watchdir, s).is_file()]
        if len(advanced) == len(stop_stems) and not win._state.is_auto_processing():  # noqa: SLF001
            note(
                "4 manual still processed",
                f"all {stop_stems} subtracted while auto_processing=False",
                "Stop holds auto promote; Resume then completes",
                severity="ux",
                cause="Executor may still promote auto jobs when paused",
                fix="Confirm paused path blocks auto promote/start",
            )
        _resume_auto_via_mono(app, right, win)
        ok_stop = _wait_tree_subs(app, win, watchdir, stop_stems, timeout=timeout)
        if not (ok_stop and not abort()):
            note(
                "4 resume incomplete",
                f"after Resume missing subs for stop_stems",
                "Resume drains admit queue in order",
                severity="ux",
                cause="Resume / Idle promotion",
                fix="Check session.resume + executor wake",
            )
        assert _wait_until_queue_idle(app, win, timeout)

        _phase("5 Process while queue busy")
        # Seed another drop then immediately Process current
        busy_stem = "ihs30_94.0_sample"
        # re-drop to create activity (stem may already exist — rewrite)
        existing = _tree_sample_dir(watchdir, busy_stem) / f"{busy_stem}.tif"
        if existing.is_file():
            _rewrite_file_in_place(existing, Path(VALIDATION_RAW) / f"{busy_stem}.tif")
        else:
            _atomic_copy_into_tree(
                Path(VALIDATION_RAW) / f"{busy_stem}.tif", watchdir, stem=busy_stem
            )
        _settle_after_idle(0.3)
        _process_history_via_ui(app, mid)
        assert not abort(), "App crashed on Process while busy"
        assert _wait_until_queue_idle(app, win, timeout)

        _phase("6 poll overwrite finished sample")
        poll_stem = burst_stems[0]
        poll_tif = _tree_sample_dir(watchdir, poll_stem) / f"{poll_stem}.tif"
        sub_before = _tree_sub_dat(watchdir, poll_stem)
        mtime_before = sub_before.stat().st_mtime if sub_before.is_file() else 0.0
        assert poll_tif.is_file(), poll_tif
        _rewrite_file_in_place(poll_tif, Path(VALIDATION_RAW) / f"{poll_stem}.tif")
        ok_poll = _wait_until(
            app,
            lambda: sub_before.is_file() and sub_before.stat().st_mtime > mtime_before + 0.01,
            min(timeout, 300.0),
            abort_if=abort,
        )
        if not (ok_poll and not abort()):
            note(
                "6 poll no reprocess",
                f"sub mtime not updated for {poll_stem} after TIFF overwrite",
                "ProcessedTiffPoller re-enqueues overwritten completed TIFF",
                severity="ux",
                cause="Poll track / owned-output filter / settle",
                fix="Ensure session_file_completed tracks TREE paths and rewrite notifies poller",
            )
        assert _wait_until_queue_idle(app, win, timeout)
        print(f"multi_file snapshot: {_snapshot_session(win)}", flush=True)

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
