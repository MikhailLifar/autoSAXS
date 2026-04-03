"""
Headless workflow test for guisaxs: drive the real Tk/CustomTkinter GUI (no pixel checks).

After each scenario, integrated and subtracted 1D curves are compared to ``validation/``
reference data using the same metric and regression check as ``test_skills_real_data``
(``integration_comparison_metric`` vs ``metrics_integrated.csv`` / ``metrics_subtracted.csv``).

Working directory for each run: ``tempfile.mkdtemp(prefix="guisaxs_test_")`` — typically
``/tmp/guisaxs_test_<random>`` on Linux, or under ``$TMPDIR`` when set. Outputs are removed
in ``finally``; set ``GUISAXS_TEST_KEEP_WORKDIR=1`` before pytest to skip rmtree (debugging).

Requires a display (use xvfb on CI), e.g.:
  xvfb-run -a /path/to/python -m pytest repos/tests/test_guisaxs.py
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import time
from typing import Any, Optional, Tuple

import pytest

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
    return float(os.environ.get("GUISAXS_TEST_TIMEOUT", "600"))


def _wait_until(root: Any, predicate, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            root.update_idletasks()
            root.update()
        except Exception:
            break
        if predicate():
            return True
        time.sleep(0.05)
    return False


_SUB_DAT_PATTERN = re.compile(r"^sub_\d+\.dat$")


def _find_reference_subtracted_for_stem(ref_stem: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (basename, path) of reference_subtracted file whose Parent sample matches ref_stem (e.g. ihs27_95.9)."""
    from autosaxs.utils import read_reference_sub_dat
    from test_skills_real_data import REFERENCE_SUBTRACTED_DIR, _strip_leading_number_codes

    for name in sorted(os.listdir(REFERENCE_SUBTRACTED_DIR)):
        if not _SUB_DAT_PATTERN.match(name):
            continue
        path = os.path.join(REFERENCE_SUBTRACTED_DIR, name)
        try:
            _, _, sample_basename = read_reference_sub_dat(path)
        except ValueError:
            continue
        if _strip_leading_number_codes(sample_basename) == ref_stem:
            return name, path
    return None, None


def _assert_gui_curves_match_validation(int_sam_path: str, sub_out_path: str) -> None:
    """
    Compare GUI outputs to validation reference .chi and reference_subtracted, using the same
    metric and >1% regression rule as test_skills_real_data.
    """
    from autosaxs.utils import integration_comparison_metric, read_chi, read_reference_sub_dat, read_saxs
    from test_skills_real_data import (
        METRICS_INTEGRATED_CSV,
        METRICS_SUBTRACTED_CSV,
        REFERENCE_DIR,
        _compare_metrics,
        _int_dat_to_ref_basename,
        _read_metrics_csv,
        _strip_leading_number_codes,
    )

    # --- Integrated sample (int_ihs27_95.9_sample.dat vs reference/*.chi)
    int_base = os.path.splitext(os.path.basename(int_sam_path))[0]
    ref_chi_base = _int_dat_to_ref_basename(int_base)
    assert ref_chi_base, f"Could not map {int_base!r} to a reference .chi basename"
    ref_chi_path = os.path.join(REFERENCE_DIR, ref_chi_base + ".chi")
    assert os.path.isfile(ref_chi_path), f"Missing reference chi: {ref_chi_path}"

    q_pipe, I_pipe, _, _ = read_saxs(int_sam_path)
    q_ref, I_ref = read_chi(ref_chi_path)
    metric_int = integration_comparison_metric(q_pipe, I_pipe, q_ref, I_ref)
    assert metric_int == metric_int, "Integrated comparison metric is NaN"
    row_int = {
        "reference": ref_chi_base + ".chi",
        "generated": os.path.basename(int_sam_path),
        "metric": float(metric_int),
    }
    old_int = _read_metrics_csv(METRICS_INTEGRATED_CSV)
    key_int = (row_int["reference"], row_int["generated"])
    assert key_int in old_int, (
        f"Missing baseline in {METRICS_INTEGRATED_CSV} for {key_int}; "
        "run the validation pipeline to record metrics."
    )
    assert _compare_metrics(old_int, [row_int], label="guisaxs integrated (ihs27)"), (
        f"Integrated curve metric regression vs {METRICS_INTEGRATED_CSV}: "
        f"ref={row_int['reference']} gen={row_int['generated']} new={metric_int:.6f}"
    )

    # --- Subtracted (sub_ihs27_95.9_sample.dat vs reference_subtracted/sub_*.dat)
    ref_stem = _strip_leading_number_codes(ref_chi_base)
    ref_sub_name, ref_sub_path = _find_reference_subtracted_for_stem(ref_stem)
    assert ref_sub_path and os.path.isfile(ref_sub_path), (
        f"No reference_subtracted entry for sample stem {ref_stem!r}"
    )

    q_sub_ref, I_sub_ref, _ = read_reference_sub_dat(ref_sub_path)
    q_sub, I_sub, _, _ = read_saxs(sub_out_path)
    metric_sub = integration_comparison_metric(q_sub, I_sub, q_sub_ref, I_sub_ref)
    assert metric_sub == metric_sub, "Subtracted comparison metric is NaN"
    row_sub = {
        "reference": ref_sub_name,
        "generated": os.path.basename(sub_out_path),
        "metric": float(metric_sub),
    }
    old_sub = _read_metrics_csv(METRICS_SUBTRACTED_CSV)
    key_sub = (row_sub["reference"], row_sub["generated"])
    assert key_sub in old_sub, (
        f"Missing baseline in {METRICS_SUBTRACTED_CSV} for {key_sub}; "
        "run the validation pipeline to record metrics."
    )
    assert _compare_metrics(old_sub, [row_sub], label="guisaxs subtracted (ihs27)"), (
        f"Subtracted curve metric regression vs {METRICS_SUBTRACTED_CSV}: "
        f"ref={row_sub['reference']} gen={row_sub['generated']} new={metric_sub:.6f}"
    )


