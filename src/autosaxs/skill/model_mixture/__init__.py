from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

import yaml

from autosaxs.core.event_bus import EventBus, EventType
from autosaxs.core.skill_progress import emit_skill_progress

from ..config import merge_skill_params, resolve_optional_config_path
from ..skill_wrap import apply_batch, require_atsas, run_with_cache
from ..common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    coerce_dat_path_expression,
    coerce_config_path_expression,
    expand_files_from_unwrapped,
)

NM_TO_ANGSTROM = 10.0
ANGSTROM_TO_NM = 0.1


def _nm_to_angstrom(value_nm: float) -> float:
    return float(value_nm) * NM_TO_ANGSTROM


def _angstrom_to_nm(value_ang: float) -> float:
    return float(value_ang) * ANGSTROM_TO_NM


def _resolve_config_path(config_path: Optional[ConfigPathExpressionArg]) -> Optional[str]:
    return resolve_optional_config_path(config_path)


def _q_range_from_merged(merged: Dict, q_min_nm: Optional[float], q_max_nm: Optional[float]) -> Optional[tuple]:
    if q_min_nm is not None or q_max_nm is not None:
        if q_min_nm is None and q_max_nm is not None:
            return (None, float(q_max_nm))
        if q_min_nm is not None and q_max_nm is not None:
            return (float(q_min_nm), float(q_max_nm))
        if q_min_nm is not None:
            return (float(q_min_nm), None)
    q_min_cfg = merged.get("q_min_nm")
    q_max_cfg = merged.get("q_max_nm")
    if q_min_cfg is None and q_max_cfg is None:
        return None
    q_min_v = None if q_min_cfg is None else float(q_min_cfg)
    q_max_v = None if q_max_cfg is None else float(q_max_cfg)
    if q_min_v is None and q_max_v is None:
        return None
    return (q_min_v, q_max_v)


def _read_fit_sizes_handoff(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fp:
        return yaml.safe_load(fp) or {}


def _rmax_nm_from_fit_sizes(
    profile: str,
    output_dir: str,
    event_bus: Optional[EventBus],
) -> tuple[float, str]:
    """Run fit_sizes in-process; return (rmax_nm, handoff_yaml_path)."""
    from ..fit_sizes import _fit_sizes_paths
    from ..fit_sizes.quality_io import normalize_fit_sizes_single_sample

    sizes_dir = os.path.join(output_dir, "_fit_sizes_defaults")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "model_mixture: running fit_sizes (in-process)…"})
    result = normalize_fit_sizes_single_sample(
        _fit_sizes_paths(
            input_paths={"profile": profile},
            output_dir=sizes_dir,
            shape="spheres",
            event_bus=event_bus,
            use_cache=False,
        )
    )
    handoff_path = str(result.get("fit_sizes_path") or result.get("fit_params_path") or "")
    if not handoff_path or not os.path.isfile(handoff_path):
        raise RuntimeError("model_mixture: fit_sizes did not write fit_sizes handoff YAML; cannot derive r_max.")
    doc = _read_fit_sizes_handoff(handoff_path)
    fit = doc.get("fit") or {}
    rmax_nm = fit.get("rmax_nm")
    if rmax_nm is None:
        rmax_nm = doc.get("rmax_nm")
    if rmax_nm is None:
        mm = doc.get("model_mixture") or {}
        rmax_nm = mm.get("r_max_nm")
    if rmax_nm is None:
        raise RuntimeError("model_mixture: fit_sizes handoff missing fit.rmax_nm; cannot derive r_max.")
    rmax_nm_f = float(rmax_nm)
    if rmax_nm_f <= 0:
        raise ValueError(f"model_mixture: invalid rmax_nm from fit_sizes: {rmax_nm_f}")
    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {"text": f"model_mixture: fit_sizes completed (rmax={rmax_nm_f:.4g} nm)."},
        )
    return rmax_nm_f, handoff_path


def _load_fit_sizes_hints(handoff_path: str) -> Optional[Dict[str, Any]]:
    if not handoff_path or not os.path.isfile(handoff_path):
        return None
    doc = _read_fit_sizes_handoff(handoff_path)
    mm = doc.get("model_mixture")
    if isinstance(mm, dict):
        return mm
    return doc


