from __future__ import annotations

import csv
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np
import yaml

from .deps import (
    EventBus,
    EventType,
    _strip_sub_int_prefix,
    apply_batch,
    ensure_q_nm,
    load_saxs_1d_any,
    parse_gnom_out,
    run_with_cache,
    write_saxs_atsas_format,
)
from .common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    coerce_dat_path_expression,
    expand_files_from_unwrapped,
)
from .fit_guinier.guinier import run_guinier_analysis


def fit_distances(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    rg_nm: Optional[float] = None,
    first: Optional[int] = None,
    last: Optional[int] = None,
    smooth: Optional[float] = None,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    SAXS / small-angle x-ray scattering: run ATSAS DATGNOM to obtain a pair distance distribution function \(p(r)\) for a monodisperse system from a 1D SAXS curve (real-space distance distribution).

    ### Arguments

    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where the GNOM outputs are written (one subdirectory per input profile).
    - `rg_nm` (float | None, default `None`): Expected Rg in nm. If omitted, run in-process Guinier analysis (`fit_guinier`) for an Rg span, then 1D optimize Rg in `[0, 1.5 × rg_max]` (30 s max) scoring each DATGNOM trial as Total Estimate − neg_frac.
    - `first` (int | None, default `None`): DATGNOM `--first` (1-based point index). If omitted, taken from the low-q end of the Guinier interval from `fit_guinier`.
    - `last` (int | None, default `None`): DATGNOM `--last`. If omitted, `--last` is not passed to DATGNOM.
    - `smooth` (float | None, default `None`): DATGNOM `--smooth`. If omitted, defaults to `2.0`.
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    ### Returns

    `dict[str, str | list[str]]` with:

    - `output_subdir`: The per-sample output directory used for this profile.
    - `gnom_out_paths`: List of DATGNOM `.out` paths written for this profile (typically a single “best” `.out`).
    - `best_gnom_out_path`: Path to the selected “best” DATGNOM `.out`.
    - `best_summary_path`: Path to a YAML summary of candidate runs and the selected parameters.
    - `fit_params_path`: Path to a YAML file containing the fit parameters used for the final run.
    - `best_symlink_out_path`: Best-effort symlink path to the selected `.out` (may be missing on some filesystems).
    - `fits_csv_path`: Path to a CSV containing candidate scores/metadata.
    - `fit_vs_exp_png_path` / `fit_vs_exp_png_error`: Fit-vs-experiment plot output or error message.
    - `best_pr_png_path` / `best_pr_png_error`: \(p(r)\) plot output or error message.

    ### Python usage

    ```python
    from autosaxs.skill import fit_distances

    out = fit_distances(
        profile="subtracted/sub_sample_01.dat",
        output_dir="distances",
        use_cache=False,
    )

    print(out["best_gnom_out_path"])
    ```

    ### CLI usage

    ```bash
    autosaxs fit_distances subtracted/sub_sample_01.dat --output-dir distances
    ```
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    profile = coerce_dat_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("fit_distances input files must have .dat extension")
    input_batch = [{"profile": p} for p in expanded_profiles]
    return _fit_distances_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        rg_nm=None if rg_nm is None else float(rg_nm),
        first=first,
        last=last,
        smooth=smooth,
        event_bus=bus,
        use_cache=use_cache,
    )


def _pr_metrics(r: np.ndarray, p: np.ndarray) -> Dict[str, Any]:
    """
    Compute a few descriptive metrics for p(r) without defining any composite score.
    """
    r = np.asarray(r, dtype=float)
    p = np.asarray(p, dtype=float)
    out: Dict[str, Any] = {}
    if r.size == 0 or p.size == 0 or r.size != p.size:
        return out
    if not np.any(np.isfinite(p)):
        return out
    i = int(np.nanargmax(p))
    peak_r = float(r[i])
    peak_p = float(p[i])
    out.update({"peak_r": peak_r, "peak_p": peak_p})
    if not np.isfinite(peak_p) or peak_p <= 0:
        return out
    half = peak_p / 2.0
    left_idx = np.where(p[:i] <= half)[0]
    right_idx = np.where(p[i:] <= half)[0]
    if left_idx.size > 0 and right_idx.size > 0:
        out["fwhm"] = float(r[i + int(right_idx[0])] - r[int(left_idx[-1])])
    return out


