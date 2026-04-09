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


def fit_distances(
    profile: PathExpressionArg,
    output_dir: str = ".",
    *,
    rg_nm: float,
    use_cache: bool = True,
) -> Dict[str, Union[str, List[str]]]:
    """
    Run ATSAS DATGNOM to obtain a pair distance distribution function p(r) for a **monodisperse** system from a 1D SAXS curve.

    The skill invokes `gnom` from `PATH` in command-line mode, explicitly enforcing `--system=0` and running
    an automated GNOM-based transform via `datgnom` with a user-provided `Rg` (in nm). Input curves are expected
    in nm^-1 and are passed through in ATSAS `.dat` format. DATGNOM produces a single `.out` file; the `.out` contains,
    among other things, the p(r) section.

    ### Arguments
    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where the GNOM outputs are written (one subdirectory per input profile).
    - `rg_nm` (float): Expected radius of gyration (Rg) in nm (typically from Guinier analysis).
    - `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

    ### Returns
    `dict[str, str]` with: `output_subdir`, `gnom_out_paths`, `best_gnom_out_path`, `best_summary_path`.
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
        rg_nm=float(rg_nm),
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
    cmd: List[str] = ["datgnom", f"--rg={float(rg_nm):.6g}"]
    if first is not None:
        cmd.append(f"--first={int(first)}")
    if last is not None:
        cmd.append(f"--last={int(last)}")
    if smooth is not None:
        cmd.append(f"--smooth={float(smooth):.6g}")
    cmd += ["-o", out_path, atsas_dat_path]
    proc = subprocess.run(cmd, cwd=output_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        return False, int(proc.returncode), (proc.stderr or "")[:2000], ""
    if not os.path.isfile(out_path):
        return False, int(proc.returncode), "gnom reported success but output file was not created", ""
    try:
        out_text = Path(out_path).read_text(errors="replace")
    except OSError as e:
        return False, int(proc.returncode), f"failed to read DATGNOM output: {e}", ""
    return True, int(proc.returncode), (proc.stderr or "")[:2000], out_text


@apply_batch(stem_from_keys="profile", per_sample_subdir="always")
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash=None,
    kwargs_for_hash_keys=["rg_nm"],
    include_config_in_hash=False,
)
def _fit_distances_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    rg_nm: float,
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

    gnom_out_paths: List[str] = []
    candidates: List[Dict[str, Any]] = []

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
        diag = _summarize_out_quality(out_text)
        rmax_nm = _parse_out_real_space_rmax(out_text)
        pr = _parse_gnom_pr_table(out_text)
        prm: Dict[str, Any] = {}
        if pr is not None:
            r, p = pr
            prm = _pr_metrics(r, p)
        suspicious = bool(re.search(r"SUSPICIOUS", out_text or "", flags=re.IGNORECASE))
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

    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {"text": f"DATGNOM (fit_distances): auto-searching --first/--last with Rg={float(rg_nm):.4f} nm…"},
        )

    n_pts = int(len(q_nm))
    first_grid = list(range(1, min(26, max(2, n_pts - 5)) + 1))
    smooth_grid = [2.0]

    def _run_last_grid(last_grid: List[int]) -> None:
        last_grid = sorted({int(x) for x in last_grid if 5 <= int(x) <= n_pts})
        if not last_grid:
            last_grid = [n_pts]
        for cand_last in last_grid:
            for cand_first in first_grid:
                if cand_first >= cand_last:
                    continue
                for cand_smooth in smooth_grid:
                    tmp_path: Optional[str] = None
                    try:
                        with tempfile.NamedTemporaryFile(
                            mode="w",
                            suffix=".out",
                            prefix="datgnom_tmp_",
                            dir=output_dir,
                            delete=False,
                        ) as tf:
                            tmp_path = tf.name
                        ok, rc, stderr, out_text = _run_datgnom_once(
                            atsas_dat_path=atsas_dat_path,
                            output_dir=output_dir,
                            rg_nm=float(rg_nm),
                            first=int(cand_first),
                            last=int(cand_last),
                            smooth=float(cand_smooth),
                            out_path=tmp_path,
                        )
                        if not ok:
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
                    finally:
                        if tmp_path:
                            try:
                                os.remove(tmp_path)
                            except OSError:
                                pass

    # Round 1: coarse search over a broad range of --last.
    last_grid_round1 = [150, 180, 200, 220, 250, 300]
    _run_last_grid(last_grid_round1)

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

    # Round 2: refine --last around the best coarse candidate.
    best_last_r1 = best_round1.get("last")
    if best_last_r1 is not None:
        center = int(best_last_r1)
        neighborhood = [-40, -20, -10, -5, 0, 5, 10, 20, 40]
        last_grid_round2 = [center + d for d in neighborhood]
        _run_last_grid(last_grid_round2)

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

    best_summary_path = os.path.join(output_dir, f"{base}_fit_distances_best.yml")
    summary = {
        "profile": profile,
        "atsas_dat_path": atsas_dat_path,
        "unit_note": "Input profile assumed q in nm^-1; DATGNOM uses the same units, therefore Rg and r are in nm.",
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
        "best_symlink_out_path": best_link_path,
        "fits_csv_path": fits_csv_path,
        "fit_vs_exp_png_path": fit_vs_exp_png_path or "",
        "fit_vs_exp_png_error": fit_vs_exp_png_error or "",
        "best_pr_png_path": best_pr_png_path or "",
        "best_pr_png_error": best_pr_png_error or "",
    }