def _profile_stem(profile: str) -> str:
    from autosaxs.core.utils import _strip_sub_int_prefix

    return _strip_sub_int_prefix(Path(profile).stem)


def _discover_fit_sizes_handoff(profile: str, output_dir: str) -> Optional[str]:
    """
    Locate an existing fit_sizes handoff without re-running GNOM.

    Prefer mixture's own ``_fit_sizes_defaults`` scratch, then sibling ``fit_sizes/``
    under the analysis root (liveview layout).
    """
    stem = _profile_stem(profile)
    name = f"{stem}_fit_sizes.yml"
    out = Path(output_dir)
    candidates = [
        out / "_fit_sizes_defaults" / stem / name,
        out.parent / "fit_sizes" / stem / name,
        out.parent.parent / "fit_sizes" / stem / name,
    ]
    for path in candidates:
        if path.is_file():
            return str(path.resolve())
    return None


def coerce_float_sequence(raw: Any) -> Optional[List[float]]:
    """
    Parse component initial-guess sequences from CLI/API/YAML.

    Accepts a comma/semicolon-separated string (``"1.5,3.0"``), a list/tuple of
    numbers, or a single number. Returns ``None`` when empty / unparseable.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        parts = [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]
        values: List[float] = []
        for p in parts:
            try:
                values.append(float(p))
            except (TypeError, ValueError):
                continue
        return values or None
    if isinstance(raw, (int, float)):
        return [float(raw)]
    if isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        values = []
        for item in raw:
            try:
                values.append(float(item))
            except (TypeError, ValueError):
                continue
        return values or None
    return None


def _format_float_seq(values: Sequence[float]) -> str:
    return ",".join(f"{float(v):.6g}" for v in values)


def _init_radii_ang_from_hints(
    hints: Optional[Dict[str, Any]],
    *,
    r_min_ang: float,
    r_max_ang: float,
) -> Optional[List[float]]:
    """
    D(R) peak positions (nm) → MIXTURE Rout init guesses (Å).

    Keep only peaks strictly inside ``(r_min, r_max)`` — do not clip outliers onto
    the bounds (that piles starts at the wall and defeats multi-phase seeding).
    Cap to ``n_phases_suggested`` (≤3) when the peak list is an unfiltered local-maxima dump.
    """
    if not isinstance(hints, dict):
        return None
    peaks = hints.get("peak_positions_nm")
    if not isinstance(peaks, list) or not peaks:
        return None
    lo = float(r_min_ang) + 1.0
    hi = max(lo + 1.0, float(r_max_ang) - 1.0)
    out: List[float] = []
    for raw in peaks:
        try:
            nm = float(raw)
        except (TypeError, ValueError):
            continue
        if not (nm > 0):
            continue
        ang = _nm_to_angstrom(nm)
        if ang <= lo or ang >= hi:
            continue
        out.append(float(ang))
    if not out:
        return None
    n_cap = 3
    raw_n = hints.get("n_phases_suggested")
    try:
        if raw_n is not None:
            n_cap = max(1, min(3, int(raw_n)))
    except (TypeError, ValueError):
        pass
    if len(out) > n_cap:
        # Evenly keep n_cap representatives across the in-bounds peak ladder.
        import numpy as np

        idx = np.linspace(0, len(out) - 1, n_cap)
        out = [out[int(round(i))] for i in idx]
    return out


def _resolve_mixture_radius_params(
    *,
    profile: str,
    output_dir: str,
    event_bus: Optional[EventBus],
    user_r_min: Optional[float] = None,
    user_r_max: Optional[float] = None,
    user_poly_min: Optional[float] = None,
    user_poly_max: Optional[float] = None,
    user_init_radii_nm: Optional[Sequence[float]] = None,
    user_init_fractions: Optional[Sequence[float]] = None,
    user_init_poly_nm: Optional[Sequence[float]] = None,
) -> Dict[str, Any]:
    """
    Resolve MIXTURE radius bounds and optional component init guesses.

    Inputs/defaults in nm; returned ``r_*`` / ``poly_*`` in Å for ATSAS.
    Explicit ``user_init_*`` win over fit_sizes ``peak_positions_nm``.
    """
    sizes_handoff_path = ""
    if user_r_max is not None:
        r_max_nm = float(user_r_max)
        sizes_handoff_path = _discover_fit_sizes_handoff(profile, output_dir) or ""
    else:
        r_max_nm, sizes_handoff_path = _rmax_nm_from_fit_sizes(profile, output_dir, event_bus)

    hints = _load_fit_sizes_hints(sizes_handoff_path) if sizes_handoff_path else None

    if user_r_min is not None:
        r_min_nm = float(user_r_min)
    elif hints and hints.get("r_min_nm") is not None:
        r_min_nm = float(hints["r_min_nm"])
    else:
        r_min_nm = 0.1

    if user_poly_max is not None:
        poly_max_nm = float(user_poly_max)
    elif user_r_max is not None:
        # User overrode r_max: do not reuse handoff poly_max tied to another Rmax.
        poly_max_nm = 0.5 * r_max_nm
    elif hints and hints.get("poly_max_nm") is not None:
        poly_max_nm = float(hints["poly_max_nm"])
    else:
        poly_max_nm = 0.5 * r_max_nm

    if user_poly_min is not None:
        poly_min_nm = float(user_poly_min)
    elif user_r_max is not None:
        poly_min_nm = 0.05
    elif hints and hints.get("poly_min_nm") is not None:
        poly_min_nm = float(hints["poly_min_nm"])
    else:
        poly_min_nm = 0.05

    r_min_ang = _nm_to_angstrom(r_min_nm)
    r_max_ang = _nm_to_angstrom(r_max_nm)
    out: Dict[str, Any] = {
        "r_min": r_min_ang,
        "r_max": r_max_ang,
        "poly_min": _nm_to_angstrom(poly_min_nm),
        "poly_max": _nm_to_angstrom(poly_max_nm),
    }

    explicit_radii = coerce_float_sequence(user_init_radii_nm)
    if explicit_radii:
        out["init_radii_ang"] = [_nm_to_angstrom(v) for v in explicit_radii if float(v) > 0]
        if event_bus and out["init_radii_ang"]:
            peaks_nm = ", ".join(f"{v:.3g}" for v in explicit_radii if float(v) > 0)
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"model_mixture: using explicit init radii [{peaks_nm}] nm"},
            )
    else:
        init = _init_radii_ang_from_hints(hints, r_min_ang=r_min_ang, r_max_ang=r_max_ang)
        if init:
            out["init_radii_ang"] = init
            if event_bus:
                peaks_nm = ", ".join(f"{_angstrom_to_nm(v):.3g}" for v in init)
                event_bus.publish(
                    EventType.MESSAGE,
                    {"text": f"model_mixture: seeding phase radii from D(R) peaks [{peaks_nm}] nm"},
                )

    fractions = coerce_float_sequence(user_init_fractions)
    if fractions:
        out["init_fractions"] = fractions
    poly_nm = coerce_float_sequence(user_init_poly_nm)
    if poly_nm:
        out["init_poly_ang"] = [_nm_to_angstrom(v) for v in poly_nm if float(v) >= 0]
    return out


def model_mixture(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    q_min_nm: Optional[float] = None,
    q_max_nm: Optional[float] = None,
    maxit: Optional[int] = None,
    r_min: Optional[float] = None,
    r_max: Optional[float] = None,
    poly_min: Optional[float] = None,
    poly_max: Optional[float] = None,
    max_nph: Optional[int] = None,
    plot_I_q: Optional[bool] = None,
    plot_logI_logq: Optional[bool] = None,
    plot_logI_q: Optional[bool] = None,
    init_radii_nm: Optional[str] = None,
    init_fractions: Optional[str] = None,
    init_poly_nm: Optional[str] = None,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    SAXS / small-angle x-ray scattering: run MIXTURE fits on a 1D subtracted curve, select the best model by BIC, and write a comparison plot, size distribution plot, and results CSV.

    ### Arguments

    - `profile` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where the outputs are written.
    - `config_path` (str | None, default `None`): Deprecated. YAML/config with a `model_mixture` section. When omitted, bundled defaults apply.
    - `q_min_nm` / `q_max_nm` (float | None): Optional q bounds (nm^-1). Defaults to None.
    - `maxit` (int, default `100`): Number of optimization iterations. Defaults to 100.
    - `max_nph` (int, default `3`): Number of phases.
    - `plot_I_q` (bool, default `False`): Write I vs q fit comparison plot (labels show BIC).
    - `plot_logI_logq` (bool, default `False`): Write log I vs log q fit comparison plot (labels show BIC_log).
    - `plot_logI_q` (bool, default `True`): Write log I vs q fit comparison plot (labels show chi2).
    - `r_min` (float | None): Minimum radius (nm). Defaults to `0.1`. Converted to Å internally for ATSAS MIXTURE.
    - `r_max` (float | None): Maximum radius (nm). Defaults to `rmax_nm` from in-process `fit_sizes`.
    - `poly_min` (float | None): Minimum polydispersity (nm). Defaults to `0.05`.
    - `poly_max` (float | None): Maximum polydispersity (nm). Defaults to `0.5 × r_max`.
    - `init_radii_nm` (str | None): Comma-separated per-phase outer-radius starts in nm
      (ATSAS MIXTURE ``Rout``). Example: ``"2.5,5.0"``. Overrides D(R) peak seeding.
    - `init_fractions` (str | None): Comma-separated per-phase volume-fraction starts
      (normalized to sum 1). Example: ``"0.7,0.3"``.
    - `init_poly_nm` (str | None): Comma-separated per-phase polydispersity starts in nm.
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    Component init priority: explicit ``init_*`` → fit_sizes ``peak_positions_nm`` (radii
    only) → skill defaults. ATSAS MIXTURE also fixes inner radius/contrast, outer
    contrast, sticky=0, and derives hard-sphere radius from Rout (not user-settable).

    ### Short parameter list

    - q_min_nm: start of fit region
    - q_max_nm: end of fit region
    - maxit: number of optimization iterations, default: 100
    - r_min: size lower bound
    - r_max: size upper bound
    - poly_min: spread lower bound
    - poly_max: spread upper bound
    - init_radii_nm: comma-separated Rout starts (nm)
    - init_fractions: comma-separated volume fractions
    - init_poly_nm: comma-separated poly starts (nm)

    ### Returns

    `dict[str, str]` with:

    - `output_subdir`: The subdirectory that contains MIXTURE outputs.
    - `comparison_path`: Path to the I vs q comparison plot (empty when `plot_I_q=False`).
    - `comparison_loglog_path`: Path to the log I vs log q comparison plot (empty when `plot_logI_logq=False`).
    - `comparison_log_path`: Path to the log I vs q comparison plot (empty when `plot_logI_q=False`).
    - `distributions_path`: Path to the MIXTURE size distributions plot.
    - `results_csv_path`: Path to the MIXTURE results CSV.
    - `r_max_nm` / `poly_max_nm`: Resolved MIXTURE radius bounds (nm), including defaults when omitted.
    - `r_min_nm` / `poly_min_nm`: Resolved MIXTURE radius/polydispersity floors (nm).

    ### Python usage

    ```python
    from autosaxs.skill import model_mixture

    out = model_mixture(
        profile="subtracted/sub_sample_01.dat",
        output_dir="mixture",
        q_min_nm=0.8,
        q_max_nm=2.5,
        use_cache=False,
    )

    print(out["results_csv_path"])
    ```

    ### CLI usage

    ```bash
    autosaxs model-mixture subtracted/sub_sample_01.dat --output-dir mixture/ 
    autosaxs model-mixture subtracted/sub_sample_01.dat--q-min-nm 0.8 --q-max-nm 2.5 --r-max 10.0 -o mixture/
    ```
    """
    if q_max_nm is not None and q_min_nm is None:
        raise ValueError("model_mixture: q_min_nm must be set when q_max_nm is set")
    cfg_path = _resolve_config_path(config_path)
    merged = merge_skill_params(
        "model_mixture",
        config_path=cfg_path,
        q_min_nm=q_min_nm,
        q_max_nm=q_max_nm,
        maxit=maxit,
        r_min=r_min,
        r_max=r_max,
        poly_min=poly_min,
        poly_max=poly_max,
        max_nph=max_nph,
        plot_I_q=plot_I_q,
        plot_logI_logq=plot_logI_logq,
        plot_logI_q=plot_logI_q,
        init_radii_nm=init_radii_nm,
        init_fractions=init_fractions,
        init_poly_nm=init_poly_nm,
    )
    required = ("maxit", "max_nph")
    missing = [k for k in required if k not in merged or merged[k] is None]
    if missing:
        raise ValueError(f"model_mixture requires parameters (config or CLI): {missing}")
    q_range_nm = _q_range_from_merged(merged, q_min_nm, q_max_nm)
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    profile = coerce_dat_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("model_mixture input files must have .dat extension")
    input_batch = [{"profile": p} for p in expanded_profiles]
    mixture_param_overrides: Dict[str, Any] = {
        "maxit": int(merged["maxit"]),
        "max_nph": int(merged["max_nph"]),
        "plot_I_q": bool(merged.get("plot_I_q", False)),
        "plot_logI_logq": bool(merged.get("plot_logI_logq", False)),
        "plot_logI_q": bool(merged.get("plot_logI_q", True)),
    }
    for key in ("r_min", "r_max", "poly_min", "poly_max"):
        if key in merged and merged[key] is not None:
            mixture_param_overrides[key] = float(merged[key])
    # Explicit component starts: prefer call kwargs, else merged config.
    for key, call_val in (
        ("init_radii_nm", init_radii_nm),
        ("init_fractions", init_fractions),
        ("init_poly_nm", init_poly_nm),
    ):
        raw = call_val if call_val is not None else merged.get(key)
        seq = coerce_float_sequence(raw)
        if seq:
            mixture_param_overrides[key] = seq
    return _model_mixture_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        q_range_nm=q_range_nm,
        mixture_param_overrides=mixture_param_overrides,
    )


