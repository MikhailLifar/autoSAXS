"""
GUI scenario test for guisaxs-liveview: drive the real PyQt GUI (no pixel checks).

Primary monodisperse scenario (``test_guisaxs_liveview_monodisperse_scenario``):
- Launch Liveview on a test watchdir (local disk on Windows; see ``_test_watchdir``)
- Calibrate once (validation AgBh + mask)
- For **three** consecutive buffer–sample pairs (ihs27, ihs28, ihs29):
  - Reset buffer between pairs (disarms analysis)
  - Integrate buffer → set buffer + subtract q-window → re-arm monodisperse
  - Integrate sample → subtract → Guinier → Kratky → fit_distances (auto)
  - When ``reference_mono`` has refine knobs: monodisperse → Adjust → set params → Confirm
- Closing the main window aborts waits (test must not keep driving a dead app)
- Compare integrated/subtracted curves to validation regression baselines

Shared helpers for commit-gate and optional liveview GUI tests.
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

_REPOS = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
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


def _test_watchdir(name: str) -> Path:
    """
    Watch-folder root for liveview GUI tests.

    VirtualBox shared folders (``Z:\\`` / ``\\\\VBoxSvr\\...``) are a common host for
    validation fixtures, but running the liveview watchdir + QProcess skill I/O there
    can abort the guest with ``STATUS_STACK_BUFFER_OVERRUN`` mid-pipeline. Prefer a
    local disk root on Windows; override with ``GUISAXS_LIVEVIEW_TEST_WORKDIR``.
    """
    override = (os.environ.get("GUISAXS_LIVEVIEW_TEST_WORKDIR") or "").strip()
    if override:
        root = Path(override)
    elif os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()) / "autosaxs-liveview-tests"
    else:
        root = Path(WORKSPACE_ROOT)
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    return path


# (protocol_key, buffer_tif, sample_tif, sample_stem_for_artifacts)
_MONO_SCENARIO_PAIRS: List[Tuple[str, str, str, str]] = [
    ("ihs27", "ihs27_buffer.tif", "ihs27_95.9_sample.tif", "ihs27_95.9_sample"),
    ("ihs28", "ihs28_buffer.tif", "ihs28_95.2_sample.tif", "ihs28_95.2_sample"),
    ("ihs29", "ihs29_buffer.tif", "ihs29_94.6_sample.tif", "ihs29_94.6_sample"),
]
_MONO_DAM_KEY = "ihs27"
_MONO_DAM_N_RUNS = 3
REFERENCE_MONO_MANIFEST = os.path.join(VALIDATION_DIR, "reference_mono", "manifest.yml")


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


def _copyfile_share_safe(src: Path, dst: Path) -> None:
    """
    Copy bytes to ``dst``.

    VirtualBox shared folders often make ``os.path.samefile()`` true for distinct
    paths (identical volume serial / file index), so ``shutil.copyfile`` raises
    ``SameFileError``. Byte copy skips that check.
    """
    try:
        shutil.copyfile(src, dst)
    except shutil.SameFileError:
        dst.write_bytes(Path(src).read_bytes())


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
        _copyfile_share_safe(src, tmp_p)
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


def _run_dammif_via_ui(
    app: Any,
    win: Any,
    right: Any,
    *,
    timeout: float,
    n_runs: Optional[int] = None,
) -> None:
    """Start guisaxs-shape → Confirm DAMMIF (IPC), wait for idle + child finish."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest

    abort = lambda: _window_gone(win)
    n = int(n_runs) if n_runs is not None else int(_MONO_DAM_N_RUNS)
    _configure_monodisperse_shape(app, right, win, enable_dammif=True)
    pane = right.monodisperse_wizard.shape_pane
    try:
        pane.set_n_runs(n)
    except Exception:
        pass
    win._state.model_dam_n_runs = n  # noqa: SLF001
    _process_events(app)

    finished: list[dict] = []
    modeling = right._modeling  # noqa: SLF001

    def _on_finished(msg: dict) -> None:
        finished.append(msg if isinstance(msg, dict) else {})

    QTest.mouseClick(pane._start, Qt.LeftButton)  # noqa: SLF001
    _process_events(app)
    _dismiss_modal_dialogs(app)

    ok_child = _wait_until(
        app,
        lambda: modeling.shape_child() is not None and modeling.shape_child().is_running(),
        min(30.0, timeout),
        abort_if=abort,
    )
    assert ok_child and not abort(), "guisaxs-shape child did not start"
    child = modeling.shape_child()
    assert child is not None
    child.finished_run.connect(_on_finished)
    child.send_confirm()
    _process_events(app)

    ok_done = _wait_until(
        app,
        lambda: bool(finished) or abort(),
        timeout,
        abort_if=abort,
    )
    assert ok_done and not abort(), "guisaxs-shape Confirm (model_dam) did not finish"
    assert finished and bool(finished[-1].get("success", False)), (
        f"model_dam Confirm failed: {finished[-1] if finished else None}"
    )
    ok_idle = _wait_until_app_idle(app, win, min(60.0, timeout))
    assert ok_idle and not abort(), "App did not become idle after shape Confirm"
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
    import importlib.util
    _hp = Path(__file__).resolve().parents[1] / "real_data" / "_helpers.py"
    _spec = importlib.util.spec_from_file_location("autosaxs_real_data_helpers", _hp)
    assert _spec and _spec.loader
    _H = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_H)
    METRICS_INTEGRATED_CHI2_CSV = _H.METRICS_INTEGRATED_CHI2_CSV
    METRICS_INTEGRATED_CSV = _H.METRICS_INTEGRATED_CSV
    METRICS_SUBTRACTED_CHI2_CSV = _H.METRICS_SUBTRACTED_CHI2_CSV
    METRICS_SUBTRACTED_CSV = _H.METRICS_SUBTRACTED_CSV
    _chi2_vs_reference = _H._chi2_vs_reference
    REFERENCE_DIR = _H.REFERENCE_DIR
    REFERENCE_SUBTRACTED_DIR = _H.REFERENCE_SUBTRACTED_DIR
    _compare_metrics = _H._compare_metrics
    _int_dat_to_ref_basename = _H._int_dat_to_ref_basename
    _read_metrics_csv = _H._read_metrics_csv
    _strip_leading_number_codes = _H._strip_leading_number_codes

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




