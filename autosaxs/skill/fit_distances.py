from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

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
    use_cache: bool = True,
) -> Dict[str, Union[str, List[str]]]:
    """
    Run ATSAS GNOM to obtain a pair distance distribution function p(r) for a **monodisperse** system from a 1D SAXS curve.

    The skill invokes `gnom` from `PATH` in command-line mode, explicitly enforcing `--system=0` and scanning candidate
    `--rmax` values to choose a robust solution. Input curves are typically in nm^-1 and are converted internally to Å^-1
    before calling GNOM. GNOM produces one `.out` file per run; the `.out` contains, among other things, the p(r) section.

    ### Arguments
    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where the GNOM outputs are written (one subdirectory per input profile).
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


def _score_gnom_solution(out_text: str) -> tuple[float, Dict[str, Any]]:
    total = _parse_gnom_total_estimate(out_text)
    pr = _parse_gnom_pr_table(out_text)
    diag: Dict[str, Any] = {"total_estimate": total}
    if pr is None:
        return -1e9, {**diag, "parse_pr_ok": False}
    _r, p = pr
    diag["parse_pr_ok"] = True
    if p.size == 0 or not np.any(np.isfinite(p)):
        return -1e9, {**diag, "parse_pr_ok": False}
    p = np.asarray(p, dtype=float)
    p_abs_max = float(np.nanmax(np.abs(p))) if np.any(np.isfinite(p)) else 0.0
    if p_abs_max <= 0:
        return -1e9, {**diag, "p_abs_max": p_abs_max}
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
    total_v = float(total) if total is not None else 0.0
    score = total_v - 5.0 * neg_frac - 2.0 * tail_ratio - 0.5 * smooth
    return float(score), diag


def _estimate_rmax_scan_grid_A(q_A: np.ndarray) -> np.ndarray:
    q = np.asarray(q_A, dtype=float)
    q = q[np.isfinite(q)]
    q = q[q > 0]
    if q.size < 5:
        return np.asarray([50.0, 75.0, 100.0, 125.0, 150.0], dtype=float)
    q_min = float(np.min(q))
    q_max = float(np.max(q))
    r_lo = max(10.0, (2.0 * np.pi) / max(q_max, 1e-6))
    r_hi = min(5000.0, (2.0 * np.pi) / max(q_min, 1e-6))
    if not np.isfinite(r_hi) or r_hi <= r_lo:
        r_hi = r_lo * 6.0
    if r_hi < r_lo * 1.5:
        r_hi = r_lo * 1.5
    grid = np.linspace(r_lo, r_hi, 15)
    return np.round(grid.astype(float), 1)


@apply_batch(stem_from_keys="profile", per_sample_subdir="always")
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash=None,
    include_config_in_hash=False,
)
def _fit_distances_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
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
        event_bus.publish(EventType.MESSAGE, {"text": "GNOM (fit_distances): preparing ATSAS .dat input…"})

    q_nm, I, sigma = load_saxs_1d_any(profile)
    q_nm, I, sigma = ensure_q_nm(q_nm, I, sigma)
    q_A = np.asarray(q_nm, dtype=float) / 10.0

    atsas_dat_path = os.path.join(output_dir, f"{base}_atsas_Ainv.dat")
    write_saxs_atsas_format(atsas_dat_path, q_A, I, sigma)

    rmax_grid_A = _estimate_rmax_scan_grid_A(q_A)
    gnom_out_paths: List[str] = []
    candidates: List[Dict[str, Any]] = []

    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {"text": f"GNOM (fit_distances): scanning rmax over {len(rmax_grid_A)} candidates…"},
        )

    for idx, rmax_A in enumerate(rmax_grid_A):
        out_path = os.path.join(output_dir, f"gnom_rmax_{float(rmax_A):.1f}.out")
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"GNOM {idx + 1}/{len(rmax_grid_A)}: rmax={rmax_A:.1f} Å…"},
            )
        proc = subprocess.run(
            ["gnom", "--system=0", f"--rmax={float(rmax_A):.1f}", "--output", out_path, atsas_dat_path],
            cwd=output_dir,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            candidates.append(
                {
                    "rmax_A": float(rmax_A),
                    "out_path": out_path,
                    "ok": False,
                    "returncode": int(proc.returncode),
                    "stderr": (proc.stderr or "")[:2000],
                    "score": -1e9,
                }
            )
            continue
        if not os.path.isfile(out_path):
            candidates.append(
                {
                    "rmax_A": float(rmax_A),
                    "out_path": out_path,
                    "ok": False,
                    "returncode": int(proc.returncode),
                    "stderr": "gnom reported success but output file was not created",
                    "score": -1e9,
                }
            )
            continue

        out_text = Path(out_path).read_text(errors="replace")
        score, diag = _score_gnom_solution(out_text)
        gnom_out_paths.append(out_path)
        candidates.append(
            {
                "rmax_A": float(rmax_A),
                "out_path": out_path,
                "ok": True,
                "returncode": int(proc.returncode),
                "score": float(score),
                **diag,
            }
        )

    if not gnom_out_paths:
        raise RuntimeError("fit_distances failed: GNOM produced no output .out files")

    best = max((c for c in candidates if c.get("ok")), key=lambda c: float(c.get("score", -1e9)))
    best_gnom_out_path = str(best["out_path"])

    best_summary_path = os.path.join(output_dir, f"{base}_fit_distances_best.yml")
    summary = {
        "profile": profile,
        "atsas_dat_path": atsas_dat_path,
        "unit_note": "Input profile assumed q in nm^-1; converted to Å^-1 for GNOM. Therefore rmax values are in Å.",
        "selected": {"rmax_A": float(best["rmax_A"]), "out_path": best_gnom_out_path, "score": float(best["score"])},
        "candidates": candidates,
    }
    with open(best_summary_path, "w") as f:
        yaml.dump(summary, f, default_flow_style=False)

    return {
        "output_subdir": output_dir,
        "gnom_out_paths": gnom_out_paths,
        "best_gnom_out_path": best_gnom_out_path,
        "best_summary_path": best_summary_path,
    }