@apply_batch(stem_from_keys="profile", per_sample_subdir="always")
@require_atsas
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash_keys=["q_range_nm", "mixture_param_overrides"],
    include_config_in_hash=False,
    warn_if_no_cache=True,
)
def _model_mixture_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
    q_range_nm: Optional[tuple] = None,
    mixture_param_overrides: Optional[Dict] = None,
) -> Dict[str, Union[str, List[str]]]:
    """
    Run MIXTURE fits and select best by BIC; write comparison plot, distribution plot, results CSV.
    """
    from .mixture import fit_mixtures as _fit_mixtures

    _ = config, use_cache, sample_index
    profile = input_paths.get("profile")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("model_mixture requires input_paths['profile']")
    if not mixture_param_overrides:
        raise ValueError("model_mixture requires mixture_param_overrides")

    radius_params = _resolve_mixture_radius_params(
        profile=str(profile),
        output_dir=output_dir,
        event_bus=event_bus,
        user_r_min=mixture_param_overrides.get("r_min"),
        user_r_max=mixture_param_overrides.get("r_max"),
        user_poly_min=mixture_param_overrides.get("poly_min"),
        user_poly_max=mixture_param_overrides.get("poly_max"),
        user_init_radii_nm=mixture_param_overrides.get("init_radii_nm"),
        user_init_fractions=mixture_param_overrides.get("init_fractions"),
        user_init_poly_nm=mixture_param_overrides.get("init_poly_nm"),
    )
    init_radii_ang = radius_params.pop("init_radii_ang", None)
    init_fractions = radius_params.pop("init_fractions", None)
    init_poly_ang = radius_params.pop("init_poly_ang", None)
    mixture_params = {
        "maxit": int(mixture_param_overrides["maxit"]),
        "max_nph": int(mixture_param_overrides["max_nph"]),
        **radius_params,
    }

    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "MIXTURE fit…"})
    emit_skill_progress("model_mixture", "mixture")
    result = _fit_mixtures(
        profile,
        output_dir=output_dir,
        fast_forward=False,
        q_range_nm=q_range_nm,
        max_nph=mixture_params["max_nph"],
        maxit=mixture_params["maxit"],
        r_min=mixture_params["r_min"],
        r_max=mixture_params["r_max"],
        poly_min=mixture_params["poly_min"],
        poly_max=mixture_params["poly_max"],
        plot_I_q=bool(mixture_param_overrides.get("plot_I_q", False)),
        plot_logI_logq=bool(mixture_param_overrides.get("plot_logI_logq", False)),
        plot_logI_q=bool(mixture_param_overrides.get("plot_logI_q", True)),
        init_radii_ang=init_radii_ang,
        init_fractions=init_fractions,
        init_poly_ang=init_poly_ang,
    )
    if result is None:
        raise RuntimeError("model_mixture failed")
    emit_skill_progress("model_mixture", "finalizing")
    from autosaxs.core.report_fragments import write_skill_report_fragments
    from autosaxs.core.utils import _strip_sub_int_prefix as _strip_base

    obase = _strip_base(os.path.splitext(os.path.basename(profile))[0])
    dest_dir = str(result["output_subdir"])
    comp = _first_comparison_basename(result)
    dist = os.path.basename(result["distributions_path"])
    csvb = os.path.basename(result["results_csv_path"])
    md_parts = [
        "### MIXTURE (multi-phase spheres)\n",
        f"Best model: **{result.get('best_label', '')}**; BIC_log = {result.get('BIC_log')}\n",
    ]
    if comp:
        md_parts.append(f"![Comparison I(q)]({comp})\n")
    md_parts.append(f"![Size distributions]({dist})\n")
    summary_refs = [
        {
            "role": "mixture_scores_preview",
            "path": csvb,
            "format": "csv",
            "row": 0,
            "columns": ["label", "BIC_log", "n_phases"],
        },
    ]
    if comp:
        summary_refs.append({"role": "mixture_comparison_png", "path": comp, "format": "png"})
    write_skill_report_fragments(
        dest_dir,
        obase,
        "model_mixture",
        "".join(md_parts),
        summary_references=summary_refs,
    )
    run_params: Dict[str, Any] = {
        "skill": "model_mixture",
        "max_nph": int(mixture_params["max_nph"]),
        "maxit": int(mixture_params["maxit"]),
        "r_min_nm": _angstrom_to_nm(mixture_params["r_min"]),
        "r_max_nm": _angstrom_to_nm(mixture_params["r_max"]),
        "poly_min_nm": _angstrom_to_nm(mixture_params["poly_min"]),
        "poly_max_nm": _angstrom_to_nm(mixture_params["poly_max"]),
        "profile": str(profile),
    }
    # Persist explicit component starts in skill input units (nm / fractions).
    if init_radii_ang:
        run_params["init_radii_nm"] = _format_float_seq(
            [_angstrom_to_nm(v) for v in init_radii_ang]
        )
    if init_fractions:
        run_params["init_fractions"] = _format_float_seq(init_fractions)
    if init_poly_ang:
        run_params["init_poly_nm"] = _format_float_seq(
            [_angstrom_to_nm(v) for v in init_poly_ang]
        )
    run_params_path = os.path.join(dest_dir, "model_mixture_run_params.yml")
    with open(run_params_path, "w", encoding="utf-8") as fp:
        yaml.dump(run_params, fp, default_flow_style=False)
    out: Dict[str, Union[str, List[str]]] = {
        "output_subdir": result["output_subdir"],
        "comparison_path": result.get("comparison_path", ""),
        "comparison_loglog_path": result.get("comparison_loglog_path", ""),
        "comparison_log_path": result.get("comparison_log_path", ""),
        "distributions_path": result["distributions_path"],
        "results_csv_path": result["results_csv_path"],
        "best_label": result.get("best_label", ""),
        "r_min_nm": _angstrom_to_nm(mixture_params["r_min"]),
        "r_max_nm": _angstrom_to_nm(mixture_params["r_max"]),
        "poly_min_nm": _angstrom_to_nm(mixture_params["poly_min"]),
        "poly_max_nm": _angstrom_to_nm(mixture_params["poly_max"]),
    }
    if "init_radii_nm" in run_params:
        out["init_radii_nm"] = run_params["init_radii_nm"]
    if "init_fractions" in run_params:
        out["init_fractions"] = run_params["init_fractions"]
    if "init_poly_nm" in run_params:
        out["init_poly_nm"] = run_params["init_poly_nm"]
    return out


def _first_comparison_basename(result: Dict[str, Any]) -> str:
    for key in ("comparison_log_path", "comparison_path", "comparison_loglog_path"):
        path = str(result.get(key) or "").strip()
        if path:
            return os.path.basename(path)
    return ""