def _run_guisaxs_minimal_scenario(*, mask_before_calibrant: bool) -> None:
    """
    Calibrant + mask (order controlled), Apply Calibration, one buffer .tif, one sample .tif.
    Asserts calibration and expected 1D outputs under a temp working directory.
    """
    from guisaxs.utils.threading_env import restore_threading_env, setup_threading_env

    setup_threading_env()

    import tkinter as tk

    import customtkinter as ctk
    from tkinterdnd2 import TkinterDnD

    from guisaxs.core.style import COLOR_THEME
    from guisaxs.gui.main_window import SAXSProcessorGUI

    calib = os.path.join(VALIDATION_RAW, "AgBh700_96.9_calib.tif")
    mask = os.path.join(VALIDATION_DIR, "mask_fti2d_1225.msk")
    buffer_tif = os.path.join(VALIDATION_RAW, "ihs27_buffer.tif")
    sample_tif = os.path.join(VALIDATION_RAW, "ihs27_95.9_sample.tif")
    for p in (calib, mask, buffer_tif, sample_tif):
        assert os.path.isfile(p), f"Missing validation fixture: {p}"

    workdir: str | None = None
    root = None
    try:
        workdir = tempfile.mkdtemp(prefix="guisaxs_test_")
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme(COLOR_THEME)
        root = TkinterDnD.Tk()
        root.geometry("800x600")
        app = SAXSProcessorGUI(root, workdir)

        if mask_before_calibrant:
            assert app.on_file_drop(mask, "Mask File (Optional)") is True
            assert app.on_file_drop(calib, "Calibrant Image") is True
        else:
            assert app.on_file_drop(calib, "Calibrant Image") is True
            assert app.on_file_drop(mask, "Mask File (Optional)") is True

        app.apply_calibration()

        timeout = _gui_timeout_sec()
        finished = _wait_until(
            root,
            lambda: not app.calibration_service.calibration_running,
            timeout,
        )
        assert finished, "Calibration did not finish within timeout"
        assert app.calibration_manager.is_calibrated, (
            "Calibration failed or did not produce an integrator; "
            f"status={app.status_var.get()!r}"
        )

        int_buf = os.path.join(workdir, "int_ihs27_buffer.dat")
        int_sam = os.path.join(workdir, "int_ihs27_95.9_sample.dat")
        sub_out = os.path.join(workdir, "sub_ihs27_95.9_sample.dat")

        # Drop buffer first and wait until its 1D curve exists. Dropping sample in the same
        # breath starts a second worker while the buffer thread may still use the integrator;
        # concurrent use of the shared pyFAI integrator can hang (see guisaxs spec / SPEC report).
        assert app.on_file_drop(buffer_tif, "Buffer Image") is True
        buf_ok = _wait_until(root, lambda: os.path.isfile(int_buf), timeout)
        assert buf_ok, (
            "Buffer integration did not produce int_ihs27_buffer.dat in time; "
            f"workdir (partial)={sorted(os.listdir(workdir))[:40]}"
        )
        assert os.path.getsize(int_buf) > 0

        assert app.on_file_drop(sample_tif, "Sample Image(s)") is True
        outputs_ok = _wait_until(
            root,
            lambda: os.path.isfile(int_sam) and os.path.isfile(sub_out),
            timeout,
        )
        assert outputs_ok, (
            "Expected sample integration or subtraction output not found in time; "
            f"workdir (partial)={sorted(os.listdir(workdir))[:40]}"
        )
        for p in (int_sam, sub_out):
            assert os.path.getsize(p) > 0

        _assert_gui_curves_match_validation(int_sam, sub_out)
    finally:
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
        restore_threading_env()
        if workdir and not os.environ.get("GUISAXS_TEST_KEEP_WORKDIR"):
            shutil.rmtree(workdir, ignore_errors=True)


def test_guisaxs_minimal_calibrate_integrate_subtract():
    """Calibrant → mask → Apply Calibration → buffer → sample."""
    _run_guisaxs_minimal_scenario(mask_before_calibrant=False)


def test_guisaxs_mask_first_then_calibrant():
    """Mask → calibrant → Apply Calibration → buffer → sample."""
    _run_guisaxs_minimal_scenario(mask_before_calibrant=True)
