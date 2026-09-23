"""
GUI scenario test for guisaxs-liveview: drive the real PyQt GUI (no pixel checks).

Primary monodisperse scenario (``test_guisaxs_liveview_monodisperse_scenario``):
- Launch Liveview on ``WORKSPACE_ROOT/test_liveview``
- Calibrate once (validation AgBh + mask)
- For **three** consecutive buffer–sample pairs (ihs27, ihs28, ihs29):
  - Reset buffer between pairs (disarms analysis)
  - Integrate buffer → set buffer + subtract q-window → re-arm monodisperse
  - Integrate sample → subtract → Guinier → Kratky → fit_distances (auto)
  - When ``reference_mono`` has refine knobs: monodisperse → Adjust → set params → Confirm
- ``model_dam`` (DAMMIF) once only (ihs27), with ``n_runs=3`` via Re-run shape after Adjust
- Closing the main window aborts waits (test must not keep driving a dead app)
- Compare integrated/subtracted curves to validation regression baselines

Headless CI::
  xvfb-run -a /path/to/python -m pytest tests/test_guisaxs_liveview.py -v

Non-headless debug (visible window)::
  GUISAXS_LIVEVIEW_TEST_TIMEOUT=1800 /path/to/python -m pytest \\
    tests/test_guisaxs_liveview.py::test_guisaxs_liveview_monodisperse_scenario -v -s
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, List, Optional, Tuple

import pytest
from guisaxs_skills.liveview.pipeline import Job, JobStep, LiveviewJobExecutor
from guisaxs_skills.liveview.session.sample_store import SampleStore
from guisaxs_skills.core.models import RunRequest
from guisaxs_skills.liveview.session import LiveviewSessionState
from guisaxs_skills.liveview.session.state import MonodisperseShapeMode

_REPOS = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_SRC = os.path.join(_REPOS, "src")
_TESTS_DIR = os.path.join(_REPOS, "tests")
for _p in (_SRC, _REPOS, _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

WORKSPACE_ROOT = os.path.abspath(os.path.join(_REPOS, ".."))
VALIDATION_DIR = os.path.join(WORKSPACE_ROOT, "validation")
VALIDATION_RAW = os.path.join(VALIDATION_DIR, "raw")

_VALIDATION_MISSING_MSG = (
    f"Validation directory not found: {VALIDATION_DIR}. "
    "Run: python scripts/setup_validation_data.py"
)

# (protocol_key, buffer_tif, sample_tif, sample_stem_for_artifacts)
_MONO_SCENARIO_PAIRS: List[Tuple[str, str, str, str]] = [
    ("ihs27", "ihs27_buffer.tif", "ihs27_95.9_sample.tif", "ihs27_95.9_sample"),
    ("ihs28", "ihs28_buffer.tif", "ihs28_95.2_sample.tif", "ihs28_95.2_sample"),
    ("ihs29", "ihs29_buffer.tif", "ihs29_94.6_sample.tif", "ihs29_94.6_sample"),
]
_MONO_DAM_KEY = "ihs27"
_MONO_DAM_N_RUNS = 3
REFERENCE_MONO_MANIFEST = os.path.join(VALIDATION_DIR, "reference_mono", "manifest.yml")


@pytest.fixture(scope="module", autouse=True)
def _require_validation_dir_fixture():
    if not os.path.isdir(VALIDATION_DIR):
        raise FileNotFoundError(_VALIDATION_MISSING_MSG)


def _gui_timeout_sec() -> float:
    return float(os.environ.get("GUISAXS_LIVEVIEW_TEST_TIMEOUT", "1800"))


def _process_events(app: Any) -> None:
    try:
        app.processEvents()
    except Exception:
        return


def _wait_until(
    app: Any,
    predicate,
    timeout_sec: float,
    *,
    step_sec: float = 0.05,
    abort_if=None,
) -> bool:
    deadline = time.monotonic() + float(timeout_sec)
    while time.monotonic() < deadline:
        if abort_if is not None:
            try:
                if abort_if():
                    return False
            except Exception:
                return False
        _process_events(app)
        try:
            if predicate():
                return True
        except Exception:
            pass
        time.sleep(max(0.01, float(step_sec)))
    return False


def _window_gone(win: Any) -> bool:
    """True when the liveview window was closed / destroyed (stop waiting)."""
    try:
        return win is None or (hasattr(win, "isVisible") and not win.isVisible())
    except RuntimeError:
        return True


def _settle_after_idle(sec: float = 1.0) -> None:
    """Small delay after UI becomes Idle to avoid races."""
    time.sleep(max(0.0, float(sec)))


def _wait_until_app_idle(app: Any, win: Any, timeout_sec: float) -> bool:
    """
    Best-effort: wait until the liveview window looks idle (no running skill, no queued items).
    Uses private attributes but stays defensive.
    """
    abort = lambda: _window_gone(win)

    def _idle() -> bool:
        try:
            runner = getattr(getattr(win, "_controller", None), "runner", None)
            if runner is None:
                runner = getattr(win, "_runner", None)
            if runner is not None and hasattr(runner, "is_running") and runner.is_running():
                return False
        except Exception:
            pass
        try:
            ctrl = getattr(win, "_controller", None)
            ex = getattr(ctrl, "executor", None) if ctrl is not None else None
            if ex is None:
                ex = getattr(win, "_executor", None)
            if ex is not None:
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

    return _wait_until(app, _idle, timeout_sec, step_sec=0.05, abort_if=abort)


def _wait_until_queue_idle(app: Any, win: Any, timeout_sec: float) -> bool:
    """Wait until the middle panel queue label reads 'Idle'."""
    abort = lambda: _window_gone(win)

    def _idle_text() -> bool:
        try:
            mid = getattr(win, "_middle", None)
            line = getattr(mid, "_status_line", None)
            if line is None or not hasattr(line, "text"):
                return False
            return (line.text() or "").strip() == "Idle"
        except Exception:
            return False

    return _wait_until(app, _idle_text, timeout_sec, step_sec=0.05, abort_if=abort)


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
    - A `.part` extension avoids enqueue on creation; the final rename to `.tif` triggers `moved()`.
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


def _right_mode_combo(right: Any):
    """Deprecated — analysis mode combo removed; kept as None for old callers."""
    return None


def _arm_monodisperse(right: Any) -> None:
    """Open/arm monodisperse analysis window (window-open arming model)."""
    right.show_monodisperse_wizard()


def _subtract_q_window_from_validation_config() -> Tuple[float, float]:
    try:
        import yaml

        cfg_data = yaml.safe_load(Path(VALIDATION_DIR, "config.conf").read_text(encoding="utf-8"))
        sub = (cfg_data or {}).get("subtract") if isinstance(cfg_data, dict) else None
        if isinstance(sub, dict) and sub.get("q_min") is not None and sub.get("q_max") is not None:
            return float(sub["q_min"]), float(sub["q_max"])
    except Exception:
        pass
    return 4.5, 5.5


def _reset_buffer_via_ui(app: Any, win: Any, left: Any, timeout: float) -> None:
    """Click Buffer Reset — clears buffer and disarms analysis."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest

    QTest.mouseClick(left._buf_reset, Qt.LeftButton)  # noqa: SLF001
    _process_events(app)
    ok = _wait_until(
        app,
        lambda: win._state.buffer_dat_path is None and not win._state.monodisperse_armed,  # noqa: SLF001
        min(30.0, timeout),
    )
    assert ok, "Buffer reset did not clear buffer / disarm analysis"
    _settle_after_idle(0.5)