# ---------------------------------------------------------------------------
# Optional / shared scenario drivers (wave-1 attack + reuse)
# ---------------------------------------------------------------------------

def _phase(msg: str) -> None:
    print(f"\n=== PHASE: {msg} ===", flush=True)


def _snapshot_session(win: Any) -> dict:
    st = win._state  # noqa: SLF001
    return {
        "calibrated": bool(getattr(st, "integrator_dir", None)),
        "buffer_ready": st.buffer_dat_path is not None,
        "monodisperse_armed": bool(st.monodisperse_armed),
        "polydisperse_armed": bool(st.polydisperse_armed),
        "auto_processing": bool(st.is_auto_processing()),
        "intake_mode": str(getattr(st.intake_mode, "value", st.intake_mode)),
    }


def _dismiss_modal_dialogs(app: Any) -> List[str]:
    """Accept visible QMessageBox dialogs so scenario tests are not blocked."""
    from PyQt5.QtWidgets import QApplication, QMessageBox

    texts: List[str] = []
    for w in list(QApplication.topLevelWidgets()):
        try:
            if isinstance(w, QMessageBox) and w.isVisible():
                texts.append((w.text() or w.informativeText() or "").strip())
                w.accept()
                _process_events(app)
        except Exception:
            continue
    return texts


def _app_log_text(win: Any) -> str:
    try:
        panel = win._right.log_panel  # noqa: SLF001
        return panel._app.toPlainText()  # noqa: SLF001
    except Exception:
        return ""


