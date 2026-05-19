from __future__ import annotations

import csv
import os
import re
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

from .common import DatPathExpressionArg, coerce_dat_path_expression, expand_files_from_unwrapped
from .deps import (
    EventBus,
    EventType,
    _strip_sub_int_prefix,
    apply_batch,
    ensure_q_nm,
    load_saxs_1d_any,
    run_with_cache,
    write_saxs_atsas_format,
)
from .fit_distances import _candidate_score
from .fit_guinier.guinier import run_guinier_analysis


def fit_sizes(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    shape: str = "spheres",
    rg_nm: Optional[float] = None,
    rmin_nm: Optional[float] = None,
    rmax_nm: Optional[float] = None,
    rad56_nm: Optional[float] = None,
    first: Optional[int] = None,
    last: Optional[int] = None,
    alpha: Optional[float] = None,
    nr: Optional[int] = None,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    SAXS / small-angle x-ray scattering: run ATSAS GNOM (system=1/5) to obtain a size distribution function \(D(R)\) for a polydisperse system from a 1D SAXS curve (polydispersity; spheres/rods).

    ### Arguments

    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Output directory (one subdirectory per input profile).
    - `shape` (str, default `spheres`): Polydisperse system model. Options:
        - `spheres`: GNOM `--system=1` (volume distribution for solid spheres).
        - `rods`: GNOM `--system=5` (length distribution for long cylinders). Requires `rad56_nm` (cylinder radius).
        - `ellipsoids`: accepted for API compatibility but **not supported by GNOM command-line** (GNOM system 2 is
          interactive-only). The skill will raise a clear error if selected.
    - `rg_nm` (float | None): Optional metadata only (not passed to GNOM); recorded in outputs if set.
    - `rmin_nm` (float | None): GNOM `--rmin` (nm). If omitted, not passed to GNOM.
    - `rmax_nm` (float | None): GNOM `--rmax` (nm). If omitted, optimized in `[ε, 3 × rg_max]` from in-process `fit_guinier` (30 s max), scoring each trial as Total Estimate − neg_frac.
    - `rad56_nm` (float | None): GNOM `--rad56` for `shape=rods` (nm cylinder radius). Ignored for spheres.
    - `first` (int | None): GNOM `--first` (1-based). If omitted, taken from the low-q end of the Guinier interval from `fit_guinier`.
    - `last` (int | None): GNOM `--last`. If omitted, not passed to GNOM.
    - `alpha` (float | None): GNOM `--alpha`. If omitted, not passed to GNOM.
    - `nr` (int | None): GNOM `--nr` (number of real-space points). If omitted, GNOM chooses automatically.
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    ### Returns

    `dict[str, str | list[str]]` with:

    - `output_subdir`: The per-sample output directory used for this profile.
    - `gnom_out_paths`: List of GNOM `.out` paths written for this profile (typically a single “best” `.out`).
    - `best_gnom_out_path`: Path to the selected “best” GNOM `.out`.
    - `best_summary_path`: Path to a YAML summary of candidate runs and the selected parameters.
    - `fit_params_path`: Path to a YAML file containing the fit parameters used for the final run.
    - `best_symlink_out_path`: Best-effort symlink path to the selected `.out` (may be missing on some filesystems).
    - `fits_csv_path`: Path to a CSV containing candidate scores/metadata.
    - `fit_vs_exp_png_path` / `fit_vs_exp_png_error`: Fit-vs-experiment plot output or error message.
    - `best_dr_png_path` / `best_dr_png_error`: \(D(R)\) plot output or error message.
    - `dr_csv_path`: Path to a CSV export of \(D(R)\) (if successfully parsed).

    ### Python usage

    ```python
    from autosaxs.skill import fit_sizes

    out = fit_sizes(
        profile="subtracted/sub_sample_01.dat",
        output_dir="sizes",
        shape="spheres",
        use_cache=False,
    )

    print(out["best_gnom_out_path"])
    ```

    ### CLI usage

    ```bash
    autosaxs fit-sizes subtracted/sub_sample_01.dat --output-dir sizes --shape spheres
    ```
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    profile = coerce_dat_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("fit_sizes input files must have .dat extension")
    input_batch = [{"profile": p} for p in expanded_profiles]
    return _fit_sizes_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        shape=str(shape),
        rg_nm=None if rg_nm is None else float(rg_nm),
        rmin_nm=None if rmin_nm is None else float(rmin_nm),
        rmax_nm=None if rmax_nm is None else float(rmax_nm),
        rad56_nm=None if rad56_nm is None else float(rad56_nm),
        first=first,
        last=last,
        alpha=None if alpha is None else float(alpha),
        nr=nr,
        event_bus=bus,
        use_cache=use_cache,
    )