def _set_buffer_and_subtract_options(
    app: Any,
    win: Any,
    left: Any,
    *,
    int_buf: Path,
    q_min: float,
    q_max: float,
    timeout: float,
) -> None:
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest

    QTest.mouseClick(left._buf_open, Qt.LeftButton)  # noqa: SLF001
    assert left._buf_wizard is not None  # noqa: SLF001
    bw = left._buf_wizard  # noqa: SLF001
    bform = bw._form  # noqa: SLF001
    assert _set_pathfield_text_by_label(bform, label="buffer_1d", text=str(int_buf))
    assert _set_form_text_field(bform, name="q_min", text=str(q_min))
    assert _set_form_text_field(bform, name="q_max", text=str(q_max))
    QTest.mouseClick(bw._apply, Qt.LeftButton)  # noqa: SLF001
    ok_state = _wait_until(app, lambda: win._state.buffer_dat_path is not None, timeout)  # noqa: SLF001
    assert ok_state, f"Buffer did not apply for {int_buf.name}"
    _settle_after_idle(0.5)
    bw.close()
    _wait_until(app, lambda: not bw.isVisible(), 3.0)


def _configure_monodisperse_shape(
    app: Any,
    right: Any,
    win: Any,
    *,
    enable_dammif: bool,
) -> None:
    """Arm mono wizard and set shape mode (DAMMIF once, else none)."""
    _arm_monodisperse(right)
    assert right._state.monodisperse_armed  # noqa: SLF001
    wiz = right.monodisperse_wizard
    pane = wiz.shape_pane
    if enable_dammif:
        pane.set_n_runs(_MONO_DAM_N_RUNS)
        pane.set_shape_mode("dammif")
        win._state.monodisperse_shape_mode = MonodisperseShapeMode.DAMMIF  # noqa: SLF001
        win._state.model_dam_n_runs = _MONO_DAM_N_RUNS  # noqa: SLF001
    else:
        pane.set_shape_mode("none")
        win._state.monodisperse_shape_mode = MonodisperseShapeMode.NONE  # noqa: SLF001
    _process_events(app)
    assert pane.shape_mode() == ("dammif" if enable_dammif else "none")