def _visible_toast_texts(win: Any) -> List[str]:
    """Collect text from guisaxs Toast widgets parented to the liveview window."""
    from PyQt5.QtWidgets import QApplication
    from guisaxs_skills.ui.toast import Toast

    out: List[str] = []
    try:
        for w in list(win.findChildren(Toast)) + [
            w for w in QApplication.topLevelWidgets() if isinstance(w, Toast)
        ]:
            try:
                if not w.isVisible():
                    continue
                lab = getattr(w, "_label", None)
                text = (lab.text() if lab is not None else "") or ""
                if text.strip():
                    out.append(text.strip())
            except Exception:
                continue
    except Exception:
        pass
    # unique preserve order
    seen = set()
    uniq = []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def _wait_user_warning(
    app: Any,
    win: Any,
    *,
    timeout_sec: float,
    keywords: Optional[List[str]] = None,
) -> dict:
    """
    Wait for user-visible warning about invalid/failed file.

    Returns dict with keys: toast_texts, dialog_texts, log_hit, ok.
    Preferred: Toast containing invalid/error; also accepts QMessageBox + App log Error:.
    """
    keys = [k.lower() for k in (keywords or ["invalid", "error", "fail", "cannot", "unable", "read"])]
    deadline = time.monotonic() + float(timeout_sec)
    dialog_texts: List[str] = []
    toast_texts: List[str] = []
    log_hit = False
    while time.monotonic() < deadline:
        _process_events(app)
        toast_texts = _visible_toast_texts(win)
        dialog_texts.extend(_dismiss_modal_dialogs(app))
        log = _app_log_text(win).lower()
        log_hit = any(k in log for k in keys) and ("error:" in log or "invalid" in log)
        toast_hit = any(any(k in t.lower() for k in keys) for t in toast_texts)
        dialog_hit = any(any(k in t.lower() for k in keys) for t in dialog_texts)
        if toast_hit or dialog_hit or log_hit:
            return {
                "toast_texts": list(toast_texts),
                "dialog_texts": list(dialog_texts),
                "log_hit": log_hit,
                "log_tail": _app_log_text(win)[-800:],
                "ok": True,
                "has_toast": toast_hit,
            }
        time.sleep(0.05)
    # Final sweep
    dialog_texts.extend(_dismiss_modal_dialogs(app))
    toast_texts = _visible_toast_texts(win)
    log = _app_log_text(win).lower()
    log_hit = any(k in log for k in keys)
    return {
        "toast_texts": list(toast_texts),
        "dialog_texts": list(dialog_texts),
        "log_hit": log_hit,
        "log_tail": _app_log_text(win)[-800:],
        "ok": bool(toast_texts or dialog_texts or log_hit),
        "has_toast": any(any(k in t.lower() for k in keys) for t in toast_texts),
    }


def _atomic_write_into_watchdir(watchdir: Path, name: str, data: bytes) -> Path:
    """Write bytes via .part then rename (same settle path as TIFF copy)."""
    watchdir.mkdir(parents=True, exist_ok=True)
    dest = watchdir / name
    fd, tmp = tempfile.mkstemp(prefix=dest.stem + "_", suffix=".part", dir=str(watchdir))
    os.close(fd)
    tmp_p = Path(tmp)
    try:
        tmp_p.write_bytes(data)
        os.replace(str(tmp_p), str(dest))
        return dest
    finally:
        if tmp_p.exists():
            try:
                tmp_p.unlink()
            except Exception:
                pass


def _set_intake_mode(app: Any, right: Any, mode: str) -> None:
    """Click Intake 2D / 1D / Sub. mode in {'2d','1d','sub'}."""
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest
    from guisaxs_skills.liveview.session.state import LiveviewIntakeMode

    m = mode.strip().lower()
    btn = {
        "2d": right._btn_intake_2d,  # noqa: SLF001
        "1d": right._btn_intake_1d,  # noqa: SLF001
        "sub": right._btn_intake_sub,  # noqa: SLF001
    }.get(m)
    assert btn is not None, f"unknown intake mode {mode!r}"
    QTest.mouseClick(btn, Qt.LeftButton)
    _process_events(app)
    expected = {
        "2d": LiveviewIntakeMode.FRAME_2D,
        "1d": LiveviewIntakeMode.CURVE_1D,
        "sub": LiveviewIntakeMode.CURVE_SUB,
    }[m]
    # Session may update async via signal
    _wait_until(
        app,
        lambda: right._state.intake_mode == expected,  # noqa: SLF001
        5.0,
        step_sec=0.05,
    )


