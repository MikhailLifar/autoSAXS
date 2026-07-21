"""fit_sizes skill: GNOM D(R) orchestration."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np

from autosaxs.core.gnom import distribution_arrays, parse_gnom_out
from autosaxs.core.gnom_quality import apply_sizes_extended_quality

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
from .artifacts import _finalize_fit_sizes_failure, write_success_artifacts
from .ensemble import run_rmax_ensemble
from .optimize import (
    _candidate_from_gnom_out,
    _guinier_from_profile,
    _optimize_rmax_nm,
    _q_to_first_point_1based,
)
from .parametric import classify_dr_parametric
from .quality_io import _assess_and_write_dr_quality, normalize_fit_sizes_single_sample
from .runners import _run_gnom_once, _shape_to_system


def fit_sizes(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
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
    stability_probe: bool = True,
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
    - `stability_probe` (bool, default `True`): When True, run a close-fit rmax ensemble (5 GNOM calls) plus one force-zero-off boundary probe (1 GNOM call) for stability hints and D(R) plot overlays.

    ### Returns

    `dict[str, str | list[str]]` with:

    - `output_subdir`: The per-sample output directory used for this profile.
    - `gnom_out_paths`: List of GNOM `.out` paths written for this profile (typically a single “best” `.out`).
    - `best_gnom_out_path`: Path to the selected “best” GNOM `.out`.
    - `fit_sizes_path`: Compact handoff YAML (`{base}_fit_sizes.yml`) — best fit, quality, analysis, and `model_mixture` hints.
    - `fit_sizes_log_path` / `best_summary_path`: Extended run log YAML (`{base}_fit_sizes_log.yml`) — candidates, ensemble, failures.
    - `fit_params_path` / `fit_sizes_hints_path` / `quality_passport_path`: Aliases of `fit_sizes_path` (backward compatibility).
    - `best_symlink_out_path`: Best-effort symlink path to the selected `.out` (may be missing on some filesystems).
    - `fit_vs_exp_png_path` / `fit_vs_exp_png_error`: Fit-vs-experiment plot output or error message.
    - `best_dr_png_path` / `best_dr_png_error`: \(D(R)\) plot output or error message.
    - `d_avg_nm` / `d_std_nm` / `pdi`: Mean size, standard deviation, and polydispersity index σ/⟨R⟩ from D(R).
    - `dr_peak_positions_nm` / `dr_n_peaks`: Peak positions and count in D(R).
    - `modality_class`: `monodisperse` \| `unimodal_polydisperse` \| `multimodal` \| `unknown`.
    - `modality_confidence`: `high` \| `low` when parametric and peak-based modality hints disagree.
    - `parametric_family` / `parametric_aic` / `n_components_suggested` / `mixture_dist_hint` / `parametric_peaks_nm`: Cheap post-hoc parametric hints on D(R).
    - `stability_class`: `stable` \| `marginal` \| `unstable` from close-fit ensemble and force-zero-off probe.
    - `ensemble_dir` / `ensemble_summary_path` / `close_fit_out_paths` / `force_zero_off_out_path`: Rmax stability probe artifacts (when `stability_probe=True`).
    - `rmax_validation`: Pathology block from force-zero-off D(R) tail analysis.
    - `rg_guinier_nm`: Guinier Rg (nm) when `fit_guinier` ran in-process.
    - `total_estimate`: GNOM Total Estimate of the selected fit.
    - `sizes_quality_class`: `high_quality` \| `acceptable` \| `failed`.
    - `overall_status`: `HIGH QUALITY` \| `ACCEPTABLE` \| `FAILED`.
    - `quality_rationale` / `user_tips`: Lists explaining the quality assessment.

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
    raw = _fit_sizes_paths(
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
        stability_probe=stability_probe,
    )
    if len(input_batch) == 1:
        return normalize_fit_sizes_single_sample(raw)
    return raw


@apply_batch(stem_from_keys="profile", per_sample_subdir="always")
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash=None,
    kwargs_for_hash_keys=["shape", "rg_nm", "rmin_nm", "rmax_nm", "rad56_nm", "first", "last", "alpha", "nr", "stability_probe"],
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
    stability_probe: bool = True,
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
        try:
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
        except RuntimeError as exc:
            if eval_tmp_path:
                try:
                    os.remove(eval_tmp_path)
                except OSError:
                    pass
            return _finalize_fit_sizes_failure(
                output_dir=output_dir,
                profile=profile,
                base=base,
                atsas_dat_path=atsas_dat_path,
                shape=shape,
                system=system,
                failure_reason="rmax_optimization_no_success",
                failures=failures,
                candidates=candidates,
                guinier_summary=guinier_summary,
                event_bus=event_bus,
                detail=str(exc),
                rg_guinier_nm=rg_guinier_nm_val,
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
        failures.append(
            {
                "shape": shape,
                "system": int(system),
                "rmax_nm": best_rmax_nm,
                "ok": False,
                "returncode": int(rc),
                "stderr": stderr,
            }
        )
        return _finalize_fit_sizes_failure(
            output_dir=output_dir,
            profile=profile,
            base=base,
            atsas_dat_path=atsas_dat_path,
            shape=shape,
            system=system,
            failure_reason="final_run_failed",
            failures=failures,
            candidates=candidates,
            guinier_summary=guinier_summary,
            event_bus=event_bus,
            detail=stderr,
            rg_guinier_nm=rg_guinier_nm_val,
        )
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

    dr_quality = _assess_and_write_dr_quality(
        output_dir=output_dir,
        base=base,
        out_text=out_text_final,
        atsas_fit_ok=True,
        rg_guinier_nm=rg_guinier_nm_val,
        shape=shape,
        neg_frac=best.get("neg_frac"),
        event_bus=event_bus,
        q_nm=q_nm,
        first_pt_1based=first_pt,
    )

    best_parsed = parse_gnom_out(out_text_final)
    parametric: Dict[str, Any] = {}
    arrays = distribution_arrays(best_parsed.get("distribution"))
    if arrays is not None:
        r_arr, d_arr, _err = arrays
        parametric = classify_dr_parametric(
            np.asarray(r_arr, dtype=float),
            np.asarray(d_arr, dtype=float),
            modality_class=str(dr_quality.get("modality_class") or "unknown"),
            dr_n_peaks=int(dr_quality.get("dr_n_peaks") or 0),
        )

    ensemble_info: Dict[str, Any] = {
        "ensemble_dir": "",
        "ensemble_summary_path": "",
        "close_fit_out_paths": [],
        "force_zero_off_out_path": "",
    }
    if stability_probe and np.isfinite(float(best_rmax_nm)) and float(best_rmax_nm) > 0:
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"GNOM (fit_sizes): Rmax ensemble around {float(best_rmax_nm):.4g} nm…"},
            )
        ensemble_info = run_rmax_ensemble(
            atsas_dat_path=atsas_dat_path,
            sample_output_dir=output_dir,
            best_rmax_nm=best_rmax_nm,
            system=system,
            shape=shape,
            rmin_nm=rmin_nm,
            rad56_nm=rad56_nm,
            first=first_pt,
            last=last_pt,
            alpha=alpha,
            nr=nr,
            best_parsed=best_parsed,
            event_bus=event_bus,
        )

    dr_quality = apply_sizes_extended_quality(
        dr_quality,
        parametric=parametric,
        ensemble_info=ensemble_info,
        shape=shape,
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
        shape=shape,
        system=system,
        best_gnom_out_path=best_gnom_out_path,
        gnom_out_paths=gnom_out_paths,
        best=best,
        candidates=candidates,
        failures=failures,
        guinier_summary=guinier_summary,
        rmax_trials=rmax_trials,
        dr_quality=dr_quality,
        user_rg_nm=user_rg_nm,
        user_first=user_first,
        user_rmax_nm=user_rmax_nm,
        best_rmax_nm=best_rmax_nm,
        first_pt=first_pt,
        last_pt=last_pt,
        rmin_nm=rmin_nm,
        rad56_nm=rad56_nm,
        alpha=alpha,
        nr=nr,
        ensemble_info=ensemble_info,
        event_bus=event_bus,
    )
