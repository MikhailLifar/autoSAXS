from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np

from autosaxs.core.utils import read_saxs, subtraction_correctness, write_saxs

from .deps import (
    EventBus,
    EventType,
    PLTViewer,
    _strip_sub_int_prefix,
    apply_batch,
    run_with_cache,
)


def _match_tail_scale_two_step(
    q_tail: np.ndarray,
    I_tail: np.ndarray,
    I_buff_tail: np.ndarray,
    n_min: int = 2,
    n_max: int = 6,
) -> float:
    if q_tail.size < 3:
        return 1.0
    coeffs_buff = np.polyfit(q_tail, I_buff_tail, 1)
    A_buff, B_buff = coeffs_buff[0], coeffs_buff[1]
    f_buff_tail = A_buff * q_tail + B_buff

    best_scale = 1.0
    best_rss = np.inf
    for n in range(n_min, int(n_max) + 1):
        if np.any(q_tail <= 0):
            continue
        q_inv_n = np.power(q_tail, -n)
        X = np.column_stack([q_inv_n, f_buff_tail])
        try:
            beta, residuals, _, _ = np.linalg.lstsq(X, I_tail, rcond=None)
            rss = float(np.sum(residuals ** 2)) if residuals.size else np.inf
            if rss < best_rss:
                best_rss = rss
                best_scale = float(beta[1])
        except (np.linalg.LinAlgError, IndexError, TypeError):
            continue
    return best_scale


def _normalize_scattering_form(form: str) -> str:
    key = str(form).strip().lower().replace("_", "-")
    if key == "linear":
        return "linear"
    if key == "porod":
        return "porod"
    if key in ("porod-plus-linear", "porod+linear"):
        return "porod-plus-linear"
    raise ValueError(
        f"Unknown scattering form {form!r}; expected 'linear', 'Porod', or 'Porod-plus-linear'"
    )


def _fit_intensity_at_q(
    q_fit: np.ndarray,
    I_fit: np.ndarray,
    form: str,
    n_min: int,
    n_max: int,
    q_eval: float,
) -> float:
    form_n = _normalize_scattering_form(form)
    q_fit = np.asarray(q_fit, dtype=float)
    I_fit = np.asarray(I_fit, dtype=float)
    if q_fit.size == 0:
        return float("nan")
    if q_eval <= 0 and form_n != "linear":
        return float("nan")

    if form_n == "linear":
        if q_fit.size < 2:
            return float(np.interp(q_eval, q_fit, I_fit)) if q_fit.size == 1 else float("nan")
        coeffs = np.polyfit(q_fit, I_fit, 1)
        return float(np.polyval(coeffs, q_eval))

    if np.any(q_fit <= 0):
        return float("nan")

    if form_n == "porod":
        if q_fit.size < 2:
            return float("nan")
        best_rss = np.inf
        best_val = float("nan")
        qe_n = float(q_eval)
        for n in range(int(n_min), int(n_max) + 1):
            col = np.power(q_fit, -n).reshape(-1, 1)
            try:
                beta, residuals, _, _ = np.linalg.lstsq(col, I_fit, rcond=None)
                rss = float(np.sum(residuals ** 2)) if residuals.size else 0.0
                if rss < best_rss:
                    best_rss = rss
                    A = float(beta[0])
                    best_val = A * (qe_n ** (-n))
            except (np.linalg.LinAlgError, IndexError, TypeError, FloatingPointError):
                continue
        return best_val

    if q_fit.size < 3:
        return float("nan")
    best_rss = np.inf
    best_val = float("nan")
    qe_n = float(q_eval)
    ones = np.ones_like(q_fit)
    for n in range(int(n_min), int(n_max) + 1):
        X = np.column_stack([np.power(q_fit, -n), q_fit, ones])
        try:
            beta, residuals, _, _ = np.linalg.lstsq(X, I_fit, rcond=None)
            rss = float(np.sum(residuals ** 2)) if residuals.size else 0.0
            if rss < best_rss:
                best_rss = rss
                A, B, C = float(beta[0]), float(beta[1]), float(beta[2])
                best_val = A * (qe_n ** (-n)) + B * qe_n + C
        except (np.linalg.LinAlgError, IndexError, TypeError, FloatingPointError):
            continue
    return best_val