def _stop_auto_via_mono(app: Any, right: Any, win: Any) -> None:
    _arm_monodisperse(right)
    wiz = right.monodisperse_wizard
    if win._state.is_auto_processing():  # noqa: SLF001
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest

        QTest.mouseClick(wiz._auto_btn, Qt.LeftButton)  # noqa: SLF001
        _process_events(app)
    assert not win._state.is_auto_processing(), "Stop auto-processing did not take effect"  # noqa: SLF001


def _resume_auto_via_mono(app: Any, right: Any, win: Any) -> None:
    _arm_monodisperse(right)
    wiz = right.monodisperse_wizard
    if not win._state.is_auto_processing():  # noqa: SLF001
        from PyQt5.QtCore import Qt
        from PyQt5.QtTest import QTest

        QTest.mouseClick(wiz._auto_btn, Qt.LeftButton)  # noqa: SLF001
        _process_events(app)
    assert win._state.is_auto_processing(), "Resume auto-processing did not take effect"  # noqa: SLF001


def _close_monodisperse(app: Any, right: Any, win: Any, timeout: float = 5.0) -> None:
    """Close the mono *dialog* (window-open arming); do not close the inner widget alone."""
    dlg = getattr(right, "_mono_dialog", None)
    if dlg is None:
        return
    try:
        dlg.close()
    except Exception:
        pass
    _wait_until(app, lambda: not win._state.monodisperse_armed, timeout)  # noqa: SLF001
    _process_events(app)


def _calibrate_via_ui(app: Any, win: Any, left: Any, *, timeout: float) -> None:
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest

    watchdir = Path(win._state.watchdir)  # noqa: SLF001
    QTest.mouseClick(left._cal_open, Qt.LeftButton)  # noqa: SLF001
    assert left._cal_wizard is not None  # noqa: SLF001
    wiz = left._cal_wizard  # noqa: SLF001
    form = wiz._form  # noqa: SLF001
    calib = Path(VALIDATION_RAW) / "AgBh700_96.9_calib.tif"
    mask = Path(VALIDATION_DIR) / "mask_fti2d_1225.msk"
    assert calib.is_file() and mask.is_file()
    assert _set_pathfield_text_by_label(form, label="calibrant_image", text=str(calib))
    try:
        getattr(form, "_on_primary_path_expression_changed")()  # type: ignore[attr-defined]
    except Exception:
        pass
    _wait_until(app, lambda: bool(_get_pathfield_text_by_label(form, label="mask")), 2.0, step_sec=0.05)
    assert _set_pathfield_text_by_label(form, label="mask", text=str(mask))
    if _get_pathfield_text_by_label(form, label="config_path").strip():
        assert _set_pathfield_text_by_label(form, label="config_path", text="")
    QTest.mouseClick(wiz._controls.run_button, Qt.LeftButton)  # noqa: SLF001
    ok_cal = _wait_until(
        app,
        lambda: (watchdir / "calibration" / "integrator").is_dir()
        and (watchdir / "calibration" / "refined.yml").is_file(),
        timeout,
    )
    assert ok_cal, "Calibration did not produce integrator + refined.yml"
    assert _wait_until_runcontrols_idle(app, wiz._controls, timeout)
    _settle_after_idle(1.0)
    wiz.close()
    _wait_until(app, lambda: not wiz.isVisible(), 3.0)
    assert _wait_until_queue_idle(app, win, timeout)
    _settle_after_idle(1.0)


def _history_step(app: Any, mid: Any, delta: int) -> None:
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest

    btn = mid._btn_hist_prev if delta < 0 else mid._btn_hist_next  # noqa: SLF001
    QTest.mouseClick(btn, Qt.LeftButton)
    _process_events(app)
    _settle_after_idle(0.3)


# ---------------------------------------------------------------------------
# TREE / multi-file / wave-2 helpers
# ---------------------------------------------------------------------------