def _load_mono_refine_params(protocol_key: str) -> Optional[dict]:
    """Return refine knobs from ``reference_mono/manifest.yml``, or None for DATGNOM-only."""
    if not os.path.isfile(REFERENCE_MONO_MANIFEST):
        return None
    import yaml

    raw = yaml.safe_load(Path(REFERENCE_MONO_MANIFEST).read_text(encoding="utf-8")) or {}
    sample = (raw.get("samples") or {}).get(protocol_key) or {}
    if str(sample.get("mode") or "").strip().lower() != "refine":
        return None
    out = {
        "q_min": sample.get("q_min"),
        "q_max": sample.get("q_max"),
        "dmax_nm": sample.get("dmax_nm"),
        "alpha": sample.get("alpha"),
        "force_zero_rmin": sample.get("force_zero_rmin", "N"),
        "force_zero_rmax": sample.get("force_zero_rmax", "N"),
    }
    if out["q_min"] is None or out["q_max"] is None or out["dmax_nm"] is None:
        return None
    return out


def _adjust_gnom_via_ui(
    app: Any,
    win: Any,
    right: Any,
    *,
    params: dict,
    timeout: float,
) -> None:
    """monodisperse → P(r) Adjust → set manual params → Confirm (refine fit_distances)."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest

    abort = lambda: _window_gone(win)
    _arm_monodisperse(right)
    coord = right.monodisperse_coordinator
    coord.open_gnom_adjust_wizard()
    _process_events(app)
    adj = coord._gnom_adjust  # noqa: SLF001
    assert adj is not None and adj.isVisible(), "GNOM Adjust wizard did not open"
    adj.set_params(params, emit=True)
    _process_events(app)
    adj._confirm.refresh()  # noqa: SLF001
    assert adj._confirm.button.isEnabled(), "Confirm disabled — params may match committed snapshot"  # noqa: SLF001
    # Confirm writes refine conf + re-runs fit_distances
    QTest.mouseClick(adj._confirm.button, Qt.LeftButton)  # noqa: SLF001
    _process_events(app)
    ok = _wait_until_app_idle(app, win, timeout)
    assert ok and not abort(), "App did not become idle after GNOM Adjust Confirm"
    _settle_after_idle(0.5)
    try:
        adj.close()
        _wait_until(app, lambda: not adj.isVisible(), 3.0, abort_if=abort)
    except Exception:
        pass
    # Adjust pauses auto-processing; resume so later shape / pairs can proceed.
    if not win._state.is_auto_processing():  # noqa: SLF001
        win._controller.monodisperse.on_resume_queue()  # noqa: SLF001
        _process_events(app)


def _run_dammif_via_ui(app: Any, win: Any, right: Any, *, timeout: float) -> None:
    """Set DAMMIF n_runs and Re-run shape on the current profile."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest

    abort = lambda: _window_gone(win)
    _configure_monodisperse_shape(app, right, win, enable_dammif=True)
    pane = right.monodisperse_wizard.shape_pane
    assert pane.n_runs() == _MONO_DAM_N_RUNS
    QTest.mouseClick(pane._rerun, Qt.LeftButton)  # noqa: SLF001
    _process_events(app)
    ok = _wait_until_app_idle(app, win, timeout)
    assert ok and not abort(), "App did not become idle after Re-run shape (model_dam)"
    if not win._state.is_auto_processing():  # noqa: SLF001
        win._controller.monodisperse.on_resume_queue()  # noqa: SLF001
        _process_events(app)


