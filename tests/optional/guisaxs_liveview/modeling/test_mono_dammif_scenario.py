"""Optional exhaustive: monodisperse GUI pipeline including DAMMIF (ihs27).

Agent: run only when modeling / shape UI changed significantly.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_lib_path = (
    Path(__file__).resolve().parents[3] / "must-run" / "guisaxs_liveview" / "_lib.py"
)
_spec = importlib.util.spec_from_file_location("liveview_test_lib", _lib_path)
assert _spec and _spec.loader
_lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_lib)
globals().update({k: getattr(_lib, k) for k in dir(_lib) if not k.startswith("__")})

def test_guisaxs_liveview_monodisperse_dammif_scenario():
    """
    End-to-end monodisperse GUI scenario: calib once, then 3 buffer–sample pairs
    with buffer reset between pairs; Adjust GNOM when refine knobs exist;
    DAMMIF for ihs27 via Start modeling → guisaxs-shape Confirm (n_runs=1).
    Closing the window aborts waits. Not part of the commit gate.
    """
    timeout = _gui_timeout_sec()
    q_min, q_max = _subtract_q_window_from_validation_config()

    watchdir = Path(WORKSPACE_ROOT) / "test_liveview"
    _rm_tree_contents(watchdir)

    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication

    from guisaxs_skills.liveview.window import LiveviewMainWindow

    created_app = QApplication.instance() is None
    app = QApplication.instance() or QApplication([])
    win = LiveviewMainWindow(watchdir=watchdir)
    win.show()
    win.raise_()
    win.activateWindow()
    _process_events(app)

    try:
        left = win._left  # noqa: SLF001
        right = win._right  # noqa: SLF001

        # --- Calibrate once ---
        QTest.mouseClick(left._cal_open, Qt.LeftButton)  # noqa: SLF001
        assert left._cal_wizard is not None  # noqa: SLF001
        wiz = left._cal_wizard  # noqa: SLF001
        form = wiz._form  # noqa: SLF001

        calib = Path(VALIDATION_RAW) / "AgBh700_96.9_calib.tif"
        mask = Path(VALIDATION_DIR) / "mask_fti2d_1225.msk"
        for p in (calib, mask):
            assert p.is_file(), f"Missing validation fixture: {p}"

        assert _set_pathfield_text_by_label(form, label="calibrant_image", text=str(calib))
        try:
            getattr(form, "_on_primary_path_expression_changed")()  # type: ignore[attr-defined]
        except Exception:
            pass
        _wait_until(
            app,
            lambda: bool(_get_pathfield_text_by_label(form, label="mask")),
            2.0,
            step_sec=0.05,
        )
        assert _set_pathfield_text_by_label(form, label="mask", text=str(mask))
        if _get_pathfield_text_by_label(form, label="config_path").strip():
            assert _set_pathfield_text_by_label(form, label="config_path", text="")
        assert not _get_pathfield_text_by_label(form, label="config_path").strip()

        QTest.mouseClick(wiz._controls.run_button, Qt.LeftButton)  # noqa: SLF001
        ok_cal = _wait_until(
            app,
            lambda: (watchdir / "calibration" / "integrator").is_dir()
            and (watchdir / "calibration" / "refined.yml").is_file(),
            timeout,
        )
        assert ok_cal, "Calibration did not produce integrator + refined.yml within timeout"
        assert _wait_until_runcontrols_idle(app, wiz._controls, timeout), "Calibration wizard did not become Idle"
        _settle_after_idle(1.0)
        wiz.close()
        _wait_until(app, lambda: not wiz.isVisible(), 3.0)
        assert _wait_until_queue_idle(app, win, timeout), "Queue did not become Idle after calibration"
        _settle_after_idle(1.0)
        assert _wait_until_app_idle(app, win, timeout), "App did not become idle after calibration"
        _settle_after_idle(1.0)

        for pair_i, (key, buf_name, sam_name, sample_token) in enumerate(_MONO_SCENARIO_PAIRS):
            enable_dam = key == _MONO_DAM_KEY
            print(f"\n=== pair {pair_i + 1}/3 {key} (model_dam={enable_dam}) ===", flush=True)
            abort = lambda: _window_gone(win)

            if pair_i > 0:
                _reset_buffer_via_ui(app, win, left, timeout)
                assert not win._state.monodisperse_armed  # noqa: SLF001

            buffer_src = Path(VALIDATION_RAW) / buf_name
            sample_src = Path(VALIDATION_RAW) / sam_name
            assert buffer_src.is_file(), buffer_src
            assert sample_src.is_file(), sample_src

            # Buffer TIFF → integrate
            _atomic_copy_into_watchdir(buffer_src, watchdir)
            int_buf = watchdir / "averaged" / f"int_{buf_name.replace('.tif', '')}.dat"
            ok_buf = _wait_until(
                app,
                lambda: int_buf.is_file() and int_buf.stat().st_size > 0,
                timeout,
                abort_if=abort,
            )
            assert ok_buf and not abort(), f"Buffer integration did not produce {int_buf}"
            assert _wait_until_queue_idle(app, win, timeout), f"Queue not Idle after buffer {key}"
            _settle_after_idle(1.0)

            _set_buffer_and_subtract_options(
                app, win, left, int_buf=int_buf, q_min=q_min, q_max=q_max, timeout=timeout
            )
            # Shape stays off during auto Guinier/p(r); DAMMIF is applied once after Adjust.
            _configure_monodisperse_shape(app, right, win, enable_dammif=False)

            # Sample TIFF → integrate + subtract + analysis (no DAM yet)
            _atomic_copy_into_watchdir(sample_src, watchdir)
            int_sam = watchdir / "averaged" / f"int_{sample_token}.dat"
            sub_out = watchdir / "subtracted" / f"sub_{sample_token}.dat"
            ok_outputs = _wait_until(
                app,
                lambda: int_sam.is_file()
                and int_sam.stat().st_size > 0
                and sub_out.is_file()
                and sub_out.stat().st_size > 0,
                timeout,
                abort_if=abort,
            )
            assert ok_outputs and not abort(), f"Missing int/sub outputs for {sample_token}"

            _wait_mono_analysis_artifacts(
                app,
                win,
                watchdir,
                sample_token=sample_token,
                expect_dam=False,
                timeout=timeout,
            )
            assert _wait_until_queue_idle(app, win, timeout), f"Queue not Idle after sample {key}"
            _settle_after_idle(1.0)

            refine = _load_mono_refine_params(key)
            if refine is not None:
                print(f"  Adjust GNOM refine params for {key}: {refine}", flush=True)
                _adjust_gnom_via_ui(app, win, right, params=refine, timeout=timeout)
                _wait_mono_analysis_artifacts(
                    app,
                    win,
                    watchdir,
                    sample_token=sample_token,
                    expect_dam=False,
                    timeout=timeout,
                )
                assert _wait_until_queue_idle(app, win, timeout), f"Queue not Idle after Adjust {key}"

            if enable_dam:
                print(f"  model_dam via guisaxs-shape Confirm n_runs=1 for {key}", flush=True)
                _run_dammif_via_ui(app, win, right, timeout=timeout, n_runs=1)
                _wait_mono_analysis_artifacts(
                    app,
                    win,
                    watchdir,
                    sample_token=sample_token,
                    expect_dam=True,
                    timeout=timeout,
                )
                dam_dir = watchdir / "dammif" / sample_token
                assert (dam_dir / "dammif_fits.yml").is_file() or (dam_dir / "best.cif").exists(), (
                    f"model_dam artifacts missing under {dam_dir}"
                )
                dam_runs = list(dam_dir.glob("dammif-*-1.cif")) or list(dam_dir.glob("dammif-*.cif"))
                assert len(dam_runs) >= 1, f"Expected DAMMIF outputs under {dam_dir}"

            _assert_curves_match_validation(integrated_path=int_sam, subtracted_path=sub_out)

        # Only one dammif sample directory should exist (ignore SkillRunner ``runs/``).
        dam_root = watchdir / "dammif"
        if dam_root.is_dir():
            dam_samples = [
                p
                for p in dam_root.iterdir()
                if p.is_dir()
                and p.name != "runs"
                and (
                    (p / "dammif_fits.yml").is_file()
                    or (p / "best.cif").exists()
                    or any(p.glob("dammif-*.cif"))
                )
            ]
            assert len(dam_samples) == 1, f"Expected one dammif sample dir, got {dam_samples}"
            assert dam_samples[0].name == "ihs27_95.9_sample"

    finally:
        try:
            win._controller.shutdown()  # noqa: SLF001
        except Exception:
            pass
        try:
            runner = win._controller.runner  # noqa: SLF001
            if runner.is_running():
                runner.cancel()
                _wait_until(app, lambda: not runner.is_running(), 8.0)
        except Exception:
            pass
        try:
            win.close()
            try:
                _wait_until(app, lambda: not win.isVisible(), 3.0)
                win.deleteLater()
                _process_events(app)
            except Exception:
                pass
        except Exception:
            pass
        if created_app:
            try:
                app.quit()
                _process_events(app)
            except Exception:
                pass

