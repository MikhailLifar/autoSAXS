"""
Meta-skill: monodisperse single-profile quality pipeline (Guinier onward).

Wires existing leaf skills only — no reimplementation of Guinier / Kratky / GNOM / DAMMIF math.
Sequence follows ``docs/saxs_quality_guide_newest.docx`` steps 4–7 and 9
(omit calibration, radiation averaging, buffer subtraction, and polydisperse step 8).
Optional ``model_dam`` when step-7 quality gates pass (Total Estimate ≥ 0.55, ΔRg ≤ 10%).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from autosaxs.core.event_bus import EventBus, EventType
from autosaxs.core.utils import _strip_sub_int_prefix

from .analyze_kratky import analyze_kratky
from .common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    coerce_dat_path_expression,
    expand_files_from_unwrapped,
)
from .fit_distances import fit_distances
from .fit_guinier import fit_guinier, parse_guinier_results_txt
from .model_dam import model_dam
from .report_individual import report_individual
from .skill_wrap import require_atsas

# Quality-guide gate for 3D (step 7): ΔRg ≤ 10% and Total Estimate ≥ 0.55
# → encoded as fit_distances pr_quality_class ``high_quality``.
_DEFAULT_MODEL_DAM_N_RUNS = 5


def _as_single_path(value: Any) -> Optional[str]:
    if isinstance(value, list):
        return value[0] if value else None
    if isinstance(value, str) and value:
        return value
    return None


def _as_scalar(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _pr_allows_model_dam(fit_distances_out: Dict[str, Any]) -> bool:
    """
    Quality guide step 7: allow ab initio 3D only when p(r) is internally consistent
    (Total Estimate ≥ 0.55 and ΔRg ≤ 10%). That maps to ``pr_quality_class == high_quality``.
    """
    cls = str(_as_scalar(fit_distances_out.get("pr_quality_class")) or "").strip().lower()
    status = str(_as_scalar(fit_distances_out.get("overall_status")) or "").strip().upper()
    if cls == "high_quality" or status == "HIGH QUALITY":
        return True
    return False


def _gnom_out_for_model_dam(
    fit_distances_out: Dict[str, Any],
    *,
    dam_dir: str,
    basename: str,
) -> Optional[str]:
    """
    Place a real GNOM ``.out`` named ``{basename}.out`` for DAMMIF.

    ``model_dam`` names report fragments from the GNOM file stem; path expressions
    also ``resolve()`` symlinks, so a copy (not a symlink) keeps the sample basename
    and ensures ``report_individual`` discovers the DAMMIF fragment.
    """
    import shutil

    src = _as_single_path(fit_distances_out.get("best_gnom_out_path"))
    if not src or not os.path.isfile(src):
        return None
    os.makedirs(dam_dir, exist_ok=True)
    dest = os.path.join(dam_dir, f"{basename}.out")
    src_abs = os.path.normpath(os.path.abspath(src))
    dest_abs = os.path.normpath(os.path.abspath(dest))
    if src_abs != dest_abs:
        shutil.copy2(src_abs, dest_abs)
    return dest_abs


@require_atsas
def process_monodisperse(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    first: Optional[int] = None,
    last: Optional[int] = None,
    smooth: Optional[float] = None,
    n_runs: int = _DEFAULT_MODEL_DAM_N_RUNS,
    use_cache: bool = False,
) -> Dict[str, Any]:
    """
    SAXS / small-angle x-ray scattering: run the monodisperse single-profile quality pipeline
    (Guinier → dimensionless Kratky → DATGNOM p(r) / Shannon–ΔRg passport → optional DAMMIF
    when quality gates pass → per-sample PDF report).

    This is a **meta-skill**: it only calls existing leaf skills (`fit_guinier`, `analyze_kratky`,
    `fit_distances`, `model_dam`, `report_individual`) and wires outputs between them.
    It does **not** change leaf interiors. Steps before Guinier (geometry, averaging, buffer
    subtraction) and polydisperse sizing are omitted — input must already be a subtracted
    (or otherwise ready) 1D profile.

    ``model_dam`` runs only when `fit_distances` reports ``high_quality`` / ``HIGH QUALITY``
    (quality guide: Total Estimate ≥ 0.55 and ΔRg ≤ 10%). Default ``n_runs=5``.

    Primary result: the assembled PDF under ``<output_dir>/reports/`` (includes DAMMIF
    fragments when generated).

    ### Arguments

    - `profile` (str): 1D path expression (file/dir/glob of `*.dat`). Directories expand non-recursively.
    - `output_dir` (str, default `.`): Pipeline root; leaf skills write under subdirectories here.
    - `config_path` (str | None, default `None`): Optional YAML config forwarded to leaf skills.
    - `first` / `last` (int | None): Optional fixed Guinier interval (1-based); both required together.
      Guinier `first` is forwarded to DATGNOM; Guinier `last` is **not** passed to DATGNOM
      (window too narrow for p(r)).
    - `smooth` (float | None, default `None`): Optional DATGNOM `--smooth` for `fit_distances`.
    - `n_runs` (int, default `5`): DAMMIF replica count for `model_dam` when the quality gate passes.
    - `use_cache` (bool, default `False`): Forwarded to leaf skills.

    ### Returns

    `dict` with:

    - `report_pdf_path`: Primary PDF quality passport (when written).
    - `assembled_report_md_path`: Merged Markdown report.
    - `pipeline_dir`: The `output_dir` used as the pipeline root.
    - `basename`: Sample basename used for report assembly.
    - `model_dam_ran`: Whether `model_dam` was invoked.
    - `model_dam_skip_reason`: Why DAMMIF was skipped (empty when run).
    - `fit_guinier`: Return dict from `fit_guinier`.
    - `analyze_kratky`: Return dict from `analyze_kratky`.
    - `fit_distances`: Return dict from `fit_distances`.
    - `model_dam`: Return dict from `model_dam` (empty dict when skipped).
    - `report_individual`: Return dict from `report_individual`.

    ### Python usage

    ```python
    from autosaxs.skill import process_monodisperse

    out = process_monodisperse(
        profile="subtracted/sub_sample_01.dat",
        output_dir="mono_out",
    )
    print(out["report_pdf_path"])
    ```

    ### CLI usage

    ```bash
    autosaxs process-monodisperse subtracted/sub_sample_01.dat --output-dir mono_out
    ```
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))

    profile_expr = coerce_dat_path_expression(profile)
    expanded = expand_files_from_unwrapped(profile_expr.unwrap(), kind="1d_dat")
    if not expanded:
        raise FileNotFoundError(f"process_monodisperse: no .dat profiles matched {profile!r}")
    for p in expanded:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("process_monodisperse input files must have .dat extension")

    if len(expanded) > 1:
        results: List[Dict[str, Any]] = []
        for p in expanded:
            stem = _strip_sub_int_prefix(Path(p).stem)
            sample_root = os.path.join(output_dir, stem)
            results.append(
                process_monodisperse(
                    p,
                    sample_root,
                    config_path=config_path,
                    first=first,
                    last=last,
                    smooth=smooth,
                    n_runs=n_runs,
                    use_cache=use_cache,
                )
            )
        return {
            "pipeline_dir": output_dir,
            "samples": results,
            "report_pdf_path": [r.get("report_pdf_path") for r in results],
        }

    profile_path = expanded[0]
    basename = _strip_sub_int_prefix(Path(profile_path).stem)
    os.makedirs(output_dir, exist_ok=True)

    guinier_dir = os.path.join(output_dir, "fit_guinier")
    kratky_dir = os.path.join(output_dir, "analyze_kratky")
    distances_dir = os.path.join(output_dir, "fit_distances")

    if bus:
        bus.publish(EventType.MESSAGE, {"text": "process_monodisperse: fit_guinier…"})
    out_guinier = fit_guinier(
        profile_path,
        guinier_dir,
        config_path=config_path,
        first=first,
        last=last,
        use_cache=use_cache,
    )
    handoff = parse_guinier_results_txt(_as_single_path(out_guinier.get("results_path")))

    kratky_kwargs: Dict[str, Any] = {
        "config_path": config_path,
        "use_cache": use_cache,
    }
    if handoff.get("rg") is not None:
        kratky_kwargs["rg_nm"] = handoff["rg"]
    if handoff.get("i0") is not None:
        kratky_kwargs["i0"] = handoff["i0"]

    if bus:
        bus.publish(EventType.MESSAGE, {"text": "process_monodisperse: analyze_kratky…"})
    out_kratky = analyze_kratky(profile_path, kratky_dir, **kratky_kwargs)

    dist_kwargs: Dict[str, Any] = {
        "config_path": config_path,
        "smooth": smooth,
        "use_cache": use_cache,
    }
    if handoff.get("rg") is not None:
        dist_kwargs["rg_nm"] = handoff["rg"]
    if handoff.get("first_point_1based") is not None:
        dist_kwargs["first"] = handoff["first_point_1based"]
    # Never pass Guinier last → DATGNOM (same rule as liveview monodisperse wiring).

    if bus:
        bus.publish(EventType.MESSAGE, {"text": "process_monodisperse: fit_distances…"})
    out_distances = fit_distances(profile_path, distances_dir, **dist_kwargs)

    out_dam: Dict[str, Any] = {}
    model_dam_ran = False
    model_dam_skip_reason = ""
    dam_dir = os.path.join(output_dir, "model_dam")
    if _pr_allows_model_dam(out_distances):
        gnom_for_dam = _gnom_out_for_model_dam(
            out_distances, dam_dir=dam_dir, basename=basename
        )
        if gnom_for_dam is None:
            model_dam_skip_reason = "fit_distances did not produce a usable GNOM .out"
            if bus:
                bus.publish(
                    EventType.MESSAGE,
                    {"text": f"process_monodisperse: skipping model_dam ({model_dam_skip_reason})"},
                )
        else:
            if bus:
                bus.publish(
                    EventType.MESSAGE,
                    {
                        "text": (
                            f"process_monodisperse: model_dam "
                            f"(n_runs={int(n_runs)}, quality gate passed)…"
                        )
                    },
                )
            out_dam = model_dam(
                profile_path,
                dam_dir,
                config_path=config_path,
                gnom_path=gnom_for_dam,
                n_runs=int(n_runs),
                use_cache=use_cache,
            )
            model_dam_ran = True
    else:
        status = _as_scalar(out_distances.get("overall_status")) or "FAILED"
        cls = _as_scalar(out_distances.get("pr_quality_class")) or "failed"
        model_dam_skip_reason = (
            f"p(r) quality gate not satisfied for 3D modeling "
            f"(overall_status={status!r}, pr_quality_class={cls!r}; "
            f"require HIGH QUALITY / Total Estimate ≥ 0.55 and ΔRg ≤ 10%)"
        )
        if bus:
            bus.publish(
                EventType.MESSAGE,
                {"text": f"process_monodisperse: skipping model_dam ({model_dam_skip_reason})"},
            )

    if bus:
        bus.publish(EventType.MESSAGE, {"text": "process_monodisperse: report_individual…"})
    out_report = report_individual(
        output_dir,
        basename,
        output_dir=output_dir,
        config_path=config_path,
        output_path=os.path.join(output_dir, "reports", f"{basename}_report.pdf"),
        write_pdf=True,
        use_cache=use_cache,
    )

    return {
        "pipeline_dir": output_dir,
        "basename": basename,
        "report_pdf_path": out_report.get("report_pdf_path"),
        "assembled_report_md_path": out_report.get("assembled_report_md_path"),
        "model_dam_ran": model_dam_ran,
        "model_dam_skip_reason": model_dam_skip_reason,
        "fit_guinier": out_guinier,
        "analyze_kratky": out_kratky,
        "fit_distances": out_distances,
        "model_dam": out_dam,
        "report_individual": out_report,
    }