def _wait_mono_analysis_artifacts(
    app: Any,
    win: Any,
    watchdir: Path,
    *,
    sample_token: str,
    expect_dam: bool,
    timeout: float,
) -> None:
    guinier_res = watchdir / "guinier_mono" / sample_token / f"{sample_token}_results.txt"
    kratky_yml = watchdir / "analyze_kratky" / sample_token / f"{sample_token}_kratky_params.yml"
    fd_log = watchdir / "fit_distances" / sample_token / f"{sample_token}_fit_distances_log.yml"
    dam_dir = watchdir / "dammif" / sample_token
    abort = lambda: _window_gone(win)

    def _ready() -> bool:
        if not (guinier_res.is_file() and guinier_res.stat().st_size > 0):
            return False
        if not (kratky_yml.is_file() and kratky_yml.stat().st_size > 0):
            return False
        if not (fd_log.is_file() and fd_log.stat().st_size > 0):
            return False
        if expect_dam:
            return (dam_dir / "dammif_fits.yml").is_file() or (dam_dir / "best.cif").exists()
        return True

    ok = _wait_until(app, _ready, timeout, step_sec=0.2, abort_if=abort)
    assert ok and not abort(), (
        f"Monodisperse artifacts incomplete for {sample_token} "
        f"(expect_dam={expect_dam}): guinier={guinier_res.is_file()} "
        f"kratky={kratky_yml.is_file()} fd={fd_log.is_file()} dam_dir={dam_dir}"
    )


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
    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=runner)  # type: ignore[arg-type]

    current = Job(
        id="cur",
        priority=0,
        steps=[JobStep(name="integrate", request=RunRequest("integrate", [], {}))],
        context={"tiff_path": str(tmp_path / "a.tif")},
    )
    current = current.mark_step_done("integrate", result={"integrated_1d": "x.dat"})
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
    assert j2.completed.results.get("integrate") == {"integrated_1d": "x.dat"}
    j3 = ex._jobs.get_nowait()  # noqa: SLF001
    assert j3 is not None and j3.id == "norm"