_IHS_SAMPLE_STEMS: List[str] = [
    "ihs27_95.9_sample",
    "ihs28_95.2_sample",
    "ihs29_94.6_sample",
    "ihs30_94.0_sample",
    "ihs31_93.1_sample",
    "ihs32_92.5_sample",
]


def _assert_watch_mode_tree(win: Any) -> None:
    from guisaxs_skills.liveview.session.state import LiveviewWatchMode

    mode = win._state.watch_mode  # noqa: SLF001
    assert mode == LiveviewWatchMode.TREE, f"expected TREE watch mode, got {mode!r}"


def _tree_sample_dir(watchdir: Path, stem: str) -> Path:
    return Path(watchdir) / stem


def _tree_int_dat(watchdir: Path, stem: str) -> Path:
    return _tree_sample_dir(watchdir, stem) / "averaged" / f"int_{stem}.dat"


def _tree_sub_dat(watchdir: Path, stem: str) -> Path:
    return _tree_sample_dir(watchdir, stem) / "subtracted" / f"sub_{stem}.dat"


def _tree_guinier_results(watchdir: Path, stem: str) -> Path:
    return (
        _tree_sample_dir(watchdir, stem)
        / "guinier_mono"
        / stem
        / f"{stem}_results.txt"
    )


def _atomic_copy_into_tree(src: Path, watchdir: Path, *, stem: Optional[str] = None) -> Path:
    """
    Place a TIFF under ``watchdir/<stem>/<name>.tif`` (TREE per-sample layout).

    Uses ``.part`` → rename so TREE scan sees a stable create/replace.
    """
    if not src.is_file():
        raise FileNotFoundError(str(src))
    tok = (stem or src.stem).strip()
    dest_dir = _tree_sample_dir(watchdir, tok)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    fd, tmp = tempfile.mkstemp(prefix=dest.stem + "_", suffix=".part", dir=str(dest_dir))
    os.close(fd)
    tmp_p = Path(tmp)
    try:
        _copyfile_share_safe(src, tmp_p)
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


def _burst_copy_samples_into_tree(
    watchdir: Path,
    stems: Optional[List[str]] = None,
) -> List[Path]:
    """Copy several validation sample TIFFs into TREE sample dirs in quick succession."""
    toks = list(stems or _IHS_SAMPLE_STEMS[:5])
    out: List[Path] = []
    for tok in toks:
        src = Path(VALIDATION_RAW) / f"{tok}.tif"
        assert src.is_file(), src
        out.append(_atomic_copy_into_tree(src, watchdir, stem=tok))
    return out


def _rewrite_file_in_place(dest: Path, src: Path) -> None:
    """Overwrite ``dest`` with ``src`` bytes and bump mtime (poll / settle rewrite)."""
    if not src.is_file():
        raise FileNotFoundError(str(src))
    shutil.copyfile(src, dest)
    try:
        os.utime(dest, None)
    except Exception:
        pass


def _wait_tree_subs(
    app: Any,
    win: Any,
    watchdir: Path,
    stems: List[str],
    *,
    timeout: float,
) -> bool:
    abort = lambda: _window_gone(win)

    def _ready() -> bool:
        return all(
            _tree_sub_dat(watchdir, s).is_file() and _tree_sub_dat(watchdir, s).stat().st_size > 0
            for s in stems
        )

    return bool(_wait_until(app, _ready, timeout, step_sec=0.2, abort_if=abort))


def _process_history_via_ui(app: Any, mid: Any) -> None:
    from PyQt5.QtCore import Qt
    from PyQt5.QtTest import QTest

    btn = mid._btn_process  # noqa: SLF001
    QTest.mouseClick(btn, Qt.LeftButton)
    _process_events(app)


def _ingest_dnd_paths(win: Any, paths: List[Path]) -> None:
    """Simulate multi-path middle drop via controller (copies into watchdir)."""
    strs = [str(p.resolve()) for p in paths if p.is_file()]
    win._controller.ingest_dropped_tiffs(strs)  # noqa: SLF001

