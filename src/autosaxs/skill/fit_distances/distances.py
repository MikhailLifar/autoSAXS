"""fit_distances skill: DATGNOM p(r) orchestration."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np

from autosaxs.core.gnom import parse_gnom_out
from autosaxs.core.gnom_quality import analyze_dmax_validation

from ..common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    coerce_dat_path_expression,
    expand_files_from_unwrapped,
)
from ..deps import (
    EventBus,
    EventType,
    _strip_sub_int_prefix,
    apply_batch,
    ensure_q_nm,
    load_saxs_1d_any,
    run_with_cache,
    write_saxs_atsas_format,
)
from .artifacts import _finalize_fit_distances_failure, write_success_artifacts
from .optimize import (
    _candidate_from_out_text,
    _guinier_from_profile,
    _optimize_rg_nm,
    _q_to_first_point_1based,
)
from .quality_io import _assess_and_write_pr_quality
from .runners import _run_datgnom_once, _run_dmax_close_fit_ensemble

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
    - `fit_distances_log_path`: Path to the extended run log YAML (`{base}_fit_distances_log.yml`) — candidates, ensemble rows, quality, failures.
    - `fit_params_path`: Path to a YAML file containing the fit parameters used for the final run.
    - `best_symlink_out_path`: Best-effort symlink path to the selected `.out` (may be missing on some filesystems).
    - `fits_csv_path`: Path to a CSV containing candidate scores/metadata.
    - `fit_vs_exp_png_path` / `fit_vs_exp_png_error`: Fit-vs-experiment plot output or error message.
    - `best_pr_png_path` / `best_pr_png_error`: \(p(r)\) plot output or error message.
    - `ensemble_dir` / `ensemble_summary_path`: Close-fits Dmax ensemble directory and CSV summary.
    - `close_fit_out_paths`: Saved GNOM `.out` paths for Dmax±10% close fits.
    - `force_zero_off_out_path`: Saved GNOM `.out` with `--force-zero-rmax=N` at Dmax.
    - `dmax_nm`: Maximum real-space size D_max (nm) from the selected GNOM/DATGNOM fit.
    - `rg_pr_nm` / `i0_pr`: Integral Rg and I(0) from p(r) (GNOM-reported or computed from the distribution).
    - `rg_guinier_nm`: Guinier Rg (nm) from in-process `fit_guinier` or user `rg_nm`.
    - `q_min_fit_nm`: Low-q bound (nm⁻¹) used in the GNOM fit (from the `.out` angular range when available).
    - `total_estimate`: GNOM Total Estimate of the selected fit.
    - `delta_rg_pct`: \|Rg_Guinier − Rg_P(r)\| / Rg_Guinier × 100.
    - `shannon_s_min`, `shannon_class`, `shannon_ok`, `shannon_tip`: Shannon sampling metrics and interpretation guide.
    - `pr_quality_class`: `high_quality` \| `acceptable` \| `failed`.
    - `overall_status`: `HIGH QUALITY` \| `ACCEPTABLE` \| `FAILED` (quality passport label).
    - `quality_rationale` / `user_tips`: Lists explaining the quality assessment.
    - `quality_passport_path`: YAML path with the full quality block.

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

    guinier_summary: Optional[Dict[str, Any]] = None
    if guinier_info is not None:
        guinier_summary = {
            "rg": guinier_info.get("rg"),
            "rg_min": guinier_info.get("rg_min"),
            "rg_max": guinier_info.get("rg_max"),
            "q_min": guinier_info.get("q_min"),
            "q_max": guinier_info.get("q_max"),
            "chosen_interval": guinier_info.get("chosen_interval"),
            "quality_class": guinier_info.get("quality_class"),
        }

    rg_guinier_nm_val: Optional[float] = None
    if guinier_info is not None and guinier_info.get("rg") is not None:
        try:
            rg_guinier_nm_val = float(guinier_info["rg"])
        except (TypeError, ValueError):
            rg_guinier_nm_val = None

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
        try:
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
        except RuntimeError as exc:
            if eval_tmp_path:
                try:
                    os.remove(eval_tmp_path)
                except OSError:
                    pass
            return _finalize_fit_distances_failure(
                output_dir=output_dir,
                profile=profile,
                base=base,
                atsas_dat_path=atsas_dat_path,
                failure_reason="rg_optimization_no_success",
                failures=failures,
                candidates=candidates,
                guinier_summary=guinier_summary,
                event_bus=event_bus,
                detail=str(exc),
                q_nm=q_nm,
                first_pt=first_pt,
                rg_guinier_nm=rg_guinier_nm_val,
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
        if eval_tmp_path:
            try:
                os.remove(eval_tmp_path)
            except OSError:
                pass
        failures.append(
            {
                "rg_nm": float(rg_nm),
                "first": first_pt,
                "last": last_pt,
                "smooth": smooth_val,
                "ok": False,
                "returncode": int(rc),
                "stderr": stderr,
            }
        )
        return _finalize_fit_distances_failure(
            output_dir=output_dir,
            profile=profile,
            base=base,
            atsas_dat_path=atsas_dat_path,
            failure_reason="final_run_failed",
            failures=failures,
            candidates=candidates,
            guinier_summary=guinier_summary,
            event_bus=event_bus,
            detail=stderr,
            q_nm=q_nm,
            first_pt=first_pt,
            rg_guinier_nm=rg_guinier_nm_val,
        )
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

    # Close-fits Dmax ensemble + force-zero-off validation (saved artifacts).
    ensemble_info: Dict[str, Any] = {
        "ensemble_dir": "",
        "ensemble_summary_path": "",
        "close_fit_out_paths": [],
        "force_zero_off_out_path": "",
        "ensemble_rows": [],
        "force_zero_off_parsed": None,
    }
    dmax_validation: Optional[Dict[str, Any]] = None
    dmax_best = best.get("rmax_nm")
    try:
        dmax_best_f = float(dmax_best) if dmax_best is not None else float("nan")
    except (TypeError, ValueError):
        dmax_best_f = float("nan")
    if np.isfinite(dmax_best_f) and dmax_best_f > 0:
        best_parsed_for_alpha = parse_gnom_out(out_text)
        alpha_best = best_parsed_for_alpha.get("current_alpha")
        try:
            alpha_f = float(alpha_best) if alpha_best is not None else None
        except (TypeError, ValueError):
            alpha_f = None
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"DATGNOM (fit_distances): Dmax ensemble around {dmax_best_f:.4g} nm…"},
            )
        ensemble_info = _run_dmax_close_fit_ensemble(
            atsas_dat_path=atsas_dat_path,
            sample_output_dir=output_dir,
            dmax_nm=dmax_best_f,
            first=first_pt,
            last=last_pt,
            alpha=alpha_f,
            event_bus=event_bus,
        )
        dmax_validation = analyze_dmax_validation(
            best_parsed=best_parsed_for_alpha,
            ensemble_rows=[
                r for r in ensemble_info.get("ensemble_rows") or [] if r.get("role") == "close_fit"
            ],
            force_zero_off_parsed=ensemble_info.get("force_zero_off_parsed"),
            dmax_ref_nm=float(dmax_best_f),
        )
        ensemble_info["dmax_validation"] = dmax_validation

    pr_quality = _assess_and_write_pr_quality(
        output_dir=output_dir,
        base=base,
        out_text=out_text,
        atsas_fit_ok=True,
        rg_guinier_nm=rg_guinier_nm_val,
        q_nm=q_nm,
        first_pt=first_pt,
        suspicious=bool(best.get("suspicious")),
        event_bus=event_bus,
        dmax_validation=dmax_validation,
    )

    if eval_tmp_path:
        try:
            os.remove(eval_tmp_path)
        except OSError:
            pass

    return write_success_artifacts(
        profile=profile,
        base=base,
        output_dir=output_dir,
        atsas_dat_path=atsas_dat_path,
        best_gnom_out_path=best_gnom_out_path,
        gnom_out_paths=gnom_out_paths,
        out_text=out_text,
        best=best,
        candidates=candidates,
        failures=failures,
        guinier_summary=guinier_summary,
        rg_trials=rg_trials,
        pr_quality=pr_quality,
        ensemble_info=ensemble_info,
        user_rg_nm=user_rg_nm,
        user_first=user_first,
        user_last=user_last,
        user_smooth=user_smooth,
        event_bus=event_bus,
    )