def test_executor_paused_starts_manual_jobs_only(tmp_path: Path):
    class _DummySignal:
        def connect(self, _fn):
            return None

    class _DummyRunner:
        def __init__(self):
            self.finished = _DummySignal()
            self.started: list[RunRequest] = []

        def is_running(self) -> bool:
            return False

        def cancel(self) -> None:
            return None

        def start(self, req) -> None:
            self.started.append(req)

    runner = _DummyRunner()
    state = LiveviewSessionState(watchdir=tmp_path)
    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=runner)  # type: ignore[arg-type]

    auto = Job(
        id="auto",
        priority=0,
        steps=[JobStep(name="integrate", request=RunRequest("integrate", [], {}))],
        context={"tiff_path": str(tmp_path / "a.tif")},
    )
    manual = Job(
        id="manual",
        priority=150,
        steps=[JobStep(name="fit_guinier", request=RunRequest("fit_guinier", ["prof.dat"], {}))],
        context={"manual": True, "monodisperse": True},
    )
    ex._jobs.put(auto)  # noqa: SLF001
    ex._jobs.put(manual)  # noqa: SLF001
    state.set_auto_processing(False)

    ex._tick()  # noqa: SLF001
    ex._tick()  # noqa: SLF001

    assert ex._current_job is not None and ex._current_job.id == "manual"  # noqa: SLF001
    assert runner.started and runner.started[0].skill_name == "fit_guinier"
    assert len(ex._jobs) == 1  # noqa: SLF001
    assert ex._jobs.get_nowait().id == "auto"  # noqa: SLF001


def test_executor_paused_advances_manual_multi_step_job(tmp_path: Path):
    class _DummySignal:
        def connect(self, _fn):
            return None

    class _DummyRunner:
        def __init__(self):
            self.finished = _DummySignal()
            self.started: list[str] = []

        def is_running(self) -> bool:
            return False

        def cancel(self) -> None:
            return None

        def start(self, req) -> None:
            self.started.append(req.skill_name)

    runner = _DummyRunner()
    state = LiveviewSessionState(watchdir=tmp_path)
    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=runner)  # type: ignore[arg-type]
    state.set_auto_processing(False)

    manual = Job(
        id="chain",
        priority=150,
        steps=[
            JobStep(name="fit_guinier", request=RunRequest("fit_guinier", ["prof.dat"], {})),
            JobStep(name="fit_distances", request=RunRequest("fit_distances", ["prof.dat"], {})),
        ],
        context={"manual": True, "monodisperse": True},
    )
    ex._start_job(manual)  # noqa: SLF001
    ex._tick()  # noqa: SLF001
    assert runner.started == ["fit_guinier"]

    from guisaxs_skills.logic.runner_qprocess import RunOutcome

    ex._on_skill_finished(  # noqa: SLF001
        RunOutcome(success=True, exit_code=0, result={"rg": 1.0}, request=None)
    )
    ex._tick()  # noqa: SLF001
    assert runner.started == ["fit_guinier", "fit_distances"]


def test_monodisperse_guinier_opts_fixed_interval_from_spinboxes(tmp_path: Path):
    class _DummySignal:
        def connect(self, _fn):
            return None

    class _DummyRunner:
        def __init__(self):
            self.finished = _DummySignal()

        def is_running(self) -> bool:
            return False

    prof = tmp_path / "sub_sample.dat"
    prof.write_text("# q I\n", encoding="utf-8")
    state = LiveviewSessionState(watchdir=tmp_path)
    state.monodisperse_wizard_params = {"first": 1, "last": 1}
    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=_DummyRunner())  # type: ignore[arg-type]

    steps = ex.monodisperse_steps_guinier_and_distances(
        str(prof),
        output_root=tmp_path,
        fixed_guinier_interval=True,
        guinier_interval_first=8,
        guinier_interval_last=32,
    )
    assert [s.name for s in steps] == ["fit_guinier", "analyze_kratky", "fit_distances"]
    g_opts = steps[0].request.options
    k_opts = steps[1].request.options
    d_opts = steps[2].request.options
    assert g_opts["first"] == 8
    assert g_opts["last"] == 32
    assert k_opts["rg_nm"] == "${fit_guinier.rg}"
    assert k_opts["i0"] == "${fit_guinier.i0}"
    assert d_opts["rg_nm"] == "${fit_guinier.rg}"
    assert d_opts["first"] == "${fit_guinier.first_point_1based}"
    # Guinier last must not be forwarded to DATGNOM (window too narrow for p(r)).
    assert "last" not in d_opts