def _summarize_out_quality(out_text: str) -> Dict[str, Any]:
    """
    Extract GNOM/DATGNOM-reported quality indicators from the .out.

    IMPORTANT: This skill does not compute or use any custom composite score.
    """
    parsed = parse_gnom_out(out_text)
    total = parsed.get("total_estimate")
    pr = parsed.get("distribution")
    diag: Dict[str, Any] = {"total_estimate": total}
    if pr is None:
        return {**diag, "parse_pr_ok": False}
    _r, p = pr
    diag["parse_pr_ok"] = True
    if p.size == 0 or not np.any(np.isfinite(p)):
        return {**diag, "parse_pr_ok": False}
    p = np.asarray(p, dtype=float)
    p_abs_max = float(np.nanmax(np.abs(p))) if np.any(np.isfinite(p)) else 0.0
    if p_abs_max <= 0:
        return {**diag, "p_abs_max": p_abs_max}
    neg_frac = float(np.mean(p < 0.0))
    tail_n = min(5, int(p.size))
    tail = p[-tail_n:]
    tail_ratio = float(np.nanmean(np.abs(tail)) / (p_abs_max + 1e-12))
    if p.size >= 3:
        d2 = np.diff(p, n=2)
        smooth = float(np.nanmean(np.abs(d2)) / (p_abs_max + 1e-12))
    else:
        smooth = 1.0
    diag.update({"neg_frac": neg_frac, "tail_ratio": tail_ratio, "smoothness": smooth, "p_abs_max": p_abs_max})
    return diag


