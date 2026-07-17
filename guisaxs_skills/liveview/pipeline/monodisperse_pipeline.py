"""Single monodisperse job-step DAG builder for auto TIFF and manual wizard runs."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ...core.models import RunRequest
from ..session.output_paths import dammif_dir, fit_bodies_dir, fit_distances_dir, guinier_mono_dir
from ..session.state import (
    DEFAULT_LIVEVIEW_PRIMITIVE_BODIES_SHAPES,
    LiveviewSessionState,
    MonodisperseShapeMode,
)
from ..services.artifacts import discover_gnom_out_path
from .jobs import JobStep

YamlOptionsLoader = Callable[[Optional[Path]], dict]

# Job step name for monodisperse Guinier (poly uses fit_guinier_poly when both armed).
FIT_GUINIER_MONO_STEP = "fit_guinier"
FIT_GUINIER_POLY_STEP = "fit_guinier_poly"


class MonodispersePipelineParts(str, Enum):
    """Which monodisperse analysis steps to include."""

    GUINIER_AND_DISTANCES = "guinier_and_distances"
    DISTANCES_ONLY = "distances_only"
    SHAPE_ONLY = "shape_only"
    FULL = "full"  # guinier + distances + optional shape from session mode


def profile_sample_stem(profile_abs: str) -> str:
    stem = Path(profile_abs).stem
    for prefix in ("sub_", "int_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
    return stem


def coerce_opt_int(val: Any) -> Optional[int]:
    if val is None or isinstance(val, bool):
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def guinier_opts(
    *,
    state: LiveviewSessionState,
    output_root: Path,
    profile_abs: str,
    load_yaml: YamlOptionsLoader,
    fixed_interval: bool = False,
    interval_first: Optional[int] = None,
    interval_last: Optional[int] = None,
    guinier_root: Optional[Path] = None,
    conf_path: Optional[Path] = None,
    window_params: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    Build fit_guinier options.

    ``guinier_root`` defaults to ``guinier_mono/<stem>``. Poly chain passes ``guinier_poly_dir``.
    ``conf_path`` / ``window_params`` default to mono session fields when omitted.
    """
    stem = profile_sample_stem(profile_abs)
    root = guinier_root if guinier_root is not None else guinier_mono_dir(output_root)
    out_sub = root / stem
    out_sub.mkdir(parents=True, exist_ok=True)
    conf = conf_path if conf_path is not None else state.fit_guinier_mono_conf_path
    wp = window_params if window_params is not None else state.monodisperse_wizard_params
    opts: dict = {}
    if fixed_interval:
        first_i = coerce_opt_int(interval_first)
        last_i = coerce_opt_int(interval_last)
        if first_i is not None and last_i is not None:
            opts["first"] = first_i
            opts["last"] = last_i
        else:
            cand_first: Optional[int] = None
            cand_last: Optional[int] = None
            if conf is not None:
                conf_opts = load_yaml(conf)
                cand_first = coerce_opt_int(conf_opts.get("first"))
                cand_last = coerce_opt_int(conf_opts.get("last"))
            if isinstance(wp, dict):
                if cand_first is None:
                    cand_first = coerce_opt_int(wp.get("guinier_first"))
                    if cand_first is None:
                        cand_first = coerce_opt_int(wp.get("first"))
                if cand_last is None:
                    cand_last = coerce_opt_int(wp.get("guinier_last"))
                    if cand_last is None:
                        cand_last = coerce_opt_int(wp.get("last"))
            # Require both ends so a lone placeholder cannot become first=1,last=1.
            if cand_first is not None and cand_last is not None:
                opts["first"] = cand_first
                opts["last"] = cand_last
    opts.pop("output_dir", None)
    opts.pop("use_cache", None)
    opts["output_dir"] = str(out_sub.resolve())
    opts["use_cache"] = False
    return opts


def fit_distances_opts(
    *,
    state: LiveviewSessionState,
    output_root: Path,
    load_yaml: YamlOptionsLoader,
    guinier_handoff: Optional[dict] = None,
    use_guinier_placeholders: bool = False,
) -> dict:
    outdir = fit_distances_dir(output_root)
    outdir.mkdir(parents=True, exist_ok=True)
    opts: dict = {}
    if state.fit_distances_conf_path is not None:
        opts.update(load_yaml(state.fit_distances_conf_path))
    wp = state.monodisperse_wizard_params
    if isinstance(wp, dict):
        for key in ("rg_nm", "first", "last", "smooth"):
            if wp.get(key) is not None:
                opts[key] = wp[key]
    if isinstance(guinier_handoff, dict):
        if guinier_handoff.get("rg") is not None and opts.get("rg_nm") is None:
            opts["rg_nm"] = guinier_handoff["rg"]
        if guinier_handoff.get("first_point_1based") is not None and opts.get("first") is None:
            opts["first"] = guinier_handoff["first_point_1based"]
        # Never pass Guinier last → DATGNOM: the Guinier window is far too narrow for
        # p(r)/DAMMIF (ATSAS "insufficient data"). Omit --last so DATGNOM chooses it.
    if use_guinier_placeholders:
        # Same-job cascade: Rg/first from fit_guinier; never last (see above).
        for key in ("rg_nm", "first", "last"):
            opts.pop(key, None)
        opts["rg_nm"] = "${fit_guinier.rg}"
        opts["first"] = "${fit_guinier.first_point_1based}"
    opts.pop("output_dir", None)
    opts.pop("use_cache", None)
    opts["output_dir"] = str(outdir.resolve())
    opts["use_cache"] = False
    return opts


