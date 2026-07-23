"""
Radiation-damage-aware averaging of per-frame 1D SAXS curves.

Compares each frame to the first (lexicographic) reference using reduced chi-squared
and CorMap (Franke et al., Nature Methods 2015; Schilling 1990), then merges
accepted frames with inverse-variance weighting.
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from math import log
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from .common import (
    DatPathExpressionArg,
    coerce_dat_path_expression,
    expand_files_from_unwrapped,
)
from .deps import (
    EventBus,
    EventType,
    _strip_sub_int_prefix,
    calc_chi2,
    read_saxs,
    run_with_cache,
    write_saxs,
)


_SIGMA_FRAC_DEFAULT = 0.03
_HIGH_DAMAGE_MSG = "RADIATION DAMAGE IS HIGH. CONSIDER DECREASING EXPOSURE TIME"


@dataclass
class _FrameCurve:
    path: str
    q: np.ndarray
    I: np.ndarray
    sigma: np.ndarray


@dataclass
class _FrameRow:
    frame_index: int
    filename: str
    chi2: Optional[float]
    cormap_p: Optional[float]
    status: str
    warn_reason: str


class _LongestRunOfHeads:
    """Schilling (1990) distribution for longest run of heads or tails."""

    def __init__(self) -> None:
        self._cache: Dict[Tuple[int, int], int] = {}

    def _a(self, n: int, c: int) -> int:
        if n <= c:
            return 2**n
        key = (n, c)
        if key in self._cache:
            return self._cache[key]
        s = 0
        for j in range(c, -1, -1):
            s += self._a(n - 1 - j, c)
        self._cache[key] = s
        return s

    def _b(self, n: int, c: int) -> int:
        return 2 * self._a(n - 1, c - 1)

    def proba_longer_run(self, n: int, c: int) -> float:
        """P(longest run of heads or tails > c) for n fair coin tosses."""
        if c > n or c < 0:
            return 0.0
        if c == 0:
            return 0.0
        delta = (2**n) - self._b(n, c)
        if delta <= 0:
            return 0.0
        return min(2.0 ** (log(delta, 2.0) - n), 1.0)


_LROH = _LongestRunOfHeads()


def _measure_longest_run_signs(delta: np.ndarray) -> int:
    """Longest consecutive run of equal non-zero signs in delta."""
    d = np.asarray(delta, dtype=float).ravel()
    signs = np.sign(d)
    signs = signs[signs != 0]
    n = int(signs.size)
    if n == 0:
        return 0
    longest = 1
    run = 1
    for i in range(1, n):
        if signs[i] == signs[i - 1]:
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    return longest


def _cormap_pvalue(I_ref: np.ndarray, I_k: np.ndarray) -> float:
    """
    CorMap p-value for two curves (Franke et al., 2015).

    Returns probability that the observed longest run of same-sign residuals
    could arise if both curves are equivalent.
    """
    delta = np.ascontiguousarray(I_k - I_ref, dtype=np.float64).ravel()
    c = _measure_longest_run_signs(delta)
    n = int(delta.size)
    if n == 0:
        return 1.0
    if c <= 0:
        return 1.0
    return _LROH.proba_longer_run(n, c - 1)


def _default_sigma(I: np.ndarray) -> np.ndarray:
    I = np.asarray(I, dtype=float)
    return _SIGMA_FRAC_DEFAULT * np.maximum(np.abs(I), 1e-300)


def _ensure_sigma(sigma: Optional[np.ndarray], I: np.ndarray) -> np.ndarray:
    if sigma is None or not np.all(np.isfinite(sigma)) or np.any(np.asarray(sigma) <= 0):
        return _default_sigma(I)
    return np.asarray(sigma, dtype=float)


def _load_curve(path: str) -> _FrameCurve:
    q, I, sigma, _ = read_saxs(path)
    q = np.asarray(q, dtype=float)
    I = np.asarray(I, dtype=float)
    sigma_eff = _ensure_sigma(sigma, I)
    return _FrameCurve(path=os.path.abspath(path), q=q, I=I, sigma=sigma_eff)


def _interp_to(q_ref: np.ndarray, q: np.ndarray, y: np.ndarray) -> np.ndarray:
    return np.interp(q_ref, q, y, left=np.nan, right=np.nan)


def _pairwise_chi2(
    ref: _FrameCurve,
    other: _FrameCurve,
) -> float:
    q_ref = ref.q
    I_k = _interp_to(q_ref, other.q, other.I)
    s_ref = ref.sigma
    s_k = _interp_to(q_ref, other.q, other.sigma)
    mask = np.isfinite(I_k) & np.isfinite(s_ref) & np.isfinite(s_k) & (s_ref > 0) & (s_k > 0)
    if int(np.sum(mask)) < 2:
        return float("nan")
    sigma_pair = np.sqrt(s_ref[mask] ** 2 + s_k[mask] ** 2)
    return float(calc_chi2(ref.I[mask], I_k[mask], sigma_pair))


def _pairwise_cormap(ref: _FrameCurve, other: _FrameCurve) -> float:
    q_ref = ref.q
    I_k = _interp_to(q_ref, other.q, other.I)
    mask = np.isfinite(I_k) & np.isfinite(ref.I)
    if int(np.sum(mask)) < 2:
        return 1.0
    return _cormap_pvalue(ref.I[mask], I_k[mask])


def _common_prefix_stems(paths: List[str]) -> str:
    stems = [
        _strip_sub_int_prefix(os.path.splitext(os.path.basename(p))[0]) for p in paths
    ]
    if not stems:
        return "series"
    prefix = os.path.commonprefix(stems)
    prefix = prefix.rstrip("_-")
    if not prefix:
        # Fall back: strip trailing _digits from first stem
        m = re.sub(r"_\d+$", "", stems[0])
        prefix = m if m else stems[0]
    return _strip_sub_int_prefix(prefix)


def _warn_reason(chi2: float, cormap_p: float, *, chi2_min: float, cormap_p_min: float) -> str:
    reasons: List[str] = []
    if np.isfinite(chi2) and chi2 < chi2_min:
        reasons.append("low_chi2")
    if np.isfinite(cormap_p) and cormap_p < cormap_p_min:
        reasons.append("low_cormap_p")
    return ",".join(reasons)


def _select_frames(
    curves: List[_FrameCurve],
    *,
    chi2_max: float,
    chi2_min: float,
    cormap_p_min: float,
) -> Tuple[List[int], List[_FrameRow], str, List[str]]:
    """Return accepted indices, CSV rows, radiation_damage flag, warning messages."""
    n_total = len(curves)
    rows: List[_FrameRow] = []
    accepted: List[int] = [0]
    warnings: List[str] = []
    stopped = False

    rows.append(
        _FrameRow(
            frame_index=0,
            filename=os.path.basename(curves[0].path),
            chi2=None,
            cormap_p=None,
            status="reference",
            warn_reason="",
        )
    )

    for k in range(1, n_total):
        if stopped:
            rows.append(
                _FrameRow(
                    frame_index=k,
                    filename=os.path.basename(curves[k].path),
                    chi2=None,
                    cormap_p=None,
                    status="skipped",
                    warn_reason="",
                )
            )
            continue

        chi2 = _pairwise_chi2(curves[0], curves[k])
        cormap_p = _pairwise_cormap(curves[0], curves[k])

        if np.isfinite(chi2) and chi2 > chi2_max:
            rows.append(
                _FrameRow(
                    frame_index=k,
                    filename=os.path.basename(curves[k].path),
                    chi2=chi2,
                    cormap_p=cormap_p,
                    status="rejected",
                    warn_reason="",
                )
            )
            stopped = True
            for j in range(k + 1, n_total):
                rows.append(
                    _FrameRow(
                        frame_index=j,
                        filename=os.path.basename(curves[j].path),
                        chi2=None,
                        cormap_p=None,
                        status="skipped",
                        warn_reason="",
                    )
                )
            break

        warn = _warn_reason(chi2, cormap_p, chi2_min=chi2_min, cormap_p_min=cormap_p_min)
        status = "kept_warn" if warn else "kept"
        if warn:
            warnings.append(
                f"Frame {k} ({os.path.basename(curves[k].path)}): "
                f"borderline comparison vs reference (chi2={chi2:.4f}, cormap_p={cormap_p:.4g}; {warn})"
            )
        rows.append(
            _FrameRow(
                frame_index=k,
                filename=os.path.basename(curves[k].path),
                chi2=chi2,
                cormap_p=cormap_p,
                status=status,
                warn_reason=warn,
            )
        )
        accepted.append(k)

    if n_total > 1 and len(accepted) == 1:
        radiation_damage = "un-acceptable"
        warnings.append(_HIGH_DAMAGE_MSG)
    else:
        radiation_damage = "acceptable"

    return accepted, rows, radiation_damage, warnings


def _weighted_average(curves: List[_FrameCurve], indices: List[int]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    q_ref = curves[indices[0]].q
    weights_sum = np.zeros_like(q_ref, dtype=float)
    I_sum = np.zeros_like(q_ref, dtype=float)

    for idx in indices:
        c = curves[idx]
        I_i = _interp_to(q_ref, c.q, c.I)
        s_i = _interp_to(q_ref, c.q, c.sigma)
        mask = np.isfinite(I_i) & np.isfinite(s_i) & (s_i > 0)
        w = np.zeros_like(q_ref)
        w[mask] = 1.0 / (s_i[mask] ** 2)
        I_sum += w * np.where(mask, I_i, 0.0)
        weights_sum += w

    safe = weights_sum > 0
    I_avg = np.zeros_like(q_ref)
    I_avg[safe] = I_sum[safe] / weights_sum[safe]
    sigma_avg = np.zeros_like(q_ref)
    sigma_avg[safe] = np.sqrt(1.0 / weights_sum[safe])
    return q_ref, I_avg, sigma_avg


def _write_frame_selection_csv(path: str, rows: List[_FrameRow]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["frame_index", "filename", "chi2", "cormap_p", "status", "warn_reason"])
        for row in rows:
            w.writerow(
                [
                    row.frame_index,
                    row.filename,
                    "" if row.chi2 is None else f"{row.chi2:.6g}",
                    "" if row.cormap_p is None else f"{row.cormap_p:.6g}",
                    row.status,
                    row.warn_reason,
                ]
            )


def _csv_to_markdown_table(csv_path: str) -> str:
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        rows = list(reader)
    if not rows:
        return ""
    header = rows[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join("---" for _ in header) + " |",
    ]
    for r in rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines) + "\n"


def _expand_profiles(profiles: DatPathExpressionArg) -> List[str]:
    expr = coerce_dat_path_expression(profiles)
    paths = expand_files_from_unwrapped(expr.unwrap(), kind="1d_dat")
    for p in paths:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("average input files must have .dat extension")
    return sorted(paths)


@run_with_cache(
    path_keys_for_hash=["profiles"],
    kwargs_for_hash_keys=["cormap_p_min", "chi2_max", "chi2_min"],
    include_config_in_hash=False,
    warn_if_no_cache=True,
)
def _average_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    *,
    cormap_p_min: float = 0.05,
    chi2_max: float = 1.25,
    chi2_min: float = 0.9,
) -> Dict[str, str]:
    _ = config, use_cache
    profiles = input_paths.get("profiles")
    if isinstance(profiles, list):
        profile_list = list(profiles)
    elif isinstance(profiles, str):
        profile_list = [profiles]
    else:
        profile_list = []
    if not profile_list:
        raise ValueError("average requires input_paths['profiles']")

    os.makedirs(output_dir, exist_ok=True)
    curves = [_load_curve(p) for p in profile_list]
    prefix = _common_prefix_stems(profile_list)
    dest = os.path.abspath(os.path.join(output_dir, f"int_{prefix}.dat"))
    csv_path = os.path.abspath(os.path.join(output_dir, f"{prefix}_frame_selection.csv"))

    frames_total = len(curves)
    accepted, rows, radiation_damage, warn_msgs = _select_frames(
        curves,
        chi2_max=chi2_max,
        chi2_min=chi2_min,
        cormap_p_min=cormap_p_min,
    )
    last_accepted_frame = accepted[-1]
    averaged_paths = [curves[i].path for i in accepted]

    q, I, sigma = _weighted_average(curves, accepted)

    average_meta: Dict[str, Any] = {
        "frames_total": frames_total,
        "last_accepted_frame": last_accepted_frame,
        "radiation_damage": radiation_damage,
        "criteria": {
            "cormap_p_min": float(cormap_p_min),
            "chi2_max": float(chi2_max),
            "chi2_min": float(chi2_min),
        },
        "averaged_files": averaged_paths,
    }

    write_saxs(
        dest,
        q,
        I,
        sigma,
        metadata={
            "type": "averaged",
            "average": average_meta,
        },
    )
    _write_frame_selection_csv(csv_path, rows)

    if event_bus is not None:
        for msg in warn_msgs:
            event_bus.publish(EventType.MESSAGE, {"text": msg})

    from autosaxs.core.report_fragments import write_skill_report_fragments

    md_table = _csv_to_markdown_table(csv_path)
    md_body = (
        f"frames_total: {frames_total}\n\n"
        f"last_accepted_frame: {last_accepted_frame}\n\n"
        f"radiation_damage: {radiation_damage}\n\n"
        f"{md_table}"
    )
    write_skill_report_fragments(
        output_dir,
        prefix,
        "average",
        md_body,
        summary_references=[
            {
                "role": "averaged_curve",
                "path": os.path.basename(dest),
                "format": "saxs_dat",
            }
        ],
        summary_extra={
            "frames_total": frames_total,
            "last_accepted_frame": last_accepted_frame,
            "radiation_damage": radiation_damage,
        },
    )

    return {
        "averaged_1d": dest,
        "frame_selection_csv": csv_path,
    }


def average(
    profiles: DatPathExpressionArg,
    output_dir: str = "./averaged",
    *,
    cormap_p_min: float = 0.05,
    chi2_max: float = 1.25,
    chi2_min: float = 0.9,
    use_cache: bool = False,
) -> Dict[str, str]:
    """
    SAXS / small-angle x-ray scattering: radiation-damage-aware averaging of per-frame 1D SAXS curves.

    ### Arguments

    - `profiles` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive). Files are sorted lexicographically.
    - `output_dir` (str, default `./averaged`): Directory where the outputs are written.
    - `cormap_p_min` (float, default `0.05`): CorMap p-value threshold for borderline warnings.
    - `chi2_max` (float, default `1.25`): Reject frame (and stop) when reduced chi-squared vs reference exceeds this value.
    - `chi2_min` (float, default `0.9`): Warn when reduced chi-squared is below this value.
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    ### Short parameter list

    - cormap_p_min: internal parameter, recommended not to change, default: 0.05
    - chi2_max: internal parameter, recommended not to change, default: 1.25
    - chi2_min: internal parameter, recommended not to change, default: 0.9

    ### Returns

    dict[str, str] with:

    - `averaged_1d`: Path to the merged `int_<prefix>.dat` curve.
    - `frame_selection_csv`: Path to per-frame selection diagnostics CSV.

    ### Python usage

    ```python
    from autosaxs.skill import average

    out = average(
        profiles="integrated/exp_*.dat",
        output_dir="./averaged",
        use_cache=False,
    )
    print(out["averaged_1d"])
    ```

    ### CLI usage

    ```bash
    autosaxs average "integrated/exp_*.dat" --output-dir ./averaged
    ```
    """
    expanded = _expand_profiles(profiles)
    return _average_paths(
        input_paths={"profiles": expanded},
        output_dir=output_dir,
        event_bus=None,
        use_cache=use_cache,
        cormap_p_min=cormap_p_min,
        chi2_max=chi2_max,
        chi2_min=chi2_min,
    )