def _minimal_ratio_scale(
    q: np.ndarray,
    I_sample: np.ndarray,
    I_buffer: np.ndarray,
    *,
    window_q_fraction: float = 0.025,
    approach_factor: float = 0.99,
    window_log_ratio_std_max: float = 1.0,
    q_min: Optional[float] = None,
    q_max: Optional[float] = None,
) -> float:
    """
    Non-parametric buffer scale (experimental; not exposed via ``subtract()``).

    Sliding q-range windows (fraction of total q span), median of I_sample/I_buffer
    per window, global min, × approach_factor. Windows with std(log(ratio)) above
    ``window_log_ratio_std_max`` are skipped (noisy low-intensity tails).
    """
    q = np.asarray(q, dtype=float)
    I_s = np.asarray(I_sample, dtype=float)
    I_b = np.asarray(I_buffer, dtype=float)
    frac = float(window_q_fraction)
    if not np.isfinite(frac) or frac <= 0.0:
        raise ValueError(f"minimal_ratio window_q_fraction must be finite and > 0, got {window_q_fraction!r}")

    if q_min is None and q_max is None:
        q_lo = float(np.min(q))
        q_hi = float(np.max(q))
        mask = np.ones(q.shape, dtype=bool)
    elif q_min is not None and q_max is not None:
        q_lo = float(q_min)
        q_hi = float(q_max)
        mask = (q >= q_lo) & (q <= q_hi)
    else:
        raise ValueError("minimal_ratio: q_min and q_max must both be set or both omitted")

    q_span = q_hi - q_lo
    if q_span <= 0.0:
        raise ValueError("minimal_ratio: q range must have positive span")
    win_dq = frac * q_span

    q_sel = q[mask]
    I_s_sel = I_s[mask]
    I_b_sel = I_b[mask]
    point_ok = (I_b_sel > 0.0) & (I_s_sel >= 0.0) & np.isfinite(I_s_sel) & np.isfinite(I_b_sel)
    q_sel = q_sel[point_ok]
    I_s_sel = I_s_sel[point_ok]
    I_b_sel = I_b_sel[point_ok]
    n = int(q_sel.size)
    if n < 2:
        raise ValueError(f"minimal_ratio requires at least 2 valid points in the q range, got {n}")

    ratios = I_s_sel / I_b_sel
    log_std_max = float(window_log_ratio_std_max)
    window_medians: List[float] = []
    for i in range(n):
        q_start = float(q_sel[i])
        in_win = (q_sel >= q_start) & (q_sel <= q_start + win_dq)
        if not np.any(in_win):
            continue
        win_ratios = ratios[in_win]
        pos = win_ratios > 0.0
        if np.count_nonzero(pos) < 2:
            continue
        log_r = np.log(win_ratios[pos])
        if float(np.std(log_r)) > log_std_max:
            continue
        window_medians.append(float(np.median(win_ratios)))
    if not window_medians:
        raise ValueError("minimal_ratio: no valid sliding windows in q range")
    return float(approach_factor) * min(window_medians)