def test_analysis_steps_both_armed_separate_guinier(tmp_path: Path):
    from guisaxs_skills.liveview.pipeline.executor import LiveviewJobExecutor
    from guisaxs_skills.liveview.pipeline.monodisperse_pipeline import (
        FIT_GUINIER_MONO_STEP,
        FIT_GUINIER_POLY_STEP,
    )

    class _DummySignal:
        def connect(self, _fn):
            return None

    class _DummyRunner:
        def __init__(self):
            self.finished = _DummySignal()

        def is_running(self) -> bool:
            return False

    prof = tmp_path / "int_sample.dat"
    prof.write_text("# q I\n", encoding="utf-8")
    state = LiveviewSessionState(watchdir=tmp_path)
    state.monodisperse_armed = True
    state.polydisperse_armed = True
    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=_DummyRunner())  # type: ignore[arg-type]
    steps = ex._analysis_steps_for_profile(str(prof), output_root=tmp_path)  # noqa: SLF001
    names = [s.name for s in steps]
    assert FIT_GUINIER_MONO_STEP in names
    assert FIT_GUINIER_POLY_STEP in names
    assert "fit_distances" in names
    assert "fit_sizes" in names
    g_mono = next(s for s in steps if s.name == FIT_GUINIER_MONO_STEP)
    g_poly = next(s for s in steps if s.name == FIT_GUINIER_POLY_STEP)
    assert "guinier_mono" in str(g_mono.request.options.get("output_dir", "")).replace("\\", "/")
    assert "guinier_poly" in str(g_poly.request.options.get("output_dir", "")).replace("\\", "/")


def test_polydisperse_steps_full_defaults(tmp_path: Path):
    from guisaxs_skills.liveview.pipeline.polydisperse_pipeline import (
        PolydispersePipelineParts,
        build_polydisperse_steps,
    )
    from guisaxs_skills.liveview.pipeline.monodisperse_pipeline import FIT_GUINIER_POLY_STEP
    from guisaxs_skills.liveview.session.state import PolydisperseMixtureMode

    class _DummySignal:
        def connect(self, _fn):
            return None

    class _DummyRunner:
        def __init__(self):
            self.finished = _DummySignal()

        def is_running(self) -> bool:
            return False

    prof = tmp_path / "sub_sample.dat"
    prof.write_text("# q I\n", encoding="utf-8")
    state = LiveviewSessionState(watchdir=tmp_path)
    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=_DummyRunner())  # type: ignore[arg-type]

    steps = build_polydisperse_steps(
        str(prof),
        output_root=tmp_path,
        state=state,
        parts=PolydispersePipelineParts.FULL,
        load_yaml=ex._load_yaml_options,  # noqa: SLF001
    )
    assert [s.name for s in steps] == [FIT_GUINIER_POLY_STEP, "fit_sizes"]
    s_opts = steps[1].request.options
    assert s_opts["shape"] == "spheres"
    assert s_opts["first"] == 1

    state.polydisperse_mixture_mode = PolydisperseMixtureMode.MIXTURE
    steps2 = build_polydisperse_steps(
        str(prof),
        output_root=tmp_path,
        state=state,
        parts=PolydispersePipelineParts.FULL,
        load_yaml=ex._load_yaml_options,  # noqa: SLF001
    )
    assert [s.name for s in steps2] == [FIT_GUINIER_POLY_STEP, "fit_sizes", "model_mixture"]
    m_opts = steps2[2].request.options
    assert "r_max" not in m_opts
    assert "poly_max" not in m_opts
    assert "r_min" not in m_opts
    assert "poly_min" not in m_opts
    assert "maxit" not in m_opts


