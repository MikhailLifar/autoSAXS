"""Single polydisperse job-step DAG builder for auto TIFF and manual window runs."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ...core.models import RunRequest
from ..session.output_paths import fit_sizes_dir, guinier_poly_dir, mixture_dir
from ..session.state import LiveviewSessionState, PolydisperseMixtureMode
from .jobs import JobStep
from .monodisperse_pipeline import (
    FIT_GUINIER_POLY_STEP,
    coerce_opt_int,
    guinier_opts,
    profile_sample_stem,
)

YamlOptionsLoader = Callable[[Optional[Path]], dict]


class PolydispersePipelineParts(str, Enum):
    """Which polydisperse analysis steps to include."""

    GUINIER_ONLY = "guinier_only"
    SIZES_ONLY = "sizes_only"
    MIXTURE_ONLY = "mixture_only"
    FULL = "full"  # guinier + fit_sizes + optional mixture


def fit_sizes_opts(
    *,
    state: LiveviewSessionState,
    output_root: Path,
    load_yaml: YamlOptionsLoader,
) -> dict:
    outdir = fit_sizes_dir(output_root)
    outdir.mkdir(parents=True, exist_ok=True)
    opts: dict = {}
    if state.fit_sizes_conf_path is not None:
        opts.update(load_yaml(state.fit_sizes_conf_path))
    wp = state.polydisperse_window_params
    if isinstance(wp, dict):
        for key in ("first", "last", "rmin_nm", "rmax_nm", "alpha"):
            if wp.get(key) is not None:
                opts[key] = wp[key]
    opts["shape"] = "spheres"
    if coerce_opt_int(opts.get("first")) is None:
        opts["first"] = 1
    else:
        opts["first"] = int(coerce_opt_int(opts.get("first")) or 1)
    opts.pop("output_dir", None)
    opts.pop("use_cache", None)
    opts["output_dir"] = str(outdir.resolve())
    opts["use_cache"] = False
    return opts


def model_mixture_opts(
    *,
    state: LiveviewSessionState,
    output_root: Path,
) -> dict:
    outdir = mixture_dir(output_root)
    outdir.mkdir(parents=True, exist_ok=True)
    opts: Dict[str, Any] = {"output_dir": str(outdir.resolve()), "use_cache": False}
    raw = state.model_mixture_options
    if isinstance(raw, dict):
        skip = frozenset({"output_dir", "use_cache", "config_path"})
        for key, value in raw.items():
            if key in skip or value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            opts[str(key)] = value
    wp = state.polydisperse_window_params
    if isinstance(wp, dict):
        mix = wp.get("mixture")
        if isinstance(mix, dict):
            for key, value in mix.items():
                if value is None:
                    continue
                if isinstance(value, str) and not value.strip():
                    continue
                opts[str(key)] = value
    return opts


def job_includes_mixture(steps: List[JobStep]) -> bool:
    return any(s.name == "model_mixture" for s in steps)


def build_polydisperse_steps(
    profile_abs: str,
    *,
    output_root: Path,
    state: LiveviewSessionState,
    parts: PolydispersePipelineParts,
    load_yaml: YamlOptionsLoader,
    fixed_guinier_interval: bool = False,
    guinier_interval_first: Optional[int] = None,
    guinier_interval_last: Optional[int] = None,
) -> List[JobStep]:
    """
    Build polydisperse JobSteps for auto or manual runs.

    FULL includes mixture when ``state.polydisperse_mixture_mode`` is MIXTURE.
    Manual GUINIER_ONLY / SIZES_ONLY never append mixture.
    """
    prof = str(Path(profile_abs).expanduser().resolve())
    root = output_root.expanduser().resolve()
    steps: List[JobStep] = []

    if parts in (PolydispersePipelineParts.GUINIER_ONLY, PolydispersePipelineParts.FULL):
        g_opts = guinier_opts(
            state=state,
            output_root=root,
            profile_abs=prof,
            load_yaml=load_yaml,
            fixed_interval=fixed_guinier_interval,
            interval_first=guinier_interval_first,
            interval_last=guinier_interval_last,
            guinier_root=guinier_poly_dir(root),
            conf_path=state.fit_guinier_poly_conf_path,
            window_params=state.polydisperse_window_params,
        )
        # Prefer polydisperse window Guinier keys when present (separate from sizes first).
        wp = state.polydisperse_window_params
        if isinstance(wp, dict) and fixed_guinier_interval:
            gf = coerce_opt_int(wp.get("guinier_first"))
            gl = coerce_opt_int(wp.get("guinier_last"))
            if gf is not None and gl is not None and not (
                guinier_interval_first is not None and guinier_interval_last is not None
            ):
                g_opts["first"] = gf
                g_opts["last"] = gl
        steps.append(
            JobStep(
                name=FIT_GUINIER_POLY_STEP,
                request=RunRequest("fit_guinier", [prof], g_opts),
            )
        )

    if parts in (PolydispersePipelineParts.SIZES_ONLY, PolydispersePipelineParts.FULL):
        s_opts = fit_sizes_opts(state=state, output_root=root, load_yaml=load_yaml)
        steps.append(JobStep(name="fit_sizes", request=RunRequest("fit_sizes", [prof], s_opts)))

    if parts == PolydispersePipelineParts.MIXTURE_ONLY or (
        parts == PolydispersePipelineParts.FULL
        and state.polydisperse_mixture_mode == PolydisperseMixtureMode.MIXTURE
    ):
        m_opts = model_mixture_opts(state=state, output_root=root)
        steps.append(JobStep(name="model_mixture", request=RunRequest("model_mixture", [prof], m_opts)))

    return steps
