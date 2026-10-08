"""
Meta-skill: polydisperse single-profile quality pipeline (Guinier onward).

Wires existing leaf skills only — no reimplementation of Guinier / GNOM / MIXTURE math.
Sequence: fit_guinier → fit_sizes → optional model_mixture (opt-in + quality gate) →
report_individual. Omits calibration, radiation averaging, buffer subtraction, and
monodisperse-only steps (Kratky / DATGNOM / DAMMIF).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from autosaxs.core.event_bus import EventBus, EventType
from autosaxs.core.utils import _strip_sub_int_prefix

from .common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    coerce_dat_path_expression,
    expand_files_from_unwrapped,
)
from .fit_guinier import fit_guinier, parse_guinier_results_txt
from .fit_sizes import fit_sizes
from .model_mixture import model_mixture
from .report_individual import report_individual
from .skill_wrap import require_atsas


def _as_single_path(value: Any) -> Optional[str]:
    if isinstance(value, list):
        return value[0] if value else None
    if isinstance(value, str) and value:
        return value
    return value if value else None


def _as_scalar(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _dr_allows_model_mixture(fit_sizes_out: Dict[str, Any]) -> bool:
    """
    Allow MIXTURE only when D(R) quality is internally consistent
    (``sizes_quality_class == high_quality`` / ``HIGH QUALITY``), analogous to
    ``process_monodisperse`` gating ``model_dam`` on p(r) high quality.
    """
    cls = str(_as_scalar(fit_sizes_out.get("sizes_quality_class")) or "").strip().lower()
    status = str(_as_scalar(fit_sizes_out.get("overall_status")) or "").strip().upper()
    if cls == "high_quality" or status == "HIGH QUALITY":
        return True
    return False


def _rmax_nm_from_fit_sizes_out(fit_sizes_out: Dict[str, Any]) -> Optional[float]:
    """Prefer compact handoff YAML ``fit.rmax_nm``; fall back to return-dict fields."""
    handoff = _as_single_path(fit_sizes_out.get("fit_sizes_path"))
    if handoff and os.path.isfile(handoff):
        try:
            with open(handoff, "r", encoding="utf-8") as fp:
                doc = yaml.safe_load(fp) or {}
        except OSError:
            doc = {}
        fit = doc.get("fit") if isinstance(doc, dict) else None
        if isinstance(fit, dict) and fit.get("rmax_nm") is not None:
            try:
                return float(fit["rmax_nm"])
            except (TypeError, ValueError):
                pass
        mm = doc.get("model_mixture") if isinstance(doc, dict) else None
        if isinstance(mm, dict) and mm.get("r_max_nm") is not None:
            try:
                return float(mm["r_max_nm"])
            except (TypeError, ValueError):
                pass
    for key in ("dmax_nm",):
        raw = _as_scalar(fit_sizes_out.get(key))
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


@require_atsas
def process_polydisperse(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    frames_dir: Optional[str] = None,
    config_path: Optional[ConfigPathExpressionArg] = None,
    first: Optional[int] = None,
    last: Optional[int] = None,
    shape: str = "spheres",
    q_min: Optional[float] = None,
    q_max: Optional[float] = None,
    run_mixture: bool = False,
    use_cache: bool = False,
) -> Dict[str, Any]:
    """
    SAXS / small-angle x-ray scattering: run the polydisperse single-profile quality pipeline
    (Guinier → GNOM D(R) / size-distribution passport → optional MIXTURE when requested and
    D(R) quality gates pass → per-sample PDF report).

    Accepts either subtracted ``.dat`` profiles (``profile``) **or** a TIFF frames
    directory (``frames_dir`` + YAML config) that runs calibrate → integrate →
    average-as-needed → subtract before this analysis chain. For a directory-only
    CLI entry that reads ``analysis: mono|poly`` from config, prefer
    ``process_directory``.

    ### Arguments

    - `profile` (str): 1D path expression (file/directory/glob of `*.dat`). Directories expand non-recursively.
      Ignored when `frames_dir` is set (still required by the CLI signature; pass any placeholder).
    - `output_dir` (str, default `.`): Pipeline root; leaf skills write under subdirectories here.
    - `frames_dir` (str | None, default `None`): Optional directory of TIFF frames. When set, run the
      YAML-driven TIFF front-end (`tiff_pipeline`) then analyze each subtracted curve. Config default:
      ``<frames_dir>/config.conf``.
    - `config_path` (str | None, default `None`): Optional YAML config forwarded to leaf skills
      (and to the TIFF front-end when `frames_dir` is set).
    - `first` / `last` (int | None): Optional fixed Guinier interval (1-based); both required together.
      Guinier `first` is forwarded to `fit_sizes` when set (or when auto-Guinier succeeds); Guinier
      `last` is **not** passed to GNOM D(R) (same narrow-window rule as mono DATGNOM).
    - `shape` (str, default `spheres`): Forwarded to `fit_sizes` (currently spheres).
    - `q_min` / `q_max` (float | None): Optional `fit_sizes` q bounds (nm⁻¹). When set, they override
      Guinier-first handoff for the corresponding end (do not combine `q_min` with an implicit first).
    - `run_mixture` (bool, default `False`): When `True`, run `model_mixture` if D(R) is high quality.
      Default stays off to match liveview auto / real-data light (MIXTURE is interactive Confirm in GUI).
    - `use_cache` (bool, default `False`): Forwarded to leaf skills.

    ### Returns

    `dict` with:

    - `report_pdf_path`: Primary PDF quality passport (when written).
    - `assembled_report_md_path`: Merged Markdown report.
    - `pipeline_dir`: The `output_dir` used as the pipeline root.
    - `basename`: Sample basename used for report assembly.
    - `model_mixture_ran`: Whether `model_mixture` was invoked.
    - `model_mixture_skip_reason`: Why MIXTURE was skipped (empty when run).
    - `fit_guinier`: Return dict from `fit_guinier`.
    - `fit_sizes`: Return dict from `fit_sizes`.
    - `model_mixture`: Return dict from `model_mixture` (empty dict when skipped).
    - `report_individual`: Return dict from `report_individual`.
    - `tiff_front` (only when `frames_dir` was set): TIFF front-end return dict.

    ### Python usage

    ```python
    from autosaxs.skill import process_polydisperse

    out = process_polydisperse(
        profile="subtracted/sub_sample_01.dat",
        output_dir="poly_out",
    )
    print(out["report_pdf_path"])

    # TIFF directory + config.conf (analysis chain forced to poly):
    out = process_polydisperse(".", output_dir="poly_out", frames_dir="/data/run01")
    ```

    ### CLI usage

    ```bash
    autosaxs process-polydisperse subtracted/sub_sample_01.dat --output-dir poly_out
    # TIFF directory → report (reads analysis from config):
    autosaxs process-directory /data/run01 --conf /data/run01/config.conf -o poly_out
    ```
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))

    tiff_front: Optional[Dict[str, Any]] = None
    if frames_dir:
        from .tiff_pipeline import run_tiff_to_subtracted

        if bus:
            bus.publish(EventType.MESSAGE, {"text": "process_polydisperse: TIFF front-end…"})
        tiff_front = run_tiff_to_subtracted(
            frames_dir,
            output_dir,
            config_path=config_path,
            use_cache=use_cache,
            event_bus=bus,
        )
        expanded = list(tiff_front["subtracted_paths"])
        config_path = tiff_front.get("config_path") or config_path
    else:
        profile_expr = coerce_dat_path_expression(profile)
        expanded = expand_files_from_unwrapped(profile_expr.unwrap(), kind="1d_dat")
        if not expanded:
            raise FileNotFoundError(f"process_polydisperse: no .dat profiles matched {profile!r}")
        for p in expanded:
            if Path(p).suffix.lower() != ".dat":
                raise ValueError("process_polydisperse input files must have .dat extension")

    if len(expanded) > 1:
        results: List[Dict[str, Any]] = []
        for p in expanded:
            stem = _strip_sub_int_prefix(Path(p).stem)
            sample_root = os.path.join(output_dir, stem)
            results.append(
                process_polydisperse(
                    p,
                    sample_root,
                    config_path=config_path,
                    first=first,
                    last=last,
                    shape=shape,
                    q_min=q_min,
                    q_max=q_max,
                    run_mixture=run_mixture,
                    use_cache=use_cache,
                )
            )
        out_multi: Dict[str, Any] = {
            "pipeline_dir": output_dir,
            "samples": results,
            "report_pdf_path": [r.get("report_pdf_path") for r in results],
        }
        if tiff_front is not None:
            out_multi["tiff_front"] = tiff_front
            out_multi["subtracted_paths"] = expanded
        return out_multi

    profile_path = expanded[0]
    basename = _strip_sub_int_prefix(Path(profile_path).stem)
    os.makedirs(output_dir, exist_ok=True)

    guinier_dir = os.path.join(output_dir, "fit_guinier")
    sizes_dir = os.path.join(output_dir, "fit_sizes")

    if bus:
        bus.publish(EventType.MESSAGE, {"text": "process_polydisperse: fit_guinier…"})
    out_guinier = fit_guinier(
        profile_path,
        guinier_dir,
        config_path=config_path,
        first=first,
        last=last,
        use_cache=use_cache,
    )
    handoff = parse_guinier_results_txt(_as_single_path(out_guinier.get("results_path")))

    sizes_kwargs: Dict[str, Any] = {
        "config_path": config_path,
        "shape": shape,
        "use_cache": use_cache,
    }
    if handoff.get("rg") is not None:
        sizes_kwargs["rg_nm"] = handoff["rg"]
    # q_min/q_max override Guinier-first; never pass Guinier last → GNOM D(R).
    if q_min is not None:
        sizes_kwargs["q_min"] = float(q_min)
    elif handoff.get("first_point_1based") is not None:
        sizes_kwargs["first"] = handoff["first_point_1based"]
    if q_max is not None:
        sizes_kwargs["q_max"] = float(q_max)

    if bus:
        bus.publish(EventType.MESSAGE, {"text": "process_polydisperse: fit_sizes…"})
    out_sizes = fit_sizes(profile_path, sizes_dir, **sizes_kwargs)

    out_mix: Dict[str, Any] = {}
    model_mixture_ran = False
    model_mixture_skip_reason = ""
    mix_dir = os.path.join(output_dir, "model_mixture")

    if not run_mixture:
        model_mixture_skip_reason = "run_mixture=False (default; matches liveview auto / real-data light)"
        if bus:
            bus.publish(
                EventType.MESSAGE,
                {"text": f"process_polydisperse: skipping model_mixture ({model_mixture_skip_reason})"},
            )
    elif not _dr_allows_model_mixture(out_sizes):
        status = _as_scalar(out_sizes.get("overall_status")) or "FAILED"
        cls = _as_scalar(out_sizes.get("sizes_quality_class")) or "failed"
        model_mixture_skip_reason = (
            f"D(R) quality gate not satisfied for MIXTURE "
            f"(overall_status={status!r}, sizes_quality_class={cls!r}; "
            f"require HIGH QUALITY)"
        )
        if bus:
            bus.publish(
                EventType.MESSAGE,
                {"text": f"process_polydisperse: skipping model_mixture ({model_mixture_skip_reason})"},
            )
    else:
        r_max_nm = _rmax_nm_from_fit_sizes_out(out_sizes)
        mix_kwargs: Dict[str, Any] = {
            "config_path": config_path,
            "use_cache": use_cache,
        }
        if r_max_nm is not None:
            mix_kwargs["r_max"] = float(r_max_nm)
        if q_min is not None:
            mix_kwargs["q_min_nm"] = float(q_min)
        if q_max is not None:
            mix_kwargs["q_max_nm"] = float(q_max)
        if bus:
            bus.publish(
                EventType.MESSAGE,
                {
                    "text": (
                        "process_polydisperse: model_mixture "
                        f"(quality gate passed, r_max={r_max_nm!r} nm)…"
                    )
                },
            )
        out_mix = model_mixture(profile_path, mix_dir, **mix_kwargs)
        model_mixture_ran = True

    if bus:
        bus.publish(EventType.MESSAGE, {"text": "process_polydisperse: report_individual…"})
    out_report = report_individual(
        output_dir,
        basename,
        output_dir=output_dir,
        config_path=config_path,
        output_path=os.path.join(output_dir, "reports", f"{basename}_report.pdf"),
        write_pdf=True,
        use_cache=use_cache,
    )

    out: Dict[str, Any] = {
        "pipeline_dir": output_dir,
        "basename": basename,
        "report_pdf_path": out_report.get("report_pdf_path"),
        "assembled_report_md_path": out_report.get("assembled_report_md_path"),
        "model_mixture_ran": model_mixture_ran,
        "model_mixture_skip_reason": model_mixture_skip_reason,
        "fit_guinier": out_guinier,
        "fit_sizes": out_sizes,
        "model_mixture": out_mix,
        "report_individual": out_report,
    }
    if tiff_front is not None:
        out["tiff_front"] = tiff_front
        out["subtracted_paths"] = expanded
    return out


# Allow `from autosaxs.skill import process_polydisperse` to return a callable even
# if Python resolves `process_polydisperse` as this *module* (not the function).
import types


class _CallableSkillModule(types.ModuleType):
    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[name-defined]
        fn = getattr(self, self.__name__.rsplit(".", 1)[-1])
        return fn(*args, **kwargs)


sys.modules[__name__].__class__ = _CallableSkillModule