def test_polydisperse_mixture_opts_include_explicit_bounds(tmp_path: Path):
    from guisaxs_skills.liveview.pipeline.polydisperse_pipeline import model_mixture_opts

    state = LiveviewSessionState(watchdir=tmp_path)
    state.polydisperse_window_params = {
        "mixture": {"max_nph": 2, "r_max": 9.5, "poly_max": 3.0},
    }
    opts = model_mixture_opts(state=state, output_root=tmp_path)
    assert opts["max_nph"] == 2
    assert opts["r_max"] == 9.5
    assert opts["poly_max"] == 3.0


def test_polydisperse_guinier_only_no_sizes_first_handoff(tmp_path: Path):
    from guisaxs_skills.liveview.pipeline.polydisperse_pipeline import (
        PolydispersePipelineParts,
        build_polydisperse_steps,
        fit_sizes_opts,
    )

    class _DummySignal:
        def connect(self, _fn):
            return None

    class _DummyRunner:
        def __init__(self):
            self.finished = _DummySignal()

        def is_running(self) -> bool:
            return False

    prof = tmp_path / "sub_sample.dat"
    prof.write_text("# q I\n", encoding="utf-8")
    state = LiveviewSessionState(watchdir=tmp_path)
    state.polydisperse_window_params = {"guinier_first": 5, "guinier_last": 20, "first": 1}
    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=_DummyRunner())  # type: ignore[arg-type]

    steps = build_polydisperse_steps(
        str(prof),
        output_root=tmp_path,
        state=state,
        parts=PolydispersePipelineParts.GUINIER_ONLY,
        load_yaml=ex._load_yaml_options,  # noqa: SLF001
        fixed_guinier_interval=True,
        guinier_interval_first=5,
        guinier_interval_last=20,
    )
    assert [s.name for s in steps] == ["fit_guinier_poly"]
    assert steps[0].request.options["first"] == 5
    assert steps[0].request.options["last"] == 20

    s_opts = fit_sizes_opts(state=state, output_root=tmp_path, load_yaml=ex._load_yaml_options)  # noqa: SLF001
    assert s_opts["first"] == 1
    assert s_opts["shape"] == "spheres"


def test_monodisperse_step_shape_dammif_uses_concrete_gnom_path(tmp_path: Path):
    class _DummySignal:
        def connect(self, _fn):
            return None

    class _DummyRunner:
        def __init__(self):
            self.finished = _DummySignal()

        def is_running(self) -> bool:
            return False

    prof = tmp_path / "sub_sample.dat"
    prof.write_text("# q I\n", encoding="utf-8")
    gnom_src = (
        Path(__file__).resolve().parents[2]
        / "validation/fit_distances_test/ihs30_94.0_sample/datgnom_rg_1.6800.out"
    )
    fd = tmp_path / "fit_distances" / "sample"
    fd.mkdir(parents=True)
    gnom = fd / "datgnom_rg_1.6800.out"
    gnom.write_bytes(gnom_src.read_bytes())

    state = LiveviewSessionState(watchdir=tmp_path)
    ex = LiveviewJobExecutor(state=state, sample_store=SampleStore(), runner=_DummyRunner())  # type: ignore[arg-type]

    step = ex.monodisperse_step_shape(
        str(prof),
        output_root=tmp_path,
        shape_mode="dammif",
        gnom_out_path=str(gnom),
    )
    assert step is not None
    assert step.name == "model_dam"
    assert step.request.options["gnom_path"] == str(gnom.resolve())
    assert "${" not in step.request.options["gnom_path"]

    fallback = ex.monodisperse_step_shape(
        str(prof),
        output_root=tmp_path,
        shape_mode="dammif",
        gnom_out_path=None,
    )
    assert fallback is not None
    assert fallback.name == "model_dam"
    assert fallback.request.options.get("gnom_path") == str(gnom.resolve())

    other_root = tmp_path / "other"
    other_root.mkdir()
    prof2 = other_root / "sub_other.dat"
    prof2.write_text("# q I\n", encoding="utf-8")
    no_gnom = ex.monodisperse_step_shape(
        str(prof2),
        output_root=other_root,
        shape_mode="dammif",
        gnom_out_path=None,
    )
    assert no_gnom is not None
    assert "gnom_path" not in no_gnom.request.options