def subtract_buffer(
    buffer_path,
    src_path,
    destpath,
    image_path=None,
    method="match_tail",
    match_tail_ops=None,
    scaling_factor: Optional[float] = None,
):
    q_buff, I_buff, sigma_buff, _ = read_saxs(buffer_path)
    q_buff_orig = np.asarray(q_buff, dtype=float)

    manual_scale: Optional[float] = None
    if scaling_factor is not None:
        try:
            manual_scale = float(scaling_factor)
        except (TypeError, ValueError):
            manual_scale = None
        if manual_scale is None or not np.isfinite(manual_scale) or manual_scale <= 0.0:
            raise ValueError(
                "subtract_buffer: scaling_factor must be a finite positive number when provided"
            )

    q, I, sigma, _ = read_saxs(src_path)
    scaling_factor = 1.00 if manual_scale is None else float(manual_scale)
    method_key = str(method).strip().lower().replace("-", "_")
    algo_ops = None
    # minimal_ratio branch kept for internal/experimental use via subtract_buffer only.
    if manual_scale is None and method_key in ("match_tail", "point_match", "minimal_ratio"):
        if method_key == "minimal_ratio":
            algo_ops = {
                "q_range_abs": None,
                "window_q_fraction": 0.025,
                "approach_factor": 0.99,
                "window_log_ratio_std_max": 1.0,
            }
        else:
            algo_ops = {
                "approach_factor": 1.00,
                "n_min": 2,
                "n_max": 6,
            }
        if match_tail_ops is None:
            match_tail_ops = dict()
        algo_ops.update(match_tail_ops)
        if algo_ops.get("q_range_rel") is not None:
            raise ValueError(
                "subtract_buffer: q_range_rel is no longer supported; use q_range_abs (q_min and q_max)"
            )

        if not np.array_equal(q, q_buff):
            I_buff = np.interp(q, q_buff, I_buff)

        if method_key == "minimal_ratio":
            q_range_abs = algo_ops.get("q_range_abs")
            if q_range_abs is None:
                q0 = max(float(np.min(q)), float(np.min(q_buff_orig)))
                q1 = min(float(np.max(q)), float(np.max(q_buff_orig)))
                algo_ops["q_range_abs"] = (q0, q1)
            else:
                q0, q1 = q_range_abs
            scaling_factor = _minimal_ratio_scale(
                q,
                I,
                I_buff,
                window_q_fraction=float(algo_ops.get("window_q_fraction", 0.025)),
                approach_factor=float(algo_ops.get("approach_factor", 0.99)),
                window_log_ratio_std_max=float(algo_ops.get("window_log_ratio_std_max", 1.0)),
                q_min=float(q0),
                q_max=float(q1),
            )
        else:
            q_range_abs = algo_ops.get("q_range_abs")
            if q_range_abs is None:
                raise ValueError(
                    f"subtract_buffer: q_range_abs is required for method {method_key!r} "
                    "(set q_min and q_max)"
                )
            q0, q1 = q_range_abs
            if q1 is None:
                q1 = float(np.max(q))
            idx = (q0 < q) & (q < q1)

            q_tail = q[idx]
            I_tail = I[idx]
            I_buff_tail = I_buff[idx]

            if method_key == "match_tail":
                scaling_factor = _match_tail_scale_two_step(
                    q_tail,
                    I_tail,
                    I_buff_tail,
                    n_min=int(algo_ops.get("n_min", 2)),
                    n_max=int(algo_ops.get("n_max", 6)),
                )
                scaling_factor *= algo_ops.get("approach_factor", 1.00)
            elif method_key == "point_match":
                sample_form = algo_ops.get("sample_form", "Porod-plus-linear")
                buffer_form = algo_ops.get("buffer_form", "linear")
                q_intersect = float(q1)
                pm_factor = float(algo_ops.get("point_match_factor", 0.995))
                n_lo = int(algo_ops.get("n_min", 2))
                n_hi = int(algo_ops.get("n_max", 6))
                I_s = _fit_intensity_at_q(q_tail, I_tail, sample_form, n_lo, n_hi, q_intersect)
                I_b = _fit_intensity_at_q(q_tail, I_buff_tail, buffer_form, n_lo, n_hi, q_intersect)
                if not np.isfinite(I_s) or not np.isfinite(I_b) or abs(I_b) < 1e-30 * max(1.0, abs(I_s)):
                    scaling_factor = 1.0
                else:
                    scaling_factor = pm_factor * I_s / I_b
                scaling_factor *= algo_ops.get("approach_factor", 1.00)

    I_buffer_scaled = I_buff * scaling_factor
    I_sub = I - I_buffer_scaled

    if sigma_buff is not None and sigma is not None:
        sigma_buffer_scaled = sigma_buff * scaling_factor
        sigma_sub = np.hypot(sigma, sigma_buffer_scaled)
    else:
        sigma_sub = None

    used_ops = None
    try:
        used_ops = dict(algo_ops) if isinstance(algo_ops, dict) else None
    except Exception:
        used_ops = None

    subtract_meta = {
        "method": method_key,
        "scaling_factor": float(scaling_factor),
        "manual_scaling_factor": bool(manual_scale is not None),
        "match_tail_ops": used_ops,
        "correctness": subtraction_correctness(I_sub, sigma_sub),
    }
    write_saxs(
        destpath,
        q,
        I_sub,
        sigma_sub,
        metadata={
            "type": "sub",
            "sample_path": src_path,
            "buffer_path": buffer_path,
            "subtract": subtract_meta,
        },
    )

    return q, I_sub, I_buffer_scaled, sigma_sub, sigma_buffer_scaled