def _parse_gnom_total_estimate(out_text: str) -> Optional[float]:
    patterns = [
        r"Total\s+Estimate\s*[:=]\s*([0-9]*\.?[0-9]+)",
        r"TOTAL\s+ESTIMATE\s*[:=]\s*([0-9]*\.?[0-9]+)",
        r"\bTOTAL\b\s*[:=]\s*([0-9]*\.?[0-9]+)",
    ]
    for pat in patterns:
        m = re.search(pat, out_text or "", flags=re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _parse_out_iq_table(
    out_text: str,
) -> Optional[tuple[np.ndarray, np.ndarray, Optional[np.ndarray], np.ndarray]]:
    """
    Parse the experimental/scattering section from an ATSAS .out file.
    Returns (q, I_exp, sigma(optional), I_fit_or_reg_or_lastcol).
    """
    lines = (out_text or "").splitlines()
    header_idx: Optional[int] = None
    for i, ln in enumerate(lines):
        s = ln.strip().upper()
        if ("EXP" in s or "EXPER" in s) and ("ERROR" in s or "ERR" in s) and ("S" in s or "Q" in s):
            header_idx = i
            break
    start = header_idx + 1 if header_idx is not None else 0

    rows: List[List[float]] = []
    for ln in lines[start:]:
        st = ln.strip()
        if not st:
            if rows:
                break
            continue
        parts = re.split(r"[,\s]+", st)
        if len(parts) < 3:
            if rows:
                break
            continue
        try:
            vals = [float(x) for x in parts]
        except ValueError:
            if rows:
                break
            continue
        rows.append(vals)

    if not rows:
        return None
    arr = np.array([r + [np.nan] * (max(len(x) for x in rows) - len(r)) for r in rows], dtype=float)
    ncol = int(arr.shape[1])
    if ncol < 3:
        return None
    q = arr[:, 0]
    I_exp = arr[:, 1]
    sigma: Optional[np.ndarray] = None
    if ncol >= 4 and np.any(np.isfinite(arr[:, 2])):
        sigma = arr[:, 2]
    # pick last finite numeric column as "fit" (often Ireg/Jreg depending on GNOM version)
    I_fit: Optional[np.ndarray] = None
    for j in range(ncol - 1, 1, -1):
        cand = arr[:, j]
        if np.any(np.isfinite(cand)):
            I_fit = cand
            break
    if I_fit is None:
        return None
    return (q.astype(float), I_exp.astype(float), sigma.astype(float) if sigma is not None else None, I_fit.astype(float))


def _parse_gnom_dr_table(out_text: str) -> Optional[tuple[np.ndarray, np.ndarray]]:
    """
    Parse D(R) table from GNOM output (.out).

    GNOM prints the recovered distribution in 3 columns; for polydisperse systems this corresponds
    to the size/volume/length distribution (often named D(R) in docs).
    """
    lines = (out_text or "").splitlines()
    blocks: List[List[tuple[float, float, float]]] = []
    cur: List[tuple[float, float, float]] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            if cur:
                blocks.append(cur)
                cur = []
            continue
        parts = re.split(r"[,\s]+", s)
        if len(parts) < 3:
            if cur:
                blocks.append(cur)
                cur = []
            continue
        try:
            a, b, c = float(parts[0]), float(parts[1]), float(parts[2])
        except ValueError:
            if cur:
                blocks.append(cur)
                cur = []
            continue
        # Heuristic: if a 4th column is numeric, treat as not-a-distribution block and cut.
        if len(parts) > 3:
            try:
                _ = float(parts[3])
                if cur:
                    blocks.append(cur)
                    cur = []
                continue
            except ValueError:
                pass
        cur.append((a, b, c))
    if cur:
        blocks.append(cur)
    if not blocks:
        return None
    for blk in reversed(blocks):
        if len(blk) >= 8:
            r = np.asarray([x[0] for x in blk], dtype=float)
            d = np.asarray([x[1] for x in blk], dtype=float)
            if np.all(np.diff(r) >= 0):
                return r, d
    return None


def _shape_to_system(shape: str) -> int:
    s = (shape or "").strip().lower()
    if s in ("sphere", "spheres", "solid_spheres", "solid-spheres"):
        return 1
    if s in ("rod", "rods", "cylinder", "cylinders", "long_cylinders", "long-cylinders"):
        return 5
    if s in ("ellipsoid", "ellipsoids"):
        # GNOM system=2 is interactive-only on the command line per ATSAS 4 manual.
        return 2
    raise ValueError(f"fit_sizes: unknown shape={shape!r}; expected 'spheres', 'rods', or 'ellipsoids'")


def _run_gnom_once(
    *,
    atsas_dat_path: str,
    output_dir: str,
    system: int,
    rmin_nm: Optional[float],
    rmax_nm: float,
    rad56_nm: Optional[float],
    first: Optional[int],
    last: Optional[int],
    alpha: Optional[float],
    nr: Optional[int],
    out_path: str,
) -> tuple[bool, int, str, str]:
    """
    Returns (ok, returncode, stderr, out_text).
    """
    if system == 2:
        return (
            False,
            2,
            "GNOM system=2 (user-supplied form factor) is not supported on the GNOM command line; use interactive GNOM/PRIMUS.",
            "",
        )
    # IMPORTANT: We run GNOM with cwd=output_dir. Therefore, pass local/basename paths to GNOM for
    # both the input .dat (which we write into output_dir) and the output .out, otherwise GNOM may
    # interpret "output_dir/..." relative to output_dir and attempt to write into a non-existent
    # nested directory.
    atsas_dat_arg = atsas_dat_path
    atsas_dat_local = os.path.basename(atsas_dat_path)
    if os.path.isfile(os.path.join(output_dir, atsas_dat_local)):
        atsas_dat_arg = atsas_dat_local
    out_arg = os.path.basename(out_path) if os.path.dirname(out_path) else out_path
    out_effective_path = os.path.join(output_dir, out_arg)

    cmd: List[str] = ["gnom", f"--system={int(system)}", f"--rmax={float(rmax_nm):.6g}"]
    if rmin_nm is not None:
        cmd.append(f"--rmin={float(rmin_nm):.6g}")
    if rad56_nm is not None:
        cmd.append(f"--rad56={float(rad56_nm):.6g}")
    if first is not None:
        cmd.append(f"--first={int(first)}")
    if last is not None:
        cmd.append(f"--last={int(last)}")
    if nr is not None:
        cmd.append(f"--nr={int(nr)}")
    if alpha is not None:
        cmd.append(f"--alpha={float(alpha):.6g}")
    cmd += ["-o", out_arg, atsas_dat_arg]
    proc = subprocess.run(cmd, cwd=output_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        return False, int(proc.returncode), (proc.stderr or proc.stdout or "")[:2000], ""
    if not os.path.isfile(out_effective_path):
        return False, int(proc.returncode), "gnom reported success but output file was not created", ""
    try:
        out_text = Path(out_effective_path).read_text(errors="replace")
    except OSError as e:
        return False, int(proc.returncode), f"failed to read GNOM output: {e}", ""
    return True, int(proc.returncode), (proc.stderr or "")[:2000], out_text


def _is_suspicious_candidate(c: Dict[str, Any]) -> bool:
    return bool(c.get("suspicious"))


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
            "fit_sizes: fit_guinier (Guinier analysis) did not return a chosen result; "
            "cannot derive rmax span or --first."
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
        raise ValueError("fit_sizes: Guinier q_min is not finite")
    idx = int(np.argmin(np.abs(q_nm - float(q_target))))
    return idx + 1


def _candidate_from_gnom_out(
    out_text: str,
    *,
    shape: str,
    system: int,
    rmax_nm: float,
    rmin_nm: Optional[float],
    rad56_nm: Optional[float],
    first: Optional[int],
    last: Optional[int],
    alpha: Optional[float],
    nr: Optional[int],
    out_path: str,
    rc: int,
    stderr: str,
    intermediate: bool,
) -> Dict[str, Any]:
    total = _parse_gnom_total_estimate(out_text)
    suspicious = bool(re.search(r"SUSPICIOUS", out_text or "", flags=re.IGNORECASE))
    dr = _parse_gnom_dr_table(out_text)
    diag: Dict[str, Any] = {
        "total_estimate": total,
        "parse_dr_ok": dr is not None,
    }
    if dr is not None:
        _r, d = dr
        d_arr = np.asarray(d, dtype=float)
        if d_arr.size > 0 and np.any(np.isfinite(d_arr)):
            diag["neg_frac"] = float(np.mean(d_arr < 0.0))
    cand: Dict[str, Any] = {
        "shape": shape,
        "system": int(system),
        "rmin_nm": rmin_nm,
        "rmax_nm": float(rmax_nm),
        "rad56_nm": rad56_nm,
        "first": int(first) if first is not None else None,
        "last": int(last) if last is not None else None,
        "alpha": alpha,
        "nr": nr,
        "suspicious": suspicious,
        "out_path": out_path,
        "intermediate": bool(intermediate),
        "ok": True,
        "returncode": int(rc),
        "stderr": stderr,
        **diag,
    }
    cand["score"] = _candidate_score(cand)
    return cand


def _trial_better(
    sc: float,
    susp: bool,
    best_score: float,
    best_rmax: Optional[float],
    best_suspicious: bool,
) -> bool:
    if best_rmax is None:
        return True
    if susp and not best_suspicious:
        return False
    if not susp and best_suspicious:
        return True
    return sc > best_score


def _optimize_rmax_nm(
    *,
    atsas_dat_path: str,
    output_dir: str,
    system: int,
    shape: str,
    rg_max_nm: float,
    rmin_nm: Optional[float],
    rad56_nm: Optional[float],
    first: Optional[int],
    last: Optional[int],
    alpha: Optional[float],
    nr: Optional[int],
    eval_tmp_path: str,
    timeout_s: float = 30.0,
    event_bus: Optional[EventBus] = None,
) -> Tuple[float, List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Bounded 1D search for rmax in (rmax_lo, 3 * rg_max_nm], maximizing TE − neg_frac."""
    from scipy.optimize import minimize_scalar

    rg_max_nm = float(rg_max_nm)
    if rg_max_nm <= 0 or not np.isfinite(rg_max_nm):
        raise ValueError(f"fit_sizes: invalid rg_max from fit_guinier: {rg_max_nm}")

    rmax_lo = 1e-6
    rmax_hi = 3.0 * rg_max_nm
    t0 = time.monotonic()
    trials: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    best_score = float("-inf")
    best_rmax: Optional[float] = None
    best_suspicious = False

    def objective(rmax: float) -> float:
        nonlocal best_score, best_rmax, best_suspicious
        if time.monotonic() - t0 > timeout_s:
            return 1e10
        rm = float(max(rmax_lo, min(float(rmax), rmax_hi)))
        ok, rc, stderr, out_text = _run_gnom_once(
            atsas_dat_path=atsas_dat_path,
            output_dir=output_dir,
            system=system,
            rmin_nm=rmin_nm,
            rmax_nm=rm,
            rad56_nm=rad56_nm,
            first=first,
            last=last,
            alpha=alpha,
            nr=nr,
            out_path=eval_tmp_path,
        )
        if not ok:
            failures.append(
                {
                    "rmax_nm": rm,
                    "ok": False,
                    "returncode": int(rc),
                    "stderr": stderr,
                }
            )
            if event_bus:
                event_bus.publish(
                    EventType.MESSAGE,
                    {"text": f"GNOM (fit_sizes): rmax trial failed at rmax={rm:.4g} nm (rc={rc})."},
                )
            return 1e10
        cand = _candidate_from_gnom_out(
            out_text,
            shape=shape,
            system=system,
            rmax_nm=rm,
            rmin_nm=rmin_nm,
            rad56_nm=rad56_nm,
            first=first,
            last=last,
            alpha=alpha,
            nr=nr,
            out_path="",
            rc=rc,
            stderr=stderr,
            intermediate=True,
        )
        trials.append(cand)
        sc = float(cand["score"])
        susp = _is_suspicious_candidate(cand)
        if _trial_better(sc, susp, best_score, best_rmax, best_suspicious):
            best_score = sc
            best_rmax = rm
            best_suspicious = susp
        return -sc

    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"GNOM (fit_sizes): optimizing rmax in [{rmax_lo:.4g}, {rmax_hi:.4g}] nm "
                    f"(30 s max)…"
                ),
            },
        )

    try:
        minimize_scalar(
            objective,
            bounds=(rmax_lo, rmax_hi),
            method="bounded",
            options={"maxiter": 40},
        )
    except Exception:
        pass

    if best_rmax is None:
        raise RuntimeError(
            "fit_sizes: rmax optimization produced no successful GNOM trial within 30 s."
        )
    return float(best_rmax), trials, failures


@apply_batch(stem_from_keys="profile", per_sample_subdir="always")
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash=None,
    kwargs_for_hash_keys=["shape", "rg_nm", "rmin_nm", "rmax_nm", "rad56_nm", "first", "last", "alpha", "nr"],
    include_config_in_hash=False,
)
def _fit_sizes_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    shape: str = "spheres",
    rg_nm: Optional[float] = None,
    rmin_nm: Optional[float] = None,
    rmax_nm: Optional[float] = None,
    rad56_nm: Optional[float] = None,
    first: Optional[int] = None,
    last: Optional[int] = None,
    alpha: Optional[float] = None,
    nr: Optional[int] = None,
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
        raise FileNotFoundError("fit_sizes requires input_paths['profile']")

    system = _shape_to_system(shape)
    if system == 2:
        raise NotImplementedError(
            "fit_sizes: shape='ellipsoids' maps to GNOM system=2 (user-supplied form factor), "
            "which ATSAS GNOM does not support in command-line mode. Use interactive GNOM/PRIMUS or choose "
            "shape='spheres' or 'rods'."
        )
    if system == 5 and rad56_nm is None:
        raise ValueError("fit_sizes: shape='rods' requires rad56_nm (cylinder radius in nm) for GNOM system=5")

    os.makedirs(output_dir, exist_ok=True)
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(profile))[0])
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "GNOM (fit_sizes): preparing ATSAS .dat input…"})

    q_nm, I, sigma = load_saxs_1d_any(profile)
    q_nm, I, sigma = ensure_q_nm(q_nm, I, sigma)
    atsas_dat_path = os.path.join(output_dir, f"{base}_atsas.dat")
    write_saxs_atsas_format(atsas_dat_path, q_nm, I, sigma)

    user_rg_nm = rg_nm
    user_first = first
    user_rmax_nm = rmax_nm
    n_pts = int(len(q_nm))

    need_guinier = (user_first is None) or (user_rmax_nm is None)
    guinier_info: Optional[Dict[str, Any]] = None
    if need_guinier:
        if event_bus:
            event_bus.publish(EventType.MESSAGE, {"text": "fit_sizes: running fit_guinier (in-process)…"})
        guinier_info = _guinier_from_profile(q_nm, I, sigma, atsas_dat_path)
        if event_bus:
            event_bus.publish(EventType.MESSAGE, {"text": "fit_sizes: fit_guinier completed."})

    if user_first is not None:
        first_pt = int(user_first)
    else:
        if guinier_info is None or guinier_info.get("q_min") is None:
            raise RuntimeError("fit_sizes: cannot derive --first without fit_guinier q_min.")
        first_pt = _q_to_first_point_1based(q_nm, float(guinier_info["q_min"]))

    last_pt: Optional[int] = int(last) if last is not None else None
    if first_pt < 1 or first_pt >= n_pts:
        raise ValueError(
            f"fit_sizes: require 1 <= first < n_points ({n_pts}); got first={first_pt}",
        )
    if last_pt is not None:
        if last_pt < 1 or last_pt > n_pts or first_pt >= last_pt:
            raise ValueError(
                f"fit_sizes: require 1 <= first < last <= n_points ({n_pts}); "
                f"got first={first_pt}, last={last_pt}",
            )

    if rmin_nm is not None and rmin_nm < 0:
        raise ValueError(f"fit_sizes: rmin_nm must be >= 0; got {rmin_nm}")
    if user_rmax_nm is not None and user_rmax_nm <= 0:
        raise ValueError(f"fit_sizes: rmax_nm must be > 0; got {user_rmax_nm}")
    if user_rmax_nm is not None and rmin_nm is not None and rmin_nm >= user_rmax_nm:
        raise ValueError(
            f"fit_sizes: require rmin_nm < rmax_nm; got rmin_nm={rmin_nm}, rmax_nm={user_rmax_nm}",
        )

    gnom_out_paths: List[str] = []
    failures: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    rmax_trials: List[Dict[str, Any]] = []

    eval_tmp_path: Optional[str] = None
    if user_rmax_nm is None:
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                delete=False,
                dir=output_dir,
                prefix="gnom_eval_",
                suffix=".out",
            ) as tf:
                eval_tmp_path = tf.name
        except OSError as e:
            raise RuntimeError(f"fit_sizes: failed to create temporary GNOM output file: {e}")
        assert guinier_info is not None
        best_rmax_nm, rmax_trials, rmax_failures = _optimize_rmax_nm(
            atsas_dat_path=atsas_dat_path,
            output_dir=output_dir,
            system=system,
            shape=shape,
            rg_max_nm=float(guinier_info["rg_max"]),
            rmin_nm=rmin_nm,
            rad56_nm=rad56_nm,
            first=first_pt,
            last=last_pt,
            alpha=alpha,
            nr=nr,
            eval_tmp_path=eval_tmp_path,
            timeout_s=30.0,
            event_bus=event_bus,
        )
        failures.extend(rmax_failures)
        candidates.extend(rmax_trials)
    else:
        best_rmax_nm = float(user_rmax_nm)

    if rmin_nm is not None and rmin_nm >= best_rmax_nm:
        raise ValueError(
            f"fit_sizes: require rmin_nm < rmax_nm; got rmin_nm={rmin_nm}, rmax_nm={best_rmax_nm}",
        )

    if event_bus:
        last_msg = f" --last={last_pt}" if last_pt is not None else " (no --last)"
        event_bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"GNOM (fit_sizes): final run system={system} --first={first_pt}{last_msg} "
                    f"rmax={best_rmax_nm:.4f} nm…"
                ),
            },
        )

    best_gnom_out_path = os.path.join(output_dir, f"gnom_system_{system}_rmax_{best_rmax_nm:.4f}.out")
    ok, rc, stderr, out_text_final = _run_gnom_once(
        atsas_dat_path=atsas_dat_path,
        output_dir=output_dir,
        system=system,
        rmin_nm=rmin_nm,
        rmax_nm=best_rmax_nm,
        rad56_nm=rad56_nm,
        first=first_pt,
        last=last_pt,
        alpha=alpha,
        nr=nr,
        out_path=best_gnom_out_path,
    )
    if not ok:
        raise RuntimeError(f"fit_sizes failed: final GNOM run exited with code {rc}\n{stderr}")
    gnom_out_paths = [best_gnom_out_path]
    best = _candidate_from_gnom_out(
        out_text_final,
        shape=shape,
        system=system,
        rmax_nm=best_rmax_nm,
        rmin_nm=rmin_nm,
        rad56_nm=rad56_nm,
        first=first_pt,
        last=last_pt,
        alpha=alpha,
        nr=nr,
        out_path=best_gnom_out_path,
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

    # Stable symlink to best .out (best effort)
    best_link_path = os.path.join(output_dir, f"{base}_gnom_sizes.out")
    try:
        if os.path.lexists(best_link_path):
            os.remove(best_link_path)
        rel_target = os.path.relpath(best_gnom_out_path, start=output_dir)
        os.symlink(rel_target, best_link_path)
    except OSError:
        pass

    fits_csv_path = os.path.join(output_dir, "fit_sizes_fits.csv")
    with open(fits_csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "shape",
                "system",
                "rmin_nm",
                "rmax_nm",
                "rad56_nm",
                "first",
                "last",
                "alpha",
                "nr",
                "total_estimate",
                "neg_frac",
                "score",
                "parse_dr_ok",
                "suspicious",
                "intermediate",
                "out_path",
            ]
        )
        for c in candidates:
            w.writerow(
                [
                    c.get("shape"),
                    c.get("system"),
                    c.get("rmin_nm"),
                    c.get("rmax_nm"),
                    c.get("rad56_nm"),
                    c.get("first"),
                    c.get("last"),
                    c.get("alpha"),
                    c.get("nr"),
                    c.get("total_estimate"),
                    c.get("neg_frac"),
                    c.get("score"),
                    bool(c.get("parse_dr_ok")),
                    bool(c.get("suspicious")),
                    bool(c.get("intermediate")),
                    c.get("out_path"),
                ]
            )

    # Fit-vs-exp PNG
    fit_vs_exp_png_path: Optional[str] = None
    fit_vs_exp_png_error: Optional[str] = None
    try:
        out_text_best = Path(best_gnom_out_path).read_text(errors="replace")
        parsed = _parse_out_iq_table(out_text_best)
        if parsed is None:
            fit_vs_exp_png_error = "could not parse I(q) table from .out"
        else:
            q, I_exp, _sigma_arr, I_fit = parsed
            fit_vs_exp_png_path = os.path.join(output_dir, f"{base}_fit_sizes_best_fit.png")
            fig, ax = plt.subplots(figsize=(7, 4))
            ax.plot(q, I_exp, lw=3, label="exp")
            ax.plot(q, I_fit, lw=2, label="fit")
            ax.set_xlabel("q (nm$^{-1}$)")
            ax.set_ylabel("I(q)")
            ax.set_yscale("log")
            te = best.get("total_estimate")
            if te is not None:
                ax.set_title(f"GNOM fit (system={system}): Total Estimate={float(te):.3f}")
            else:
                ax.set_title(f"GNOM fit (system={system})")
            ax.grid(True, which="both", alpha=0.25)
            ax.legend()
            fig.tight_layout()
            fig.savefig(fit_vs_exp_png_path, dpi=150, bbox_inches="tight")
            plt.close(fig)
            if event_bus:
                event_bus.publish(
                    EventType.MESSAGE,
                    {"text": f"GNOM (fit_sizes): wrote fit-vs-exp PNG: {os.path.basename(fit_vs_exp_png_path)}"},
                )
    except Exception as e:
        fit_vs_exp_png_error = f"failed to write fit-vs-exp PNG: {e}"
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"GNOM (fit_sizes): fit-vs-exp PNG not created ({fit_vs_exp_png_error})."},
            )

    # D(R) PNG (+ CSV)
    best_dr_png_path: Optional[str] = None
    best_dr_png_error: Optional[str] = None
    dr_csv_path: Optional[str] = None
    try:
        out_text_best = Path(best_gnom_out_path).read_text(errors="replace")
        dr = _parse_gnom_dr_table(out_text_best)
        if dr is None:
            best_dr_png_error = "could not parse D(R) table from best .out"
        else:
            r, d = dr
            dr_csv_path = os.path.join(output_dir, f"{base}_fit_sizes_DR.csv")
            with open(dr_csv_path, "w", newline="") as fp:
                w = csv.writer(fp)
                w.writerow(["R_nm", "D_R"])
                for rr, dd in zip(r.tolist(), d.tolist()):
                    w.writerow([rr, dd])
            png_path = os.path.splitext(best_gnom_out_path)[0] + "_DR.png"
            fig, ax = plt.subplots(figsize=(7, 4))
            ax.plot(r, d, lw=2)
            ax.set_xlabel("R (nm)")
            ax.set_ylabel("D(R)")
            te = best.get("total_estimate")
            if te is not None:
                ax.set_title(f"GNOM D(R): system={system}, Total Estimate={float(te):.3f}")
            else:
                ax.set_title(f"GNOM D(R): system={system}")
            ax.grid(True, alpha=0.25)
            fig.tight_layout()
            fig.savefig(png_path, dpi=150, bbox_inches="tight")
            plt.close(fig)
            best_dr_png_path = png_path
            if event_bus:
                event_bus.publish(
                    EventType.MESSAGE,
                    {"text": f"GNOM (fit_sizes): wrote D(R) PNG: {os.path.basename(png_path)}"},
                )
    except Exception as e:
        best_dr_png_error = f"failed to write D(R) PNG/CSV: {e}"
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"GNOM (fit_sizes): D(R) PNG not created ({best_dr_png_error})."},
            )

    fit_params_path = os.path.join(output_dir, f"{base}_fit_sizes_fit_params.yml")
    fit_params_doc: Dict[str, Any] = {
        "shape": shape,
        "system": int(system),
        "rmin_nm": rmin_nm,
        "rmax_nm": best_rmax_nm,
        "rad56_nm": rad56_nm,
        "first": first_pt,
        "last": last_pt,
        "alpha": alpha,
        "nr": nr,
    }
    if user_rg_nm is not None:
        fit_params_doc["rg_nm"] = float(user_rg_nm)
    with open(fit_params_path, "w") as fp:
        yaml.dump(fit_params_doc, fp, default_flow_style=False)

    if user_rmax_nm is not None:
        rmax_param_src = "user"
    else:
        rmax_param_src = "rmax_optimization"

    if user_first is not None:
        first_param_src = "user"
    else:
        first_param_src = "fit_guinier"

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

    best_summary_path = os.path.join(output_dir, f"{base}_fit_sizes_best.yml")
    summary = {
        "profile": profile,
        "atsas_dat_path": atsas_dat_path,
        "unit_note": "Input profile assumed q in nm^-1; GNOM uses the same units on the command line, therefore R is in nm.",
        "fit_params_path": fit_params_path,
        "fit_param_sources": {
            "rmax_nm": rmax_param_src,
            "first": first_param_src,
        },
        "fit_guinier": guinier_summary,
        "rmax_optimization_trials": rmax_trials if user_rmax_nm is None else None,
        "selected": {
            "shape": shape,
            "system": int(system),
            "rmin_nm": rmin_nm,
            "rmax_nm": best_rmax_nm,
            "rad56_nm": rad56_nm,
            "first": first_pt,
            "last": last_pt,
            "alpha": alpha,
            "nr": nr,
            "total_estimate": best.get("total_estimate"),
            "neg_frac": best.get("neg_frac"),
            "score": best.get("score"),
            "out_path": best_gnom_out_path,
        },
        "candidates": candidates,
        "failures": failures,
        "best_symlink_out_path": best_link_path,
        "fits_csv_path": fits_csv_path,
        "fit_vs_exp_png_path": fit_vs_exp_png_path,
        "fit_vs_exp_png_error": fit_vs_exp_png_error,
        "best_dr_png_path": best_dr_png_path,
        "best_dr_png_error": best_dr_png_error,
        "dr_csv_path": dr_csv_path,
    }
    with open(best_summary_path, "w") as f:
        yaml.dump(summary, f, default_flow_style=False)

    from autosaxs.core.report_fragments import write_skill_report_fragments

    md_parts = [f"### GNOM size distribution (fit_sizes, shape={shape})\n"]
    if fit_vs_exp_png_path and os.path.isfile(fit_vs_exp_png_path):
        md_parts.append(f"![Selected GNOM fit vs data]({os.path.basename(fit_vs_exp_png_path)})\n")
    if best_dr_png_path and os.path.isfile(best_dr_png_path):
        md_parts.append(f"![D(R)]({os.path.basename(best_dr_png_path)})\n")
    summary_refs = [
        {"role": "fit_sizes_summary", "path": os.path.basename(best_summary_path), "format": "text"},
    ]
    if dr_csv_path and os.path.isfile(dr_csv_path):
        summary_refs.append({"role": "dr_csv", "path": os.path.basename(dr_csv_path), "format": "csv", "row": 0})
    write_skill_report_fragments(
        output_dir,
        base,
        "fit_sizes",
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
        "best_dr_png_path": best_dr_png_path or "",
        "best_dr_png_error": best_dr_png_error or "",
        "dr_csv_path": dr_csv_path or "",
    }