def _assert_curves_match_validation(*, integrated_path: Path, subtracted_path: Path) -> None:
    """
    Compare liveview outputs to validation reference .chi / reference_subtracted using the same
    metric+baseline files as test_skills_real_data.
    """
    from autosaxs.core.utils import (
        integration_comparison_metric,
        read_chi,
        read_reference_sub_dat,
        read_saxs,
        subtraction_comparison_metric,
    )
    from test_skills_real_data import (
        METRICS_INTEGRATED_CHI2_CSV,
        METRICS_INTEGRATED_CSV,
        METRICS_SUBTRACTED_CHI2_CSV,
        METRICS_SUBTRACTED_CSV,
        _chi2_vs_reference,
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
    q_pipe, I_pipe, sigma_pipe, _ = read_saxs(str(integrated_path))
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
    row_int_chi2 = {
        "reference": ref_chi_base + ".chi",
        "generated": integrated_path.name,
        "metric": float(_chi2_vs_reference(q_ref, I_ref, q_pipe, I_pipe, sigma_pipe=sigma_pipe)),
    }
    old_int_chi2 = _read_metrics_csv(METRICS_INTEGRATED_CHI2_CSV)
    assert _compare_metrics(old_int_chi2, [row_int_chi2], label="guisaxs-liveview integrated chi2 (ihs27)")

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
    q_sub, I_sub, sigma_sub, _ = read_saxs(str(subtracted_path))
    metric_sub = subtraction_comparison_metric(q_sub_ref, I_sub_ref, q_sub, I_sub)
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
    row_sub_chi2 = {
        "reference": str(best_name),
        "generated": subtracted_path.name,
        "metric": float(_chi2_vs_reference(q_sub_ref, I_sub_ref, q_sub, I_sub, sigma_pipe=sigma_sub)),
    }
    old_sub_chi2 = _read_metrics_csv(METRICS_SUBTRACTED_CHI2_CSV)
    assert _compare_metrics(old_sub_chi2, [row_sub_chi2], label="guisaxs-liveview subtracted chi2 (ihs27)")

    _ = sub_base  # keep variable for debugging readability


def test_guisaxs_liveview_monodisperse_scenario():
    """
    End-to-end monodisperse GUI scenario: calib once, then 3 buffer–sample pairs
    with buffer reset between pairs; Adjust GNOM when refine knobs exist;
    model_dam (n_runs=3) only for ihs27 via Re-run shape. Closing the window aborts waits.
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
                print(f"  model_dam n_runs={_MONO_DAM_N_RUNS} for {key}", flush=True)
                _run_dammif_via_ui(app, win, right, timeout=timeout)
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
                # n_runs=3 should produce multiple dammif-* prefixes (or damaver).
                dam_runs = list(dam_dir.glob("dammif-*-1.cif")) or list(dam_dir.glob("dammif-*.cif"))
                assert len(dam_runs) >= 1, f"Expected DAMMIF outputs under {dam_dir}"

            _assert_curves_match_validation(integrated_path=int_sam, subtracted_path=sub_out)

        # Only one dammif sample directory should exist
        dam_root = watchdir / "dammif"
        if dam_root.is_dir():
            dam_samples = [p for p in dam_root.iterdir() if p.is_dir()]
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