from .common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    SingletonDatPathExpressionArg,
    coerce_dat_path_expression,
    coerce_singleton_dat_path_expression,
    expand_files_from_unwrapped,
)
from .config import merge_skill_params, resolve_optional_config_path


def _resolve_config_path(config_path: Optional[ConfigPathExpressionArg]) -> Optional[str]:
    return resolve_optional_config_path(config_path)


def subtract(
    sample_1d: DatPathExpressionArg,
    buffer_1d: SingletonDatPathExpressionArg,
    output_dir: str = ".",
    *,
    q_min: float,
    q_max: float,
    config_path: Optional[ConfigPathExpressionArg] = None,
    method: Optional[str] = None,
    sample_form: Optional[str] = None,
    buffer_form: Optional[str] = None,
    point_match_factor: Optional[float] = None,
    scaling_factor: Optional[float] = None,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    SAXS / small-angle x-ray scattering: subtract a buffer curve from a sample 1D profile (background subtraction). Scaling uses either `point_match` (default)
    or legacy `match_tail`, optionally restricted to a q window (`q_min` / `q_max`).

    ### Arguments

    - `sample_1d` (str): Sample path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `buffer_1d` (str): Path to the buffer 1D `.dat` curve (must be an existing file).
    - `output_dir` (str, default `.`): Directory where subtraction outputs are written.
    - `config_path` (str | None, default `None`): Optional path to a YAML config file with a `subtract` section. When omitted, bundled defaults apply for method/forms; q-window keys come from CLI or user file only.
    - `method` (str | None, default `None`): `point_match` or `match_tail`. Defaults from bundled config when omitted.
    - `q_min` (float): Lower bound of q-range (nm⁻¹). Required; may be overridden by a user config file `subtract` section.
    - `q_max` (float): Upper bound of q-range (nm⁻¹); for `point_match` the match uses this as q intersect (upper edge of the window). Required; may be overridden by a user config file `subtract` section.
    - `sample_form` / `buffer_form` (str | None): For `point_match` only — each is `linear`, `Porod`, or `Porod-plus-linear`.
    - `point_match_factor` (float | None, default `None`): For `point_match`, scale satisfies `point_match_factor * I_sample_fit(q_max) = scale * I_buffer_fit(q_max)`.
    - `scaling_factor` (float | None, default `None`): If provided, overrides automatic scaling and uses this factor directly (must be finite and > 0).
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    The q window (`q_min`, `q_max`) is always required at the Python API and CLI. A user config file may supply values that override the arguments passed to `subtract()`.

    ### Returns

    `dict[str, str]` with:

    - `subtracted_1d`: Path to the subtracted curve `.dat`.
    - `diff_plot_path`: Path to a diff plot PNG.
    - `diff_log_plot_path`: Path to a diff plot PNG with log(I) vs q.
    - `sub_plot_path`: Path to a subtracted curve plot PNG.

    Subtraction quality (`correct` or `over-subtracted`) is written into the subtracted `.dat` metadata
    (``subtract.correctness``) and into per-sample report fragments (individual Markdown and summary YAML).

    ### Python usage

    ```python
    from autosaxs.skill import subtract

    out = subtract(
        sample_1d="integration/int_sample_01.dat",
        buffer_1d="integration/int_buffer.dat",
        output_dir="subtracted",
        method="point_match",
        q_min=4.0,
        q_max=6.0,
        use_cache=False,
    )

    print(out["subtracted_1d"])
    ```

    ### CLI usage

    ```bash
    autosaxs subtract integration/int_sample_01.dat integration/int_buffer.dat \
      --output-dir subtracted --method point_match --q-min 4.0 --q-max 6.0
    ```
    """
    cfg_path = _resolve_config_path(config_path)
    merged = merge_skill_params(
        "subtract",
        config_path=cfg_path,
        method=method,
        q_min=q_min,
        q_max=q_max,
        sample_form=sample_form,
        buffer_form=buffer_form,
        point_match_factor=point_match_factor,
        scaling_factor=scaling_factor,
    )
    method_eff = str(merged.get("method", "point_match")).strip().lower().replace("-", "_")
    if method_eff == "minimal_ratio":
        raise ValueError(
            "subtract: method 'minimal_ratio' is not supported via the public API "
            "(experimental; reserved for future development). Use 'point_match' or 'match_tail'."
        )
    q_min_eff = merged.get("q_min", q_min)
    q_max_eff = merged.get("q_max", q_max)
    if q_min_eff is None or q_max_eff is None:
        raise ValueError("subtract: q_min and q_max must both be set (no defaults)")
    sample_form_eff = merged.get("sample_form", "Porod-plus-linear")
    buffer_form_eff = merged.get("buffer_form", "linear")
    point_match_factor_eff = float(merged.get("point_match_factor", 0.995))
    scaling_factor_eff = merged.get("scaling_factor", scaling_factor)
    match_tail_ops: Dict = {"q_range_abs": (float(q_min_eff), float(q_max_eff))}
    if method_eff == "point_match":
        match_tail_ops["sample_form"] = sample_form_eff
        match_tail_ops["buffer_form"] = buffer_form_eff
        match_tail_ops["point_match_factor"] = point_match_factor_eff
    match_tail_ops_out: Dict = match_tail_ops
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    sample_1d = coerce_dat_path_expression(sample_1d)
    buffer_1d = coerce_singleton_dat_path_expression(buffer_1d)
    buff = buffer_1d.unwrap()[0]
    if not buff or not os.path.isfile(buff):
        raise FileNotFoundError("subtract requires buffer_1d to be an existing file")
    expanded_samples = expand_files_from_unwrapped(sample_1d.unwrap(), kind="1d_dat")
    for p in expanded_samples:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("subtract input sample_1d files must have .dat extension")
    input_batch = [{"sample_1d": p, "buffer_1d": buff} for p in expanded_samples]
    return _subtract_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        method=method_eff,
        match_tail_ops=match_tail_ops_out,
        scaling_factor=scaling_factor_eff,
    )


@apply_batch(stem_from_keys="sample_1d", single_output_dir=True)
@run_with_cache(
    path_keys_for_hash=["sample_1d", "buffer_1d"],
    kwargs_for_hash_keys=["method", "match_tail_ops"],
    include_config_in_hash=False,
)
def _subtract_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
    method: str = "point_match",
    match_tail_ops: Optional[Dict] = None,  # required q_range_abs when called via subtract()
    scaling_factor: Optional[float] = None,
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
    sample_1d = input_paths.get("sample_1d")
    buffer_1d = input_paths.get("buffer_1d")
    if isinstance(sample_1d, list):
        sample_1d = sample_1d[0] if sample_1d else None
    if isinstance(buffer_1d, list):
        buffer_1d = buffer_1d[0] if buffer_1d else None
    if not sample_1d or not os.path.isfile(sample_1d):
        raise FileNotFoundError("subtract requires input_paths['sample_1d']")
    if not buffer_1d or not os.path.isfile(buffer_1d):
        raise FileNotFoundError("subtract requires input_paths['buffer_1d']")
    os.makedirs(output_dir, exist_ok=True)
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(sample_1d))[0])
    dest = os.path.join(output_dir, f"sub_{base}.dat")
    q, I_sub, I_buff_scaled, sigma_sub, sigma_buff_scaled = subtract_buffer(
        buffer_1d,
        sample_1d,
        dest,
        method=method,
        match_tail_ops=match_tail_ops,
        scaling_factor=scaling_factor,
    )
    q_sample, I_sample, sigma_sample, _ = read_saxs(sample_1d)
    diff_plot_path = os.path.join(output_dir, f"diff_{base}.png")
    diff_log_plot_path = os.path.join(output_dir, f"diff_log_{base}.png")
    sub_plot_path = os.path.join(output_dir, f"sub_{base}.png")
    PLTViewer.view_curves(
        q_sample,
        I_sample,
        "sample",
        q,
        I_buff_scaled,
        "buffer scaled",
        sigmas=(sigma_sample, sigma_buff_scaled),
        legend=True,
        plotFilePath=diff_plot_path,
        save=False,
    )
    I_sample_log = np.where(np.asarray(I_sample, dtype=float) > 0.0, np.log(np.asarray(I_sample, dtype=float)), np.nan)
    I_buff_log = np.where(np.asarray(I_buff_scaled, dtype=float) > 0.0, np.log(np.asarray(I_buff_scaled, dtype=float)), np.nan)
    PLTViewer.view_curves(
        q_sample,
        I_sample_log,
        "sample (log)",
        q,
        I_buff_log,
        "buffer scaled (log)",
        xlabel="q (nm-1)",
        ylabel="ln(I) (a.u.)",
        legend=True,
        plotFilePath=diff_log_plot_path,
        save=False,
    )
    PLTViewer.view_curves(
        q,
        I_sub,
        "sample",
        sigmas=(sigma_sub,),
        legend=True,
        plotFilePath=sub_plot_path,
        save=False,
    )
    from autosaxs.core.report_fragments import write_skill_report_fragments

    _, _, _sigma, meta = read_saxs(dest)
    subtract_meta = meta.get("subtract") if isinstance(meta, dict) else {}
    correctness = (
        str(subtract_meta.get("correctness"))
        if isinstance(subtract_meta, dict) and subtract_meta.get("correctness")
        else "correct"
    )
    md_lines = [
        "### Buffer subtraction\n",
        f"Scaling method: **{method}**.\n",
        f"Subtraction quality: **{correctness}**.\n",
        f"![Difference sample vs scaled buffer]({os.path.basename(diff_plot_path)})\n",
        f"![Difference log scale]({os.path.basename(diff_log_plot_path)})\n",
        f"![Subtracted curve]({os.path.basename(sub_plot_path)})\n",
    ]
    summary_refs = [
        {"role": "subtracted_curve", "path": os.path.basename(dest), "format": "saxs_dat", "display_name": "subtracted"},
    ]
    write_skill_report_fragments(
        output_dir,
        base,
        "subtract",
        "".join(md_lines),
        summary_references=summary_refs,
        summary_extra={"correctness": correctness},
    )
    return {
        "subtracted_1d": dest,
        "diff_plot_path": diff_plot_path,
        "diff_log_plot_path": diff_log_plot_path,
        "sub_plot_path": sub_plot_path,
    }

