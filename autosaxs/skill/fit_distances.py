from __future__ import annotations

import csv
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

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
    run_with_cache,
    write_saxs_atsas_format,
)
from .common import PathExpressionArg, coerce_path_expression, expand_files_from_unwrapped
from ..guinier import find_guinier_region, run_autorg_atsas


def fit_distances(
    profile: PathExpressionArg,
    output_dir: str = ".",
    *,
    rg_nm: Optional[float] = None,
    first: Optional[int] = None,
    last: Optional[int] = None,
    smooth: Optional[float] = None,
    use_cache: bool = True,
) -> Dict[str, Union[str, List[str]]]:
    """
    Run ATSAS DATGNOM to obtain a pair distance distribution function p(r) for a **monodisperse** system from a 1D SAXS curve.

    The skill invokes `gnom` from `PATH` in command-line mode, explicitly enforcing `--system=0` and running
    an automated GNOM-based transform via `datgnom` with `Rg` (in nm). Input curves are expected in nm^-1 and are
    passed through in ATSAS `.dat` format. If `rg_nm` and/or `first` are omitted, ATSAS ``autorg`` is run on the
    profile: user-supplied values take precedence over AUTORG for each parameter. If AUTORG fails, the skill falls
    back to the previous grid search for unset parameters; if `rg_nm` is still missing, a sliding-window Guinier fit
    (:func:`autosaxs.guinier.find_guinier_region`) supplies `Rg`. When AUTORG succeeds and `last` is omitted, DATGNOM
    is run without ``--last``. DATGNOM produces a single `.out` file; the `.out` contains, among other things, the p(r) section.

    ### Arguments
    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where the GNOM outputs are written (one subdirectory per input profile).
    - `rg_nm` (float | None, default `None`): Expected Rg in nm. If omitted, taken from AUTORG when possible, else from Guinier search.
    - `first` (int | None, default `None`): DATGNOM `--first`. If omitted, taken from AUTORG Guinier interval when possible. If set with `last`, runs one fit. If set alone, `last` is auto-searched unless AUTORG succeeded and `last` is omitted (then DATGNOM runs without `--last`). If omitted and AUTORG fails or gives no interval, `first` is auto-searched.
    - `last` (int | None, default `None`): DATGNOM `--last`. Same pairing rules as `first`; if set alone, `first` is auto-searched. Omitted with successful AUTORG implies a single DATGNOM run without `--last`.
    - `smooth` (float | None, default `None`): DATGNOM `--smooth`. If set, that value is used and smoothness is not searched. If omitted during auto-search, trials use smoothness `2.0`. In full manual mode (`first` and `last` both set), omitted means do not pass `--smooth`.
    - `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

    ### Returns
    `dict[str, str]` with: `output_subdir`, `gnom_out_paths`, `best_gnom_out_path`, `best_summary_path`,
    `fit_params_path` (YAML with the `rg_nm`, `first`, and `last` used for the final DATGNOM fit), plus the
    existing artifact paths (`best_symlink_out_path`, `fits_csv_path`, PNG paths, etc.).
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    profile = coerce_path_expression(profile)
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


def _parse_gnom_total_estimate(out_text: str) -> Optional[float]:
    patterns = [
        r"Total\s+Estimate\s*[:=]\s*([0-9]*\.?[0-9]+)",
        r"TOTAL\s+ESTIMATE\s*[:=]\s*([0-9]*\.?[0-9]+)",
        r"\bTOTAL\b\s*[:=]\s*([0-9]*\.?[0-9]+)",
    ]
    for pat in patterns:
        m = re.search(pat, out_text, flags=re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _parse_gnom_pr_table(out_text: str) -> Optional[tuple[np.ndarray, np.ndarray]]:
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
            p = np.asarray([x[1] for x in blk], dtype=float)
            if np.all(np.diff(r) >= 0):
                return r, p
    return None


def _parse_out_real_space_rmax(out_text: str) -> Optional[float]:
    """
    Parse DATGNOM/GNOM reported real-space maximum from the configuration/results header.

    Expected line shape:
      Real space range: 0.0000 to 56.0700
    """
    m = re.search(r"Real\s+space\s+range:\s*[0-9]*\.?[0-9]+\s*to\s*([0-9]*\.?[0-9]+)", out_text or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


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
    total = _parse_gnom_total_estimate(out_text)
    pr = _parse_gnom_pr_table(out_text)
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


def _parse_out_iq_table(
    out_text: str,
) -> Optional[tuple[np.ndarray, np.ndarray, Optional[np.ndarray], np.ndarray]]:
    """
    Parse the experimental/scattering section from an ATSAS .out file.

    Returns (q, I_exp, I_fit_or_reg_or_lastcol) and optionally sigma if present.
    The exact column semantics vary by ATSAS version and settings; this parser uses
    common header hints and falls back to selecting sensible numeric columns.
    """
    lines = (out_text or "").splitlines()
    header_idx: Optional[int] = None
    for i, ln in enumerate(lines):
        s = ln.strip().upper()
        # Typical headers contain S/Q and EXP/ERROR/REG/FIT wording.
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
        # Filter out obvious non-data lines.
        if not np.isfinite(vals[0]) or vals[0] <= 0:
            if rows:
                break
            continue
        rows.append(vals)

    if len(rows) < 8:
        # Fallback: pick the last large numeric block in the file.
        rows = []
        blocks: List[List[List[float]]] = []
        cur: List[List[float]] = []
        for ln in lines:
            st = ln.strip()
            if not st:
                if cur:
                    blocks.append(cur)
                    cur = []
                continue
            parts = re.split(r"[,\s]+", st)
            if len(parts) < 3:
                if cur:
                    blocks.append(cur)
                    cur = []
                continue
            try:
                vals = [float(x) for x in parts]
            except ValueError:
                if cur:
                    blocks.append(cur)
                    cur = []
                continue
            cur.append(vals)
        if cur:
            blocks.append(cur)
        for blk in reversed(blocks):
            if len(blk) >= 8 and len(blk[0]) >= 3:
                rows = blk
                break

    if len(rows) < 8:
        return None

    ncol = max(len(r) for r in rows)
    arr = np.full((len(rows), ncol), np.nan, dtype=float)
    for i, r in enumerate(rows):
        arr[i, : len(r)] = r
    q = arr[:, 0]
    I_exp = arr[:, 1]
    sigma = arr[:, 2] if ncol >= 3 else None

    # Prefer a "fit/regularized" column: last column if exists, else 4th, else 3rd.
    if ncol >= 5:
        I_fit = arr[:, 4]
    elif ncol >= 4:
        I_fit = arr[:, 3]
    else:
        I_fit = None
    if I_fit is None or not np.any(np.isfinite(I_fit)):
        # fall back to last available numeric column beyond I_exp
        for j in range(ncol - 1, 1, -1):
            cand = arr[:, j]
            if np.any(np.isfinite(cand)):
                I_fit = cand
                break
    if I_fit is None:
        return None
    return (
        q.astype(float),
        I_exp.astype(float),
        sigma.astype(float) if sigma is not None else None,
        I_fit.astype(float),
    )


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
    use_cache: bool = True,
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

    need_autorg = (rg_nm is None) or (first is None)
    autorg_result: Optional[Dict[str, Any]] = None
    if need_autorg:
        if event_bus:
            event_bus.publish(EventType.MESSAGE, {"text": "fit_distances: running AUTORG…"})
        autorg_result = run_autorg_atsas(atsas_dat_path, q_nm)
        if autorg_result is not None:
            if rg_nm is None:
                rg_nm = float(autorg_result["Rg"])
            if first is None:
                fp = autorg_result.get("first_point_1based")
                if fp is not None:
                    first = int(fp)
            if event_bus:
                event_bus.publish(EventType.MESSAGE, {"text": "fit_distances: AUTORG succeeded."})
        elif event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": "fit_distances: AUTORG failed or unparsable; using parameter search / Guinier fallback."},
            )

    if rg_nm is None:
        gr = find_guinier_region(q_nm, I, sigma=sigma)
        if gr is None:
            raise RuntimeError(
                "fit_distances: Rg is unknown (no rg_nm, AUTORG failed, and Guinier region search failed).",
            )
        rg_nm = float(gr["rg"])
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"fit_distances: Rg from sliding-window Guinier fit: {rg_nm:.4f} nm"},
            )

    rg_nm = float(rg_nm)
    autorg_ok = bool(need_autorg and autorg_result is not None)

    gnom_out_paths: List[str] = []
    candidates: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []

    def _record_candidate(
        *,
        out_path: str,
        rc: int,
        stderr: str,
        out_text: str,
        cand_first: Optional[int],
        cand_last: Optional[int],
        cand_smooth: Optional[float],
        intermediate: bool,
    ) -> None:
        # Parse once per candidate: previous implementation parsed p(r) twice (via _summarize_out_quality + here).
        total = _parse_gnom_total_estimate(out_text)
        suspicious = bool(re.search(r"SUSPICIOUS", out_text or "", flags=re.IGNORECASE))
        rmax_nm = _parse_out_real_space_rmax(out_text)
        pr = _parse_gnom_pr_table(out_text)

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
        if not intermediate:
            gnom_out_paths.append(out_path)
        candidates.append(
            {
                "rg_nm": float(rg_nm),
                "first": int(cand_first) if cand_first is not None else None,
                "last": int(cand_last) if cand_last is not None else None,
                "smooth": float(cand_smooth) if cand_smooth is not None else None,
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
        )

    n_pts = int(len(q_nm))
    manual = first is not None and last is not None
    search_first = first is None
    search_last = last is None
    search_smooth = smooth is None

    autorg_omit_last_mode = bool(autorg_ok and last is None and first is not None)

    if autorg_omit_last_mode:
        assert first is not None
        fi = int(first)
        if fi < 1 or fi >= n_pts:
            raise ValueError(
                f"fit_distances: require 1 <= first < n_points ({n_pts}) for DATGNOM without --last; got first={fi}",
            )
        cand_smooth = float(smooth) if smooth is not None else 2.0
        sm_msg = f" --smooth={cand_smooth:.6g}"
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {
                    "text": (
                        f"DATGNOM (fit_distances): after AUTORG, single run --first={fi} (no --last){sm_msg} "
                        f"with Rg={float(rg_nm):.4f} nm…"
                    ),
                },
            )
        out_path_final = os.path.join(output_dir, f"datgnom_rg_{float(rg_nm):.4f}.out")
        ok, rc, stderr, out_text = _run_datgnom_once(
            atsas_dat_path=atsas_dat_path,
            output_dir=output_dir,
            rg_nm=float(rg_nm),
            first=fi,
            last=None,
            smooth=cand_smooth,
            out_path=out_path_final,
        )
        if not ok:
            raise RuntimeError(f"fit_distances failed: datgnom exited with code {rc}\n{stderr}")
        gnom_out_paths.append(out_path_final)
        best_gnom_out_path = out_path_final
        _record_candidate(
            out_path=out_path_final,
            rc=rc,
            stderr=stderr,
            out_text=out_text,
            cand_first=fi,
            cand_last=None,
            cand_smooth=cand_smooth,
            intermediate=False,
        )
        best = dict(candidates[-1])
        best = {**best, "out_path": best_gnom_out_path, "intermediate": False}
    elif manual:
        assert first is not None and last is not None
        fi, la = int(first), int(last)
        if fi < 1 or la > n_pts or fi >= la:
            raise ValueError(
                f"fit_distances: require 1 <= first < last <= n_points ({n_pts}); got first={fi}, last={la}",
            )
        msg_extra = f" --smooth={float(smooth):.6g}" if smooth is not None else ""
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {
                    "text": (
                        f"DATGNOM (fit_distances): fixed --first={fi} --last={la}{msg_extra} "
                        f"with Rg={float(rg_nm):.4f} nm…"
                    ),
                },
            )
        out_path_final = os.path.join(output_dir, f"datgnom_rg_{float(rg_nm):.4f}.out")
        ok, rc, stderr, out_text = _run_datgnom_once(
            atsas_dat_path=atsas_dat_path,
            output_dir=output_dir,
            rg_nm=float(rg_nm),
            first=fi,
            last=la,
            smooth=float(smooth) if smooth is not None else None,
            out_path=out_path_final,
        )
        if not ok:
            raise RuntimeError(f"fit_distances failed: datgnom exited with code {rc}\n{stderr}")
        gnom_out_paths.append(out_path_final)
        best_gnom_out_path = out_path_final
        _record_candidate(
            out_path=out_path_final,
            rc=rc,
            stderr=stderr,
            out_text=out_text,
            cand_first=fi,
            cand_last=la,
            cand_smooth=float(smooth) if smooth is not None else None,
            intermediate=False,
        )
        best = dict(candidates[-1])
        best = {**best, "out_path": best_gnom_out_path, "intermediate": False}
    else:
        fixed_parts: List[str] = []
        if not search_first:
            fixed_parts.append(f"--first={int(first)}")
        if not search_last:
            fixed_parts.append(f"--last={int(last)}")
        if not search_smooth:
            fixed_parts.append(f"--smooth={float(smooth):.6g}")
        search_parts: List[str] = []
        if search_first:
            search_parts.append("first")
        if search_last:
            search_parts.append("last")
        if search_smooth:
            search_parts.append("smooth")
        mode_msg = (
            f"auto-search [{', '.join(search_parts)}]"
            if search_parts
            else "auto-search"
        )
        if fixed_parts:
            mode_msg += f"; fixed [{', '.join(fixed_parts)}]"
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {
                    "text": (
                        f"DATGNOM (fit_distances): {mode_msg} with Rg={float(rg_nm):.4f} nm…"
                    ),
                },
            )

        first_grid = list(range(1, min(26, max(2, n_pts - 5)) + 1))
        if search_first:
            first_values = first_grid
        else:
            assert first is not None
            fi0 = int(first)
            if fi0 < 1 or fi0 >= n_pts:
                raise ValueError(
                    f"fit_distances: require 1 <= first < n_points ({n_pts}); got first={fi0}",
                )
            first_values = [fi0]

        if search_smooth:
            smooth_grid = [2.0]
        else:
            assert smooth is not None
            smooth_grid = [float(smooth)]

        # Reuse one temp path for all intermediate evaluations to avoid per-candidate
        # NamedTemporaryFile create/delete overhead.
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

        def _normalize_last_list(raw: List[int], *, from_user_grid: bool) -> List[int]:
            if from_user_grid:
                out = sorted({int(x) for x in raw if 5 <= int(x) <= n_pts})
                return out if out else [n_pts]
            if len(raw) != 1:
                raise RuntimeError("fit_distances: internal error, fixed last expects one value")
            la0 = int(raw[0])
            if la0 < 1 or la0 > n_pts:
                raise ValueError(
                    f"fit_distances: require 1 <= last <= n_points ({n_pts}); got last={la0}",
                )
            return [la0]

        def _run_last_grid(last_grid_raw: List[int], *, from_user_grid: bool) -> None:
            last_list = _normalize_last_list(last_grid_raw, from_user_grid=from_user_grid)
            for cand_last in last_list:
                for cand_first in first_values:
                    if cand_first >= cand_last:
                        continue
                    for cand_smooth in smooth_grid:
                        ok, rc, stderr, out_text = _run_datgnom_once(
                            atsas_dat_path=atsas_dat_path,
                            output_dir=output_dir,
                            rg_nm=float(rg_nm),
                            first=int(cand_first),
                            last=int(cand_last),
                            smooth=float(cand_smooth),
                            out_path=eval_tmp_path,
                        )
                        if not ok:
                            failures.append(
                                {
                                    "rg_nm": float(rg_nm),
                                    "first": int(cand_first),
                                    "last": int(cand_last),
                                    "smooth": float(cand_smooth),
                                    "ok": False,
                                    "returncode": int(rc),
                                    "stderr": stderr,
                                }
                            )
                            if event_bus:
                                msg = f"DATGNOM (fit_distances): trial failed (rc={rc})."
                                if stderr:
                                    msg += f" {stderr}"
                                event_bus.publish(EventType.MESSAGE, {"text": msg})
                            continue
                        # Intermediate candidates are not persisted; keep only metrics.
                        _record_candidate(
                            out_path="",
                            rc=rc,
                            stderr=stderr,
                            out_text=out_text,
                            cand_first=cand_first,
                            cand_last=cand_last,
                            cand_smooth=cand_smooth,
                            intermediate=True,
                        )

        # Round 1: coarse search over --last (skipped when last is user-fixed).
        if search_last:
            last_grid_round1 = [150, 180, 200, 220, 250, 300]
            _run_last_grid(last_grid_round1, from_user_grid=True)
        else:
            assert last is not None
            _run_last_grid([int(last)], from_user_grid=False)

        if not candidates:
            raise RuntimeError("fit_distances failed: DATGNOM produced no successful candidates")

        def _total_estimate_key(c: Dict[str, Any]) -> float:
            te = c.get("total_estimate")
            try:
                return float(te) if te is not None else float("-inf")
            except Exception:
                return float("-inf")

        def _is_suspicious(c: Dict[str, Any]) -> bool:
            return bool(c.get("suspicious"))

        def _select_best(cs: List[Dict[str, Any]]) -> Dict[str, Any]:
            if not cs:
                raise RuntimeError("fit_distances failed: DATGNOM produced no output .out files")
            # Prefer non-suspicious when possible.
            non_susp = [c for c in cs if not _is_suspicious(c)]
            pool = non_susp if non_susp else cs
            # Default: choose by Total Estimate (descending).
            # Tie-breakers: prefer lower neg_frac, lower tail_ratio if available.
            def key_default(c: Dict[str, Any]) -> tuple[float, float, float]:
                neg = c.get("neg_frac")
                tail = c.get("tail_ratio")
                try:
                    neg_v = float(neg) if neg is not None else 1.0
                except Exception:
                    neg_v = 1.0
                try:
                    tail_v = float(tail) if tail is not None else 1.0
                except Exception:
                    tail_v = 1.0
                return (-_total_estimate_key(c), neg_v, tail_v)

            return sorted(pool, key=key_default)[0]

        best_round1 = _select_best(candidates)

        # Round 2: refine --last around the best coarse candidate (only when last is being searched).
        if search_last:
            best_last_r1 = best_round1.get("last")
            if best_last_r1 is not None:
                center = int(best_last_r1)
                neighborhood = [-40, -20, -10, -5, 0, 5, 10, 20, 40]
                last_grid_round2 = [center + d for d in neighborhood]
                _run_last_grid(last_grid_round2, from_user_grid=True)

        best = _select_best(candidates)

        # Re-run best candidate to a persistent output file (only final output is saved).
        best_first = best.get("first")
        best_last = best.get("last")
        best_smooth = best.get("smooth")
        out_path_final = os.path.join(output_dir, f"datgnom_rg_{float(rg_nm):.4f}.out")
        ok, rc, stderr, out_text = _run_datgnom_once(
            atsas_dat_path=atsas_dat_path,
            output_dir=output_dir,
            rg_nm=float(rg_nm),
            first=int(best_first) if best_first is not None else None,
            last=int(best_last) if best_last is not None else None,
            smooth=float(best_smooth) if best_smooth is not None else None,
            out_path=out_path_final,
        )
        if not ok:
            raise RuntimeError(f"fit_distances failed: final datgnom run exited with code {rc}\n{stderr}")
        gnom_out_paths.append(out_path_final)
        best_gnom_out_path = out_path_final
        # Ensure the selected metadata reflects the persisted final output.
        best = {**best, "out_path": best_gnom_out_path, "intermediate": False}

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
                    c.get("tail_ratio"),
                    c.get("smoothness"),
                    bool(c.get("ok")),
                    c.get("out_path"),
                ]
            )

    fit_vs_exp_png_path: Optional[str] = None
    fit_vs_exp_png_error: Optional[str] = None
    try:
        parsed = _parse_out_iq_table(out_text)
        if parsed is None:
            fit_vs_exp_png_error = "could not parse I(q) table from .out"
        else:
            q, I_exp, sigma_arr, I_fit = parsed
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
        pr = _parse_gnom_pr_table(out_text)
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
    elif need_autorg and autorg_result is not None:
        rg_param_src = "autorg"
    else:
        rg_param_src = "guinier"

    if user_first is not None:
        first_param_src = "user"
    elif need_autorg and autorg_result is not None and autorg_result.get("first_point_1based") is not None:
        first_param_src = "autorg"
    else:
        first_param_src = "search"

    if user_last is not None:
        last_param_src = "user"
    elif autorg_omit_last_mode:
        last_param_src = "omitted_no_datgnom_last"
    else:
        last_param_src = "search"

    autorg_summary: Optional[Dict[str, Any]] = None
    if need_autorg:
        autorg_summary = {
            "ok": autorg_ok,
            "Rg": autorg_result.get("Rg") if autorg_result else None,
            "first_point_1based": autorg_result.get("first_point_1based") if autorg_result else None,
            "last_point_1based": autorg_result.get("last_point_1based") if autorg_result else None,
            "guinier_interval": autorg_result.get("guinier_interval") if autorg_result else None,
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
        },
        "autorg": autorg_summary,
        "selected": {
            "rg_nm": float(best["rg_nm"]),
            "first": best.get("first"),
            "last": best.get("last"),
            "smooth": best.get("smooth"),
            "rmax_nm": best.get("rmax_nm"),
            "out_path": best_gnom_out_path,
            "suspicious": bool(best.get("suspicious")),
            "total_estimate": best.get("total_estimate"),
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