def shape_step(
    profile_abs: str,
    *,
    state: LiveviewSessionState,
    output_root: Path,
    shape_mode: str,
    gnom_out_path: Optional[str] = None,
    gnom_from_distances_placeholder: bool = False,
) -> Optional[JobStep]:
    prof = str(Path(profile_abs).expanduser().resolve())
    mode = MonodisperseShapeMode(str(shape_mode).lower()) if shape_mode else MonodisperseShapeMode.NONE
    if mode == MonodisperseShapeMode.DAMMIF:
        damdir = dammif_dir(output_root)
        damdir.mkdir(parents=True, exist_ok=True)
        opts: Dict[str, Any] = {
            "output_dir": str(damdir.resolve()),
            "use_cache": False,
        }
        if gnom_from_distances_placeholder:
            opts["gnom_path"] = "${fit_distances.best_gnom_out_path}"
        else:
            gnom_abs = discover_gnom_out_path(
                profile_abs=prof,
                output_root=output_root,
                watchdir=state.watchdir,
                hint=(gnom_out_path or "").strip(),
            )
            if gnom_abs:
                opts["gnom_path"] = gnom_abs
        return JobStep(
            name="fit_dammif",
            request=RunRequest("fit_dammif", [prof], opts),
        )
    if mode == MonodisperseShapeMode.BODIES:
        bodies_outdir = fit_bodies_dir(output_root)
        bodies_outdir.mkdir(parents=True, exist_ok=True)
        shapes = state.fit_bodies_shapes
        if not shapes:
            shapes = list(DEFAULT_LIVEVIEW_PRIMITIVE_BODIES_SHAPES)
        wp = state.monodisperse_wizard_params
        opts = {
            "output_dir": str(bodies_outdir.resolve()),
            "use_cache": False,
            "shapes": list(shapes),
        }
        if isinstance(wp, dict):
            for key in ("first", "last"):
                if wp.get(key) is not None:
                    opts[key] = wp[key]
        return JobStep(name="fit_bodies", request=RunRequest("fit_bodies", [prof], opts))
    return None


def job_includes_shape(steps: List[JobStep]) -> bool:
    return any(s.name in ("fit_dammif", "fit_bodies") for s in steps)


def build_monodisperse_steps(
    profile_abs: str,
    *,
    output_root: Path,
    state: LiveviewSessionState,
    parts: MonodispersePipelineParts,
    load_yaml: YamlOptionsLoader,
    guinier_handoff: Optional[dict] = None,
    fixed_guinier_interval: bool = False,
    guinier_interval_first: Optional[int] = None,
    guinier_interval_last: Optional[int] = None,
    gnom_out_path: Optional[str] = None,
) -> List[JobStep]:
    """
    Build monodisperse JobSteps for auto or manual runs.

    FULL includes shape when ``state.monodisperse_shape_mode`` is not NONE.
    Manual GUINIER_AND_DISTANCES / DISTANCES_ONLY never append shape.
    """
    prof = str(Path(profile_abs).expanduser().resolve())
    root = output_root.expanduser().resolve()
    steps: List[JobStep] = []

    if parts in (MonodispersePipelineParts.GUINIER_AND_DISTANCES, MonodispersePipelineParts.FULL):
        use_placeholders = guinier_handoff is None
        g_opts = guinier_opts(
            state=state,
            output_root=root,
            profile_abs=prof,
            load_yaml=load_yaml,
            fixed_interval=fixed_guinier_interval,
            interval_first=guinier_interval_first,
            interval_last=guinier_interval_last,
            guinier_root=guinier_mono_dir(root),
            conf_path=state.fit_guinier_mono_conf_path,
            window_params=state.monodisperse_wizard_params,
        )
        d_opts = fit_distances_opts(
            state=state,
            output_root=root,
            load_yaml=load_yaml,
            guinier_handoff=guinier_handoff,
            use_guinier_placeholders=use_placeholders,
        )
        steps.append(
            JobStep(
                name=FIT_GUINIER_MONO_STEP,
                request=RunRequest("fit_guinier", [prof], g_opts),
            )
        )
        steps.append(JobStep(name="fit_distances", request=RunRequest("fit_distances", [prof], d_opts)))

    if parts == MonodispersePipelineParts.DISTANCES_ONLY:
        d_opts = fit_distances_opts(
            state=state,
            output_root=root,
            load_yaml=load_yaml,
            guinier_handoff=guinier_handoff,
            use_guinier_placeholders=False,
        )
        steps.append(JobStep(name="fit_distances", request=RunRequest("fit_distances", [prof], d_opts)))

    want_shape = parts in (MonodispersePipelineParts.SHAPE_ONLY, MonodispersePipelineParts.FULL)
    if want_shape:
        mode = state.monodisperse_shape_mode
        if mode != MonodisperseShapeMode.NONE:
            chained = parts == MonodispersePipelineParts.FULL and any(
                s.name == "fit_distances" for s in steps
            )
            step = shape_step(
                prof,
                state=state,
                output_root=root,
                shape_mode=str(mode.value),
                gnom_out_path=gnom_out_path,
                gnom_from_distances_placeholder=chained and mode == MonodisperseShapeMode.DAMMIF,
            )
            if step is not None:
                steps.append(step)

    return steps