def _run_datgnom_once(
    *,
    atsas_dat_path: str,
    output_dir: str,
    rg_nm: float,
    first: Optional[int] = None,
    last: Optional[int] = None,
    smooth: Optional[float] = None,
    out_path: str,
) -> tuple[bool, int, str, str]:
    """
    Returns (ok, returncode, stderr, out_text).
    """
    # Always pass absolute paths because we run with cwd=output_dir and the caller
    # may already include output_dir in the provided relative paths.
    atsas_dat_path_abs = str(Path(atsas_dat_path).expanduser().resolve())
    out_path_abs = str(Path(out_path).expanduser().resolve())

    cmd: List[str] = ["datgnom", f"--rg={float(rg_nm):.6g}"]
    if first is not None:
        cmd.append(f"--first={int(first)}")
    if last is not None:
        cmd.append(f"--last={int(last)}")
    if smooth is not None:
        cmd.append(f"--smooth={float(smooth):.6g}")
    cmd += ["-o", out_path_abs, atsas_dat_path_abs]
    proc = subprocess.run(cmd, cwd=output_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        return False, int(proc.returncode), (proc.stderr or "")[:2000], ""
    if not os.path.isfile(out_path_abs):
        return False, int(proc.returncode), "gnom reported success but output file was not created", ""
    try:
        out_text = Path(out_path_abs).read_text(errors="replace")
    except OSError as e:
        return False, int(proc.returncode), f"failed to read DATGNOM output: {e}", ""
    return True, int(proc.returncode), (proc.stderr or "")[:2000], out_text


def _candidate_score(cand: Dict[str, Any]) -> float:
    """score = Total Estimate − neg_frac (higher is better)."""
    te = cand.get("total_estimate")
    try:
        te_v = float(te) if te is not None else float("-inf")
    except (TypeError, ValueError):
        te_v = float("-inf")
    nf = cand.get("neg_frac")
    try:
        nf_v = float(nf) if nf is not None else 0.0
    except (TypeError, ValueError):
        nf_v = 0.0
    if not np.isfinite(te_v):
        return float("-inf")
    return float(te_v - nf_v)


def _is_suspicious_candidate(c: Dict[str, Any]) -> bool:
    return bool(c.get("suspicious"))


def _select_best(cs: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not cs:
        raise RuntimeError("fit_distances failed: DATGNOM produced no output .out files")
    non_susp = [c for c in cs if not _is_suspicious_candidate(c)]
    pool = non_susp if non_susp else cs

    def sort_key(c: Dict[str, Any]) -> Tuple[float, float, float]:
        neg = c.get("neg_frac")
        tail = c.get("tail_ratio")
        try:
            neg_v = float(neg) if neg is not None else 1.0
        except (TypeError, ValueError):
            neg_v = 1.0
        try:
            tail_v = float(tail) if tail is not None else 1.0
        except (TypeError, ValueError):
            tail_v = 1.0
        return (-_candidate_score(c), neg_v, tail_v)

    return sorted(pool, key=sort_key)[0]


def _candidate_from_out_text(
    out_text: str,
    *,
    rg_nm: float,
    first: Optional[int],
    last: Optional[int],
    smooth: Optional[float],
    out_path: str,
    rc: int,
    stderr: str,
    intermediate: bool,
) -> Dict[str, Any]:
    parsed = parse_gnom_out(out_text)
    total = parsed.get("total_estimate")
    suspicious = bool(parsed.get("suspicious"))
    rmax_nm = parsed.get("real_space_rmax")
    pr = parsed.get("distribution")

    diag: Dict[str, Any] = {"total_estimate": total}
    prm: Dict[str, Any] = {}
    if pr is None:
        diag["parse_pr_ok"] = False
    else:
        r, p = pr
        diag["parse_pr_ok"] = True
        p = np.asarray(p, dtype=float)
        if p.size == 0 or not np.any(np.isfinite(p)):
            diag["parse_pr_ok"] = False
        else:
            p_abs_max = float(np.nanmax(np.abs(p))) if np.any(np.isfinite(p)) else 0.0
            diag["p_abs_max"] = p_abs_max
            if np.isfinite(p_abs_max) and p_abs_max > 0:
                diag["neg_frac"] = float(np.mean(p < 0.0))
                tail_n = min(5, int(p.size))
                tail = p[-tail_n:]
                diag["tail_ratio"] = float(np.nanmean(np.abs(tail)) / (p_abs_max + 1e-12))
                if p.size >= 3:
                    d2 = np.diff(p, n=2)
                    diag["smoothness"] = float(np.nanmean(np.abs(d2)) / (p_abs_max + 1e-12))
                else:
                    diag["smoothness"] = 1.0
            prm = _pr_metrics(np.asarray(r, dtype=float), p)

    cand: Dict[str, Any] = {
        "rg_nm": float(rg_nm),
        "first": int(first) if first is not None else None,
        "last": int(last) if last is not None else None,
        "smooth": float(smooth) if smooth is not None else None,
        "rmax_nm": rmax_nm,
        "suspicious": suspicious,
        "out_path": out_path,
        "intermediate": bool(intermediate),
        "ok": True,
        "returncode": int(rc),
        "stderr": stderr,
        **diag,
        **prm,
    }
    cand["score"] = _candidate_score(cand)
    return cand


def _guinier_from_profile(
    q_nm: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray],
    atsas_dat_path: str,
) -> Dict[str, Any]:
    """In-process fit_guinier (run_guinier_analysis) for Rg span and Guinier interval."""
    results = run_guinier_analysis(q_nm, I, sigma, atsas_dat_path=atsas_dat_path)
    if results.get("chosen") is None:
        raise RuntimeError(
            "fit_distances: fit_guinier (Guinier analysis) did not return a chosen result; "
            "cannot derive Rg span or --first."
        )
    ch_int = results.get("chosen_interval")
    return {
        "rg": results.get("chosen_Rg"),
        "rg_min": results.get("rg_min"),
        "rg_max": results.get("rg_max"),
        "q_min": ch_int[0] if ch_int else None,
        "q_max": ch_int[1] if ch_int else None,
        "chosen_interval": ch_int,
        "quality_class": results.get("quality_class"),
    }


def _q_to_first_point_1based(q_nm: np.ndarray, q_target: float) -> int:
    q_nm = np.asarray(q_nm, dtype=float)
    if not np.isfinite(q_target):
        raise ValueError("fit_distances: Guinier q_min is not finite")
    idx = int(np.argmin(np.abs(q_nm - float(q_target))))
    return idx + 1


def _optimize_rg_nm(
    *,
    atsas_dat_path: str,
    output_dir: str,
    rg_max_nm: float,
    first: int,
    last: Optional[int],
    smooth: float,
    eval_tmp_path: str,
    timeout_s: float = 30.0,
    event_bus: Optional[EventBus] = None,
) -> Tuple[float, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
  1D bounded search for Rg in (rg_lo, 1.5 * rg_max_nm], maximizing TE − neg_frac per DATGNOM trial.
    """
    from scipy.optimize import minimize_scalar

    rg_max_nm = float(rg_max_nm)
    if rg_max_nm <= 0 or not np.isfinite(rg_max_nm):
        raise ValueError(f"fit_distances: invalid rg_max from fit_guinier: {rg_max_nm}")

    rg_lo = 1e-6
    rg_hi = 1.5 * rg_max_nm
    t0 = time.monotonic()
    trials: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    best_score = float("-inf")
    best_rg: Optional[float] = None

    def objective(rg: float) -> float:
        nonlocal best_score, best_rg
        if time.monotonic() - t0 > timeout_s:
            return 1e10
        rg_v = float(max(rg_lo, min(float(rg), rg_hi)))
        ok, rc, stderr, out_text = _run_datgnom_once(
            atsas_dat_path=atsas_dat_path,
            output_dir=output_dir,
            rg_nm=rg_v,
            first=int(first),
            last=last,
            smooth=float(smooth),
            out_path=eval_tmp_path,
        )
        if not ok:
            failures.append(
                {
                    "rg_nm": rg_v,
                    "first": int(first),
                    "last": last,
                    "smooth": float(smooth),
                    "ok": False,
                    "returncode": int(rc),
                    "stderr": stderr,
                }
            )
            if event_bus:
                event_bus.publish(
                    EventType.MESSAGE,
                    {"text": f"DATGNOM (fit_distances): Rg trial failed at rg={rg_v:.4g} nm (rc={rc})."},
                )
            return 1e10
        cand = _candidate_from_out_text(
            out_text,
            rg_nm=rg_v,
            first=first,
            last=last,
            smooth=smooth,
            out_path="",
            rc=rc,
            stderr=stderr,
            intermediate=True,
        )
        trials.append(cand)
        sc = float(cand["score"])
        if sc > best_score:
            best_score = sc
            best_rg = rg_v
        return -sc

    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"DATGNOM (fit_distances): optimizing Rg in [{rg_lo:.4g}, {rg_hi:.4g}] nm "
                    f"(30 s max)…"
                ),
            },
        )

    try:
        minimize_scalar(
            objective,
            bounds=(rg_lo, rg_hi),
            method="bounded",
            options={"maxiter": 40},
        )
    except Exception:
        pass

    if best_rg is None:
        raise RuntimeError(
            "fit_distances: Rg optimization produced no successful DATGNOM trial within 30 s."
        )
    return float(best_rg), trials, failures


@apply_batch(stem_from_keys="profile", per_sample_subdir="always")
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash=None,
    kwargs_for_hash_keys=["rg_nm", "first", "last", "smooth"],
    include_config_in_hash=False,
)
def _fit_distances_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    rg_nm: Optional[float] = None,
    first: Optional[int] = None,
    last: Optional[int] = None,
    smooth: Optional[float] = None,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
    profile = input_paths.get("profile")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("fit_distances requires input_paths['profile']")

    os.makedirs(output_dir, exist_ok=True)
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(profile))[0])
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "DATGNOM (fit_distances): preparing ATSAS .dat input…"})

    q_nm, I, sigma = load_saxs_1d_any(profile)
    q_nm, I, sigma = ensure_q_nm(q_nm, I, sigma)

    atsas_dat_path = os.path.join(output_dir, f"{base}_atsas.dat")
    write_saxs_atsas_format(atsas_dat_path, q_nm, I, sigma)

    user_rg_nm = rg_nm
    user_first = first
    user_last = last
    user_smooth = smooth

    n_pts = int(len(q_nm))
    need_guinier = (user_rg_nm is None) or (user_first is None)
    guinier_info: Optional[Dict[str, Any]] = None
    if need_guinier:
        if event_bus:
            event_bus.publish(EventType.MESSAGE, {"text": "fit_distances: running fit_guinier (in-process)…"})
        guinier_info = _guinier_from_profile(q_nm, I, sigma, atsas_dat_path)
        if event_bus:
            event_bus.publish(EventType.MESSAGE, {"text": "fit_distances: fit_guinier completed."})

    if user_first is not None:
        first_pt = int(user_first)
    else:
        if guinier_info is None or guinier_info.get("q_min") is None:
            raise RuntimeError("fit_distances: cannot derive --first without fit_guinier q_min.")
        first_pt = _q_to_first_point_1based(q_nm, float(guinier_info["q_min"]))

    last_pt: Optional[int] = int(user_last) if user_last is not None else None
    smooth_val = float(user_smooth) if user_smooth is not None else 2.0

    if first_pt < 1 or first_pt >= n_pts:
        raise ValueError(
            f"fit_distances: require 1 <= first < n_points ({n_pts}); got first={first_pt}",
        )
    if last_pt is not None:
        if last_pt < 1 or last_pt > n_pts or first_pt >= last_pt:
            raise ValueError(
                f"fit_distances: require 1 <= first < last <= n_points ({n_pts}); "
                f"got first={first_pt}, last={last_pt}",
            )

    gnom_out_paths: List[str] = []
    candidates: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    rg_trials: List[Dict[str, Any]] = []

    eval_tmp_path: Optional[str] = None
    if user_rg_nm is None:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".out",
                prefix="datgnom_eval_",
                dir=output_dir,
                delete=False,
            ) as tf:
                eval_tmp_path = tf.name
        except Exception as e:
            raise RuntimeError(f"fit_distances: failed to create temporary DATGNOM output file: {e}")
        assert guinier_info is not None
        rg_nm, rg_trials, rg_failures = _optimize_rg_nm(
            atsas_dat_path=atsas_dat_path,
            output_dir=output_dir,
            rg_max_nm=float(guinier_info["rg_max"]),
            first=first_pt,
            last=last_pt,
            smooth=smooth_val,
            eval_tmp_path=eval_tmp_path,
            timeout_s=30.0,
            event_bus=event_bus,
        )
        failures.extend(rg_failures)
        candidates.extend(rg_trials)
    else:
        rg_nm = float(user_rg_nm)

    rg_nm = float(rg_nm)
    last_msg = f" --last={last_pt}" if last_pt is not None else " (no --last)"
    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"DATGNOM (fit_distances): final run --first={first_pt}{last_msg} "
                    f"--smooth={smooth_val:.6g} Rg={rg_nm:.4f} nm…"
                ),
            },
        )

    out_path_final = os.path.join(output_dir, f"datgnom_rg_{float(rg_nm):.4f}.out")
    ok, rc, stderr, out_text = _run_datgnom_once(
        atsas_dat_path=atsas_dat_path,
        output_dir=output_dir,
        rg_nm=rg_nm,
        first=first_pt,
        last=last_pt,
        smooth=smooth_val,
        out_path=out_path_final,
    )
    if not ok:
        raise RuntimeError(f"fit_distances failed: datgnom exited with code {rc}\n{stderr}")
    gnom_out_paths.append(out_path_final)
    best_gnom_out_path = out_path_final
    best = _candidate_from_out_text(
        out_text,
        rg_nm=rg_nm,
        first=first_pt,
        last=last_pt,
        smooth=smooth_val,
        out_path=out_path_final,
        rc=rc,
        stderr=stderr,
        intermediate=False,
    )
    candidates.append(best)

    if eval_tmp_path:
        try:
            os.remove(eval_tmp_path)
        except OSError:
            pass

    # Export summary artifacts for downstream use/inspection:
    # - stable symlink to best DATGNOM .out
    # - CSV table of rmax vs metric(s)
    # - p(r) plots for each successful fit
    best_link_path = os.path.join(output_dir, f"{base}_gnom.out")
    try:
        if os.path.lexists(best_link_path):
            os.remove(best_link_path)
        rel_target = os.path.relpath(best_gnom_out_path, start=output_dir)
        os.symlink(rel_target, best_link_path)
    except OSError:
        # Symlinks can be unsupported on some filesystems; ignore if creation fails.
        pass

    fits_csv_path = os.path.join(output_dir, "fit_distances_fits.csv")
    with open(fits_csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "rg_nm",
                "first",
                "last",
                "smooth",
                "rmax_nm",
                "peak_r",
                "peak_p",
                "fwhm",
                "suspicious",
                "intermediate",
                "total_estimate",
                "neg_frac",
                "score",
                "tail_ratio",
                "smoothness",
                "ok",
                "out_path",
            ]
        )
        for c in candidates:
            w.writerow(
                [
                    c.get("rg_nm"),
                    c.get("first"),
                    c.get("last"),
                    c.get("smooth"),
                    c.get("rmax_nm"),
                    c.get("peak_r"),
                    c.get("peak_p"),
                    c.get("fwhm"),
                    bool(c.get("suspicious")),
                    bool(c.get("intermediate")),
                    c.get("total_estimate"),
                    c.get("neg_frac"),
                    c.get("score"),
                    c.get("tail_ratio"),
                    c.get("smoothness"),
                    bool(c.get("ok")),
                    c.get("out_path"),
                ]
            )

    fit_vs_exp_png_path: Optional[str] = None
    fit_vs_exp_png_error: Optional[str] = None
    try:
        parsed = parse_gnom_out(out_text)
        iq_table = parsed.get("iq_table")
        if iq_table is None:
            fit_vs_exp_png_error = "could not parse I(q) table from .out"
        else:
            q, I_exp, sigma_arr, I_fit = iq_table
            fit_vs_exp_png_path = os.path.join(output_dir, f"{base}_fits.png")
            fig, ax = plt.subplots(figsize=(7, 4))
            ax.plot(q, I_exp, lw=3, label="exp")
            ax.plot(q, I_fit, lw=2, label="fit")
            ax.set_xlabel("q (nm$^{-1}$)")
            ax.set_ylabel("I(q)")
            ax.set_yscale("log")
            te = best.get("total_estimate")
            rg_nm_v = best.get("rg_nm")
            if te is not None and rg_nm_v is not None:
                ax.set_title(f"DATGNOM fit: Rg={float(rg_nm_v):.4f} nm, Total Estimate={float(te):.3f}")
            elif rg_nm_v is not None:
                ax.set_title(f"DATGNOM fit: Rg={float(rg_nm_v):.4f} nm")
            ax.grid(True, which="both", alpha=0.25)
            ax.legend()
            fig.tight_layout()
            fig.savefig(fit_vs_exp_png_path, dpi=150, bbox_inches="tight")
            plt.close(fig)
            if event_bus:
                event_bus.publish(
                    EventType.MESSAGE,
                    {"text": f"DATGNOM (fit_distances): wrote fit-vs-exp PNG: {os.path.basename(fit_vs_exp_png_path)}"},
                )
    except Exception as e:
        fit_vs_exp_png_error = f"failed to write fit-vs-exp PNG: {e}"
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"DATGNOM (fit_distances): fit-vs-exp PNG not created ({fit_vs_exp_png_error})."},
            )

    # Only the final best .out exists; intermediate evaluations are not persisted.
    best_pr_png_path: Optional[str] = None
    best_pr_png_error: Optional[str] = None
    for c in [best]:
        if not c.get("ok"):
            continue
        out_path = str(c.get("out_path") or "")
        if not out_path or not os.path.isfile(out_path):
            best_pr_png_error = f"best .out path missing: {out_path!r}"
            if event_bus:
                event_bus.publish(
                    EventType.MESSAGE,
                    {"text": f"DATGNOM (fit_distances): p(r) PNG not created ({best_pr_png_error})."},
                )
            continue
        try:
            out_text = Path(out_path).read_text(errors="replace")
        except OSError:
            best_pr_png_error = f"failed to read best .out: {out_path!r}"
            if event_bus:
                event_bus.publish(
                    EventType.MESSAGE,
                    {"text": f"DATGNOM (fit_distances): p(r) PNG not created ({best_pr_png_error})."},
                )
            continue
        pr = parse_gnom_out(out_text).get("distribution")
        if pr is None:
            best_pr_png_error = f"could not parse p(r) table from: {os.path.basename(out_path)}"
            if event_bus:
                event_bus.publish(
                    EventType.MESSAGE,
                    {"text": f"DATGNOM (fit_distances): p(r) PNG not created ({best_pr_png_error})."},
                )
            continue
        r, p = pr
        png_path = os.path.splitext(out_path)[0] + ".png"
        try:
            fig, ax = plt.subplots(figsize=(7, 4))
            ax.plot(r, p, lw=2)
            ax.set_xlabel("r (nm)")
            ax.set_ylabel("p(r)")
            rg_nm_v = c.get("rg_nm")
            te = c.get("total_estimate")
            if rg_nm_v is not None and te is not None:
                ax.set_title(f"DATGNOM p(r): Rg={float(rg_nm_v):.4f} nm, Total Estimate={float(te):.3f}")
            elif rg_nm_v is not None:
                ax.set_title(f"DATGNOM p(r): Rg={float(rg_nm_v):.4f} nm")
            else:
                ax.set_title("DATGNOM p(r)")
            ax.grid(True, alpha=0.25)
            fig.tight_layout()
            fig.savefig(png_path, dpi=150, bbox_inches="tight")
            plt.close(fig)
            best_pr_png_path = png_path
            best_pr_png_error = None
            if event_bus:
                event_bus.publish(
                    EventType.MESSAGE,
                    {"text": f"DATGNOM (fit_distances): wrote p(r) PNG: {os.path.basename(png_path)}"},
                )
        except Exception:
            best_pr_png_error = f"matplotlib failed to save PNG: {os.path.basename(png_path)}"
            if event_bus:
                event_bus.publish(
                    EventType.MESSAGE,
                    {"text": f"DATGNOM (fit_distances): p(r) PNG not created ({best_pr_png_error})."},
                )
            try:
                plt.close(fig)  # type: ignore[name-defined]
            except Exception:
                pass

    fit_params_path = os.path.join(output_dir, f"{base}_fit_distances_fit_params.yml")
    fit_params_doc = {
        "rg_nm": float(best["rg_nm"]),
        "first": best.get("first"),
        "last": best.get("last"),
    }
    with open(fit_params_path, "w") as fp:
        yaml.dump(fit_params_doc, fp, default_flow_style=False)

    if user_rg_nm is not None:
        rg_param_src = "user"
    else:
        rg_param_src = "rg_optimization"

    if user_first is not None:
        first_param_src = "user"
    else:
        first_param_src = "fit_guinier"

    if user_last is not None:
        last_param_src = "user"
    else:
        last_param_src = "omitted"

    if user_smooth is not None:
        smooth_param_src = "user"
    else:
        smooth_param_src = "default"

    guinier_summary: Optional[Dict[str, Any]] = None
    if need_guinier and guinier_info is not None:
        guinier_summary = {
            "rg": guinier_info.get("rg"),
            "rg_min": guinier_info.get("rg_min"),
            "rg_max": guinier_info.get("rg_max"),
            "q_min": guinier_info.get("q_min"),
            "q_max": guinier_info.get("q_max"),
            "chosen_interval": guinier_info.get("chosen_interval"),
            "quality_class": guinier_info.get("quality_class"),
        }

    best_summary_path = os.path.join(output_dir, f"{base}_fit_distances_best.yml")
    summary = {
        "profile": profile,
        "atsas_dat_path": atsas_dat_path,
        "unit_note": "Input profile assumed q in nm^-1; DATGNOM uses the same units, therefore Rg and r are in nm.",
        "fit_params_path": fit_params_path,
        "fit_param_sources": {
            "rg_nm": rg_param_src,
            "first": first_param_src,
            "last": last_param_src,
            "smooth": smooth_param_src,
        },
        "fit_guinier": guinier_summary,
        "rg_optimization_trials": rg_trials if user_rg_nm is None else None,
        "selected": {
            "rg_nm": float(best["rg_nm"]),
            "first": best.get("first"),
            "last": best.get("last"),
            "smooth": best.get("smooth"),
            "rmax_nm": best.get("rmax_nm"),
            "out_path": best_gnom_out_path,
            "suspicious": bool(best.get("suspicious")),
            "total_estimate": best.get("total_estimate"),
            "neg_frac": best.get("neg_frac"),
            "score": best.get("score"),
        },
        "candidates": candidates,
        "failures": failures,
        "best_symlink_out_path": best_link_path,
        "fits_csv_path": fits_csv_path,
        "fit_vs_exp_png_path": fit_vs_exp_png_path,
        "fit_vs_exp_png_error": fit_vs_exp_png_error,
        "best_pr_png_path": best_pr_png_path,
        "best_pr_png_error": best_pr_png_error,
    }
    with open(best_summary_path, "w") as f:
        yaml.dump(summary, f, default_flow_style=False)

    from autosaxs.core.report_fragments import write_skill_report_fragments

    md_parts = ["### DATGNOM / p(r) (fit_distances)\n"]
    if fit_vs_exp_png_path and os.path.isfile(fit_vs_exp_png_path):
        md_parts.append(f"![Fit vs experiment]({os.path.basename(fit_vs_exp_png_path)})\n")
    if best_pr_png_path and os.path.isfile(best_pr_png_path):
        md_parts.append(f"![p(r)]({os.path.basename(best_pr_png_path)})\n")
    summary_refs = [
        {"role": "fit_distances_summary", "path": os.path.basename(best_summary_path), "format": "text"},
        {
            "role": "fit_distances_scores",
            "path": os.path.basename(fits_csv_path),
            "format": "csv",
            "row": 0,
            "columns": ["rmax_nm", "total_estimate", "ok"],
        },
    ]
    write_skill_report_fragments(
        output_dir,
        base,
        "fit_distances",
        "".join(md_parts),
        summary_references=summary_refs,
    )

    return {
        "output_subdir": output_dir,
        "gnom_out_paths": gnom_out_paths,
        "best_gnom_out_path": best_gnom_out_path,
        "best_summary_path": best_summary_path,
        "fit_params_path": fit_params_path,
        "best_symlink_out_path": best_link_path,
        "fits_csv_path": fits_csv_path,
        "fit_vs_exp_png_path": fit_vs_exp_png_path or "",
        "fit_vs_exp_png_error": fit_vs_exp_png_error or "",
        "best_pr_png_path": best_pr_png_path or "",
        "best_pr_png_error": best_pr_png_error or "",
    }

