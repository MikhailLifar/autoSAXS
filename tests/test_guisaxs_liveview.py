"""
Headless workflow test for guisaxs-liveview: drive the real PyQt GUI (no pixel checks).

Scenario:
- Launch Liveview window on a fixed watchdir: WORKSPACE_ROOT/test_liview
- Set calibration (validation/raw/AgBh...); wizard should auto-fill mask+config, but we set explicitly.
- Upload buffer TIFF -> wait for averaged/int_ihs27_buffer.dat
- Set buffer + subtraction options (Apply button)
- Set analysis mode to "Monodisperse analysis: p(r)" and apply fit_distances config
- Upload sample TIFF -> wait for subtracted/sub_ihs27_95.9_sample.dat and fit_distances PNGs
- Compare integrated + subtracted curves to validation baselines using the same regression metric as test_skills_real_data.

Requires a display (use xvfb on CI), e.g.:
  xvfb-run -a /path/to/python -m pytest repos/tests/test_guisaxs_liveview.py -v
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

import pytest
from guisaxs_skills.liveview.jobs import Job, JobStep
from guisaxs_skills.core.models import RunRequest
from guisaxs_skills.liveview.executor import LiveviewJobExecutor
from guisaxs_skills.liveview.state import LiveviewSessionState

_REPOS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_TESTS_DIR = os.path.join(_REPOS, "tests")
for _p in (_REPOS, _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

WORKSPACE_ROOT = os.path.abspath(os.path.join(_REPOS, ".."))
VALIDATION_DIR = os.path.join(WORKSPACE_ROOT, "validation")
VALIDATION_RAW = os.path.join(VALIDATION_DIR, "raw")

_VALIDATION_MISSING_MSG = (
    f"Validation directory not found: {VALIDATION_DIR}. "
    "Run: python repos/scripts/setup_validation_data.py"
)


@pytest.fixture(scope="module", autouse=True)
def _require_validation_dir_fixture():
    if not os.path.isdir(VALIDATION_DIR):
        raise FileNotFoundError(_VALIDATION_MISSING_MSG)


def _gui_timeout_sec() -> float:
    return float(os.environ.get("GUISAXS_LIVEVIEW_TEST_TIMEOUT", "900"))


def _process_events(app: Any) -> None:
    try:
        app.processEvents()
    except Exception:
        return


def _wait_until(app: Any, predicate, timeout_sec: float, *, step_sec: float = 0.05) -> bool:
    deadline = time.monotonic() + float(timeout_sec)
    while time.monotonic() < deadline:
        _process_events(app)
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(max(0.01, float(step_sec)))
    return False


def _settle_after_idle(sec: float = 1.0) -> None:
    """Small delay after UI becomes Idle to avoid races."""
    time.sleep(max(0.0, float(sec)))


def _wait_until_app_idle(app: Any, win: Any, timeout_sec: float) -> bool:
    """
    Best-effort: wait until the liveview window looks idle (no running skill, no queued items).
    Uses private attributes but stays defensive.
    """

    def _idle() -> bool:
        try:
            runner = getattr(win, "_runner", None)
            if runner is not None and hasattr(runner, "is_running") and runner.is_running():
                return False
        except Exception:
            pass
        try:
            ex = getattr(win, "_executor", None)
            if ex is not None:
                if getattr(ex, "_current_incoming", None) is not None:
                    return False
                if getattr(ex, "_current_job", None) is not None:
                    return False
                if getattr(ex, "_pending_step_name", None) is not None:
                    return False
                inc = getattr(ex, "_incoming", None)
                if inc is not None and hasattr(inc, "__len__") and len(inc) > 0:
                    return False
                jq = getattr(ex, "_jobs", None)
                if jq is not None and hasattr(jq, "__len__") and len(jq) > 0:
                    return False
        except Exception:
            pass
        try:
            mid = getattr(win, "_middle", None)
            line = getattr(mid, "_status_line", None)
            if line is not None and hasattr(line, "text"):
                if (line.text() or "").strip() != "Idle":
                    return False
        except Exception:
            pass
        return True

    return _wait_until(app, _idle, timeout_sec, step_sec=0.05)


def _wait_until_queue_idle(app: Any, win: Any, timeout_sec: float) -> bool:
    """Wait until the middle panel queue label reads 'Idle'."""

    def _idle_text() -> bool:
        try:
            mid = getattr(win, "_middle", None)
            line = getattr(mid, "_status_line", None)
            if line is None or not hasattr(line, "text"):
                return False
            return (line.text() or "").strip() == "Idle"
        except Exception:
            return False

    return _wait_until(app, _idle_text, timeout_sec, step_sec=0.05)


def _wait_until_runcontrols_idle(app: Any, controls: Any, timeout_sec: float) -> bool:
    """Wait until a RunControls widget shows 'Idle'."""

    def _idle_text() -> bool:
        try:
            lbl = getattr(controls, "_state", None)
            if lbl is None or not hasattr(lbl, "text"):
                return False
            return (lbl.text() or "").strip() == "Idle"
        except Exception:
            return False

    return _wait_until(app, _idle_text, timeout_sec, step_sec=0.05)


def _rm_tree_contents(path: Path) -> None:
    """
    Remove all contents under `path` (including dotdirs), but keep the directory itself.
    """
    path.mkdir(parents=True, exist_ok=True)
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except FileNotFoundError:
                pass


def _atomic_copy_into_watchdir(src: Path, watchdir: Path) -> Path:
    """
    Copy into a temporary *non-tif* name inside watchdir, then atomically rename to `.tif`.

    Rationale:
    - Creating a `.tif` temp file inside watchdir triggers watchdog `created()` and the app may briefly
      show Queue=2 (temp name + final name).
    - Copying with `copy2()` preserves mtime and can be ignored by the watcher "new file" heuristic.
    - A `.part` extension avoids enqueue on creation; the final rename to `.tif` triggers `moved()`,
      which the watcher treats as a new arrival.
    """
    watchdir.mkdir(parents=True, exist_ok=True)
    if not src.is_file():
        raise FileNotFoundError(str(src))
    dest = watchdir / src.name
    fd, tmp = tempfile.mkstemp(prefix=dest.stem + "_", suffix=".part", dir=str(watchdir))
    os.close(fd)
    tmp_p = Path(tmp)
    try:
        shutil.copyfile(src, tmp_p)
        try:
            os.utime(tmp_p, None)
        except Exception:
            pass
        os.replace(str(tmp_p), str(dest))
        return dest
    finally:
        if tmp_p.exists():
            try:
                tmp_p.unlink()
            except Exception:
                pass


def _set_pathfield_text_by_label(form: Any, *, label: str, text: str) -> bool:
    """
    Best-effort: set a SkillForm PathField by its row label (positional or option).
    """
    try:
        from guisaxs_skills.ui.path_field import PathField

        # Positional rows: meta.positional_params aligned to _pos_widgets
        meta = getattr(form, "_meta", None)
        if meta is not None:
            pos_params = getattr(meta, "positional_params", [])
            widgets = getattr(form, "_pos_widgets", [])
            for i, p in enumerate(pos_params):
                if str(getattr(p, "name", "")) == label and i < len(widgets):
                    w = widgets[i]
                    if isinstance(w, PathField):
                        w.set_text(text)
                        return True
        # Option rows: stored in _opt_fields by name
        opt_fields = getattr(form, "_opt_fields", {}) or {}
        w2 = opt_fields.get(label)
        if isinstance(w2, PathField):
            w2.set_text(text)
            return True
    except Exception:
        return False
    return False


def _get_pathfield_text_by_label(form: Any, *, label: str) -> str:
    """Best-effort: read a SkillForm PathField text by its row label."""
    try:
        from guisaxs_skills.ui.path_field import PathField

        meta = getattr(form, "_meta", None)
        if meta is not None:
            pos_params = getattr(meta, "positional_params", [])
            widgets = getattr(form, "_pos_widgets", [])
            for i, p in enumerate(pos_params):
                if str(getattr(p, "name", "")) == label and i < len(widgets):
                    w = widgets[i]
                    if isinstance(w, PathField):
                        return (w.text() or "").strip()
        opt_fields = getattr(form, "_opt_fields", {}) or {}
        w2 = opt_fields.get(label)
        if isinstance(w2, PathField):
            return (w2.text() or "").strip()
    except Exception:
        return ""
    return ""


def _set_form_text_field(form: Any, *, name: str, text: str) -> bool:
    """Set a SkillForm option text field (QLineEdit) by option name."""
    try:
        from PyQt5.QtWidgets import QLineEdit

        opt_fields = getattr(form, "_opt_fields", {}) or {}
        w = opt_fields.get(name)
        if isinstance(w, QLineEdit):
            w.setText(str(text))
            return True
    except Exception:
        return False
    return False


def test_executor_requeues_cancelled_job_before_normal_jobs(tmp_path: Path):
    """
    Unit-ish check: when cancel_current() causes a step to fail, the executor requeues the current job
    with priority between rerun (100) and normal (0), i.e. it should be chosen before normal jobs.
    """

    class _DummySignal:
        def connect(self, _fn):
            return None

    class _DummyRunner:
        def __init__(self):
            self.finished = _DummySignal()

        def is_running(self) -> bool:
            return False

        def cancel(self) -> None:
            return None

        def start(self, _req) -> None:
            return None

    runner = _DummyRunner()
    state = LiveviewSessionState(watchdir=tmp_path)
    ex = LiveviewJobExecutor(state=state, runner=runner)  # type: ignore[arg-type]

    current = Job(
        id="cur",
        priority=0,
        steps=[JobStep(name="integrate", request=RunRequest("integrate", [], {}))],
        context={"tiff_path": str(tmp_path / "a.tif")},
    )
    normal = Job(
        id="norm",
        priority=0,
        steps=[JobStep(name="integrate", request=RunRequest("integrate", [], {}))],
        context={},
    )
    rerun = Job(
        id="rerun",
        priority=100,
        steps=[JobStep(name="subtract", request=RunRequest("subtract", [], {}))],
        context={},
    )

    ex._current_job = current  # noqa: SLF001
    ex._pending_step_name = "integrate"  # noqa: SLF001
    ex._jobs.put(normal)  # noqa: SLF001
    ex._jobs.put(rerun)  # noqa: SLF001

    ex.cancel_current()
    from guisaxs_skills.logic.runner_qprocess import RunOutcome

    ex._on_skill_finished(RunOutcome(success=False, exit_code=15, result={}, request=None))  # noqa: SLF001

    j1 = ex._jobs.get_nowait()  # noqa: SLF001
    assert j1 is not None and j1.id == "rerun"
    j2 = ex._jobs.get_nowait()  # noqa: SLF001
    assert j2 is not None and j2.id.startswith("cur:retry:")
    j3 = ex._jobs.get_nowait()  # noqa: SLF001
    assert j3 is not None and j3.id == "norm"


def _assert_curves_match_validation(*, integrated_path: Path, subtracted_path: Path) -> None:
    """
    Compare liveview outputs to validation reference .chi / reference_subtracted using the same
    metric+baseline files as test_skills_real_data.
    """
    from autosaxs.core.utils import integration_comparison_metric, read_chi, read_reference_sub_dat, read_saxs
    from test_skills_real_data import (
        METRICS_INTEGRATED_CSV,
        METRICS_SUBTRACTED_CSV,
        REFERENCE_DIR,
        REFERENCE_SUBTRACTED_DIR,
        _compare_metrics,
        _int_dat_to_ref_basename,
        _read_metrics_csv,
        _strip_leading_number_codes,
    )

    assert integrated_path.is_file()
    assert subtracted_path.is_file()

    # Integrated sample (int_<stem>.dat vs reference/*.chi)
    int_base = integrated_path.stem
    ref_chi_base = _int_dat_to_ref_basename(int_base)
    assert ref_chi_base, f"Could not map {int_base!r} to a reference .chi basename"
    ref_chi_path = Path(REFERENCE_DIR) / (ref_chi_base + ".chi")
    assert ref_chi_path.is_file(), f"Missing reference chi: {ref_chi_path}"
    q_pipe, I_pipe, _, _ = read_saxs(str(integrated_path))
    q_ref, I_ref = read_chi(str(ref_chi_path))
    metric_int = integration_comparison_metric(q_pipe, I_pipe, q_ref, I_ref)
    assert metric_int == metric_int, "Integrated comparison metric is NaN"
    row_int = {
        "reference": ref_chi_base + ".chi",
        "generated": integrated_path.name,
        "metric": float(metric_int),
    }
    old_int = _read_metrics_csv(METRICS_INTEGRATED_CSV)
    key_int = (row_int["reference"], row_int["generated"])
    assert key_int in old_int, (
        f"Missing baseline in {METRICS_INTEGRATED_CSV} for {key_int}; "
        "run the validation pipeline to record metrics."
    )
    assert _compare_metrics(old_int, [row_int], label="guisaxs-liveview integrated (ihs27)")

    # Subtracted (sub_<stem>.dat vs reference_subtracted/sub_*.dat selected by Parent sample)
    sub_base = subtracted_path.stem
    stem = _strip_leading_number_codes(ref_chi_base)
    best_name: Optional[str] = None
    best_path: Optional[Path] = None
    sub_pat = Path(REFERENCE_SUBTRACTED_DIR)
    for p in sorted(sub_pat.glob("sub_*.dat")):
        try:
            _, _, sample_basename = read_reference_sub_dat(str(p))
        except ValueError:
            continue
        if _strip_leading_number_codes(sample_basename) == stem:
            best_name = p.name
            best_path = p
            break
    assert best_path is not None and best_path.is_file(), f"No reference_subtracted entry for sample stem {stem!r}"
    q_sub_ref, I_sub_ref, _ = read_reference_sub_dat(str(best_path))
    q_sub, I_sub, _, _ = read_saxs(str(subtracted_path))
    metric_sub = integration_comparison_metric(q_sub, I_sub, q_sub_ref, I_sub_ref)
    assert metric_sub == metric_sub, "Subtracted comparison metric is NaN"
    row_sub = {
        "reference": str(best_name),
        "generated": subtracted_path.name,
        "metric": float(metric_sub),
    }
    old_sub = _read_metrics_csv(METRICS_SUBTRACTED_CSV)
    key_sub = (row_sub["reference"], row_sub["generated"])
    assert key_sub in old_sub, (
        f"Missing baseline in {METRICS_SUBTRACTED_CSV} for {key_sub}; "
        "run the validation pipeline to record metrics."
    )
    assert _compare_metrics(old_sub, [row_sub], label="guisaxs-liveview subtracted (ihs27)")

    _ = sub_base  # keep variable for debugging readability


def test_guisaxs_liveview_calibrate_buffer_subtract_and_pr_outputs():
    timeout = _gui_timeout_sec()

    watchdir = Path(WORKSPACE_ROOT) / "test_liveview"
    _rm_tree_contents(watchdir)

    # --- Qt app + window
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    from PyQt5.QtWidgets import QApplication

    from guisaxs_skills.core.event_bus import EventBus
    from guisaxs_skills.liveview.state import AnalysisMode
    from guisaxs_skills.liveview.window import LiveviewMainWindow

    created_app = QApplication.instance() is None
    app = QApplication.instance() or QApplication([])
    bus = EventBus()
    win = LiveviewMainWindow(bus=bus, watchdir=watchdir)
    win.show()

    try:
        # --- Set calibration (open wizard, fill, run, wait, close wizard)
        left = win._left  # noqa: SLF001 (test)
        QTest.mouseClick(left._cal_open, Qt.LeftButton)  # noqa: SLF001
        assert left._cal_wizard is not None  # noqa: SLF001
        wiz = left._cal_wizard  # noqa: SLF001
        form = wiz._form  # noqa: SLF001

        calib = Path(VALIDATION_RAW) / "AgBh700_96.9_calib.tif"
        cfg = Path(VALIDATION_DIR) / "config.conf"
        mask = Path(VALIDATION_DIR) / "mask_fti2d_1225.msk"
        for p in (calib, cfg, mask):
            assert p.is_file(), f"Missing validation fixture: {p}"

        # Setting the calib image should auto-fill config + mask.
        assert _set_pathfield_text_by_label(form, label="calib_image", text=str(calib))
        # Force the same refresh logic as a real user edit would trigger.
        try:
            getattr(form, "_on_primary_path_expression_changed")()  # type: ignore[attr-defined]
        except Exception:
            pass
        _wait_until(
            app,
            lambda: Path(_get_pathfield_text_by_label(form, label="config_path")).name == "config.conf",
            2.0,
            step_sec=0.05,
        )
        _wait_until(
            app,
            lambda: bool(_get_pathfield_text_by_label(form, label="mask")),
            2.0,
            step_sec=0.05,
        )
        # Assert smart defaults guessed both config+mask; then force explicit values for determinism.
        assert Path(_get_pathfield_text_by_label(form, label="config_path")).name == "config.conf"
        assert _get_pathfield_text_by_label(form, label="mask"), "Mask path was not auto-filled"
        assert _set_pathfield_text_by_label(form, label="config_path", text=str(cfg))
        assert _set_pathfield_text_by_label(form, label="mask", text=str(mask))

        QTest.mouseClick(wiz._controls.run_button, Qt.LeftButton)  # noqa: SLF001

        ok_cal = _wait_until(app, lambda: (watchdir / "calibration" / "integrator").is_dir(), timeout)
        assert ok_cal, "Calibration did not produce calibration/integrator within timeout"
        assert (watchdir / "calibration" / "refined.yml").is_file()
        # User-facing indicator: wizard state label becomes Idle.
        assert _wait_until_runcontrols_idle(app, wiz._controls, timeout), "Calibration wizard did not become Idle"
        _settle_after_idle(1.0)
        wiz.close()
        _wait_until(app, lambda: not wiz.isVisible(), 3.0)
        # Ensure calibration subprocess has fully finished and pipeline is idle before uploading TIFFs.
        assert _wait_until_queue_idle(app, win, timeout), "Queue did not become Idle after calibration"
        _settle_after_idle(1.0)
        assert _wait_until_app_idle(app, win, timeout), "App did not become idle after calibration"
        _settle_after_idle(1.0)

        # --- Upload buffer TIFF (must be NEW after watcher start)
        buffer_src = Path(VALIDATION_RAW) / "ihs27_buffer.tif"
        assert buffer_src.is_file()
        _atomic_copy_into_watchdir(buffer_src, watchdir)
        int_buf = watchdir / "averaged" / "int_ihs27_buffer.dat"
        ok_buf = _wait_until(app, lambda: int_buf.is_file() and int_buf.stat().st_size > 0, timeout)
        assert ok_buf, f"Buffer integration did not produce {int_buf}"
        assert _wait_until_queue_idle(app, win, timeout), "Queue did not become Idle after buffer integration"
        _settle_after_idle(1.0)

        # --- Set buffer (open wizard, set qmin/qmax, apply, wait, close wizard)
        QTest.mouseClick(left._buf_open, Qt.LeftButton)  # noqa: SLF001
        assert left._buf_wizard is not None  # noqa: SLF001
        bw = left._buf_wizard  # noqa: SLF001
        bform = bw._form  # noqa: SLF001

        # subtract positional params are (sample_1d, buffer_1d); sample_1d row is hidden/disabled.
        assert _set_pathfield_text_by_label(bform, label="buffer_1d", text=str(int_buf))
        # Match validation baseline subtraction window from validation/config.conf (sub.q_range_abs).
        try:
            import yaml

            cfg_data = yaml.safe_load(Path(VALIDATION_DIR, "config.conf").read_text(encoding="utf-8"))
            sub = (cfg_data or {}).get("sub") if isinstance(cfg_data, dict) else None
            qra = sub.get("q_range_abs") if isinstance(sub, dict) else None
            q_min = float(qra[0]) if isinstance(qra, list) and len(qra) == 2 and qra[0] is not None else None
            q_max = float(qra[1]) if isinstance(qra, list) and len(qra) == 2 and qra[1] is not None else None
        except Exception:
            q_min = 4.5
            q_max = 5.5
        assert q_min is not None and q_max is not None, "config.conf sub.q_range_abs must provide [q_min, q_max]"
        assert _set_form_text_field(bform, name="q_min", text=str(q_min))
        assert _set_form_text_field(bform, name="q_max", text=str(q_max))
        QTest.mouseClick(bw._apply, Qt.LeftButton)  # noqa: SLF001
        ok_state_c = _wait_until(app, lambda: win._state.buffer_dat_path is not None, timeout)  # noqa: SLF001
        assert ok_state_c, "Buffer did not apply (state not updated)"
        _settle_after_idle(1.0)
        bw.close()
        _wait_until(app, lambda: not bw.isVisible(), 3.0)

        # --- Enable analysis mode: Monodisperse p(r)
        right = win._right  # noqa: SLF001
        idx_pr = None
        for i in range(right._mode_combo.count()):  # noqa: SLF001
            if right._mode_combo.itemData(i) == AnalysisMode.MONODISPERSE_PR:  # noqa: SLF001
                idx_pr = i
                break
        assert idx_pr is not None, "Analysis mode 'Monodisperse analysis: p(r)' not found in combo"
        right._mode_combo.setCurrentIndex(int(idx_pr))  # noqa: SLF001
        _process_events(app)

        # --- Upload sample TIFF -> expect subtraction + p(r) artifacts
        sample_src = Path(VALIDATION_RAW) / "ihs27_95.9_sample.tif"
        assert sample_src.is_file()
        _atomic_copy_into_watchdir(sample_src, watchdir)

        int_sam = watchdir / "averaged" / "int_ihs27_95.9_sample.dat"
        sub_out = watchdir / "subtracted" / "sub_ihs27_95.9_sample.dat"
        ok_outputs = _wait_until(
            app,
            lambda: int_sam.is_file()
            and int_sam.stat().st_size > 0
            and sub_out.is_file()
            and sub_out.stat().st_size > 0,
            timeout,
        )
        assert ok_outputs, "Expected integrated and subtracted outputs not found in time"
        assert _wait_until_queue_idle(app, win, timeout), "Queue did not become Idle after sample processing"
        _settle_after_idle(1.0)

        # fit_distances: per-sample subdir under fit_distances/<stem>/.
        # The p(r) PNG naming is `datgnom_rg_*.png` (not necessarily `*_pr.png`).
        fd_dir = watchdir / "fit_distances"
        sample_token = "ihs27_95.9_sample"
        ok_pr = _wait_until(
            app,
            lambda: any(p.is_file() and p.stat().st_size > 0 for p in fd_dir.rglob("*_fits.png"))
            and any(
                p.is_file()
                and p.stat().st_size > 0
                and p.name.endswith(".png")
                and not p.name.endswith("_fits.png")
                for p in fd_dir.rglob("*.png")
            )
            and any(sample_token in p.name for p in fd_dir.rglob("*_fit_distances_best.yml")),
            timeout,
            step_sec=0.1,
        )
        assert ok_pr, "fit_distances did not produce expected fit + p(r) artifacts"

        _assert_curves_match_validation(integrated_path=int_sam, subtracted_path=sub_out)
    finally:
        # Stop live background workers first (watchdog thread + timer ticks + runner subprocess).
        try:
            try:
                win._watcher.stop()  # noqa: SLF001
            except Exception:
                pass
            try:
                win._executor.stop()  # noqa: SLF001
            except Exception:
                pass
        except Exception:
            pass
        try:
            # Avoid "QProcess destroyed while process is still running" warnings.
            try:
                runner = win._runner  # noqa: SLF001
                if runner is not None and runner.is_running():
                    runner.cancel()
                    _wait_until(app, lambda: not runner.is_running(), 8.0)
            except Exception:
                pass
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

