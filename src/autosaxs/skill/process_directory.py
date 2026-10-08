"""
Meta-skill: YAML-driven TIFF directory → analysis PDF report(s).

Front-end (calibrate → integrate → average-as-needed → subtract) lives in
``tiff_pipeline``; analysis dispatches to ``process_monodisperse`` or
``process_polydisperse`` based on config ``analysis: mono|poly``.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, Optional

from autosaxs.core.event_bus import EventBus, EventType

from .common import ConfigPathExpressionArg
from .process_monodisperse import process_monodisperse
from .process_polydisperse import process_polydisperse
from .skill_wrap import require_atsas
from .tiff_pipeline import (
    load_tiff_pipeline_config,
    resolve_tiff_config_path,
    run_tiff_to_subtracted,
)


@require_atsas
def process_directory(
    frames_dir: str,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    use_cache: bool = False,
    n_runs: int = 5,
    run_mixture: bool = False,
) -> Dict[str, Any]:
    """
    SAXS / small-angle x-ray scattering: process a directory of TIFF frames to
    per-sample PDF quality reports using a small YAML pipeline config.

    Sequence: calibrate → integrate → average (only when multiple frames share a
    logical stem) → subtract → ``process_monodisperse`` or ``process_polydisperse``
    (chosen by config ``analysis``).

    ### Config (``config.conf`` / ``.yaml`` in the frames directory, or ``--conf``)

    ```yaml
    calibrant: "*AgBh*.tif"          # basename fnmatch glob (default)
    buffer_rules:                    # sample_glob → buffer_glob (fnmatch only)
      "*_sample*.tif": "*_buffer*.tif"
    analysis: mono                   # required: mono | poly
    # optional leaf overrides:
    # calibrate: { wavelength: 1.445 }
    # subtract: { q_min: 4.5, q_max: 5.5 }
    ```

    Matching is **fnmatch against basenames** (no regex). Calibrant: lexicographically
    first match. Buffer pairing reuses ``map_sample_files_to_buffer_files``; if that
    leaves unpaired samples and exactly one buffer exists, all samples share it.

    ### Arguments

    - `frames_dir` (str): Directory of TIFF frames (non-recursive ``*.tif`` / ``*.tiff``).
    - `output_dir` (str, default `.`): Pipeline root (`integrator/`, `averaged/`,
      `subtracted/`, per-sample analysis trees).
    - `config_path` (str | None, default `None`): YAML config; default
      ``<frames_dir>/config.conf``.
    - `use_cache` (bool, default `False`): Forwarded to leaf skills.
    - `n_runs` (int, default `5`): Forwarded to ``process_monodisperse`` (DAMMIF replicas).
    - `run_mixture` (bool, default `False`): Forwarded to ``process_polydisperse``.

    ### Returns

    `dict` with:

    - `report_pdf_path`: PDF path(s) from the analysis meta-skill.
    - `pipeline_dir`: The `output_dir` used as the pipeline root.
    - `frames_dir`: Absolute frames directory.
    - `config_path`: Resolved config path.
    - `analysis`: ``mono`` or ``poly``.
    - `subtracted_paths`: List of subtracted ``.dat`` paths.
    - `tiff_front`: Return dict from the TIFF front-end helper.
    - `analysis_out`: Return dict from ``process_monodisperse`` / ``process_polydisperse``.

    ### Python usage

    ```python
    from autosaxs.skill import process_directory

    out = process_directory("/data/run01", output_dir="/data/run01/out")
    print(out["report_pdf_path"])
    ```

    ### CLI usage

    ```bash
    autosaxs process-directory /data/run01
    autosaxs process-directory /data/run01 --conf /data/run01/config.conf -o /data/run01/out
    ```
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))

    frames_dir = os.path.abspath(os.path.expanduser(str(frames_dir)))
    if not os.path.isdir(frames_dir):
        raise NotADirectoryError(f"process_directory: frames_dir is not a directory: {frames_dir!r}")

    conf_path = resolve_tiff_config_path(frames_dir, config_path)
    cfg = load_tiff_pipeline_config(conf_path)
    if not cfg.analysis:
        raise ValueError(
            f"process_directory requires top-level analysis: mono|poly in {conf_path!r}"
        )

    os.makedirs(output_dir, exist_ok=True)
    if bus:
        bus.publish(
            EventType.MESSAGE,
            {"text": f"process_directory: TIFF front-end (analysis={cfg.analysis})…"},
        )
    front = run_tiff_to_subtracted(
        frames_dir,
        output_dir,
        config_path=conf_path,
        use_cache=use_cache,
        event_bus=bus,
    )
    subtracted_paths = list(front["subtracted_paths"])

    if bus:
        bus.publish(
            EventType.MESSAGE,
            {
                "text": (
                    f"process_directory: analysis ({cfg.analysis}) on "
                    f"{len(subtracted_paths)} subtracted profile(s)…"
                )
            },
        )

    if cfg.analysis == "mono":
        analysis_out = process_monodisperse(
            subtracted_paths,
            output_dir,
            config_path=conf_path,
            n_runs=n_runs,
            use_cache=use_cache,
        )
    else:
        analysis_out = process_polydisperse(
            subtracted_paths,
            output_dir,
            config_path=conf_path,
            run_mixture=run_mixture,
            use_cache=use_cache,
        )

    return {
        "pipeline_dir": output_dir,
        "frames_dir": frames_dir,
        "config_path": conf_path,
        "analysis": cfg.analysis,
        "subtracted_paths": subtracted_paths,
        "report_pdf_path": analysis_out.get("report_pdf_path"),
        "tiff_front": front,
        "analysis_out": analysis_out,
    }


# Allow `from autosaxs.skill import process_directory` to return a callable even
# if Python resolves `process_directory` as this *module* (not the function).
import types


class _CallableSkillModule(types.ModuleType):
    def __call__(self, *args: Any, **kwargs: Any) -> Any:  # type: ignore[name-defined]
        fn = getattr(self, self.__name__.rsplit(".", 1)[-1])
        return fn(*args, **kwargs)


sys.modules[__name__].__class__ = _CallableSkillModule
