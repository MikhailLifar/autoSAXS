from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from ..deps import (
    EventBus,
    EventType,
    _strip_sub_int_prefix,
    apply_batch,
    ensure_q_nm,
    load_saxs_1d_any,
    parse_gnom_out,
    run_with_cache,
    write_saxs_atsas_format,
)
from ..common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    SingletonPathExpressionArg,
    coerce_dat_path_expression,
    coerce_singleton_path_expression,
    expand_files_from_unwrapped,
)

_PROTOCOL_MODES = ("pilot", "average", "refined")
_DENSS_MODES = {"slow": "SLOW", "fast": "FAST", "membrane": "MEMBRANE"}


def _normalize_protocol_mode(mode: str) -> str:
    key = str(mode).strip().lower()
    if key not in _PROTOCOL_MODES:
        allowed = ", ".join(_PROTOCOL_MODES)
        raise ValueError(f"mode must be one of: {allowed} (got {mode!r})")
    return key


def _normalize_denss_mode(denss_mode: str) -> str:
    key = str(denss_mode).strip().lower()
    if key not in _DENSS_MODES:
        allowed = ", ".join(sorted(_DENSS_MODES))
        raise ValueError(f"denss_mode must be one of: {allowed} (got {denss_mode!r})")
    return _DENSS_MODES[key]


def _denss_executable(name: str) -> str:
    """Resolve a DENSS CLI next to this Python, then on PATH."""
    cand = Path(sys.executable).resolve().parent / name
    if cand.is_file() and os.access(cand, os.X_OK):
        return str(cand)
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(
        f"model_density: DENSS executable {name!r} not found next to "
        f"{sys.executable} or on PATH. Install denss into this environment "
        f"(e.g. pip install denss)."
    )


def model_density(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    gnom_path: Optional[SingletonPathExpressionArg] = None,
    mode: str = "pilot",
    denss_mode: str = "slow",
    n_maps: int = 20,
    n_jobs: int = 1,
    visualize_all: bool = False,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    SAXS / small-angle x-ray scattering: ab initio continuous electron-density reconstruction with DENSS (Grant protocol; density map / FSC resolution / voxel σ map). Requires the DENSS package (`denss`, `denss-all`, `denss-refine`) installed in the active Python environment.

    Protocol `mode`: `pilot` runs a single DENSS reconstruction; `average` runs denss-all (N maps, enantiomer selection, alignment, averaging, FSC) and writes a voxel-wise σ map from the aligned replicas; `refined` runs denss-all then denss-refine of the average against the data (σ still from the denss-all aligned stack). Pipeline q is converted to Å⁻¹ for DENSS staging (never pass autosaxs nm GNOM `.out` files to DENSS unchanged). Alignment is denss-all's built-in procedure (no separate aligner).

    ### Arguments

    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where DENSS outputs are written.
    - `gnom_path` (str | None, default `None`): Optional GNOM/DATGNOM `.out` used only for \(D_{\\max}\) (nm→Å). Smooth \(I(q)\) comes from the staged Å `.dat` (DENSS may fit internally).
    - `mode` (str, default `pilot`): Protocol stage: `pilot`, `average`, or `refined`.
    - `denss_mode` (str, default `slow`): DENSS algorithm mode: `slow`, `fast`, or `membrane`.
    - `n_maps` (int, default `20`): Number of reconstructions for `average`/`refined` (ignored in `pilot`; must be ≥2 when used).
    - `n_jobs` (int, default `1`): Parallel cores for denss-all.
    - `visualize_all` (bool, default `False`): When True, write slice GIF/PNG under `{output}/visuals/` (synced YZ/XZ/XY cuts through the particle AABB; nm scale bar below panels; electron-ish colormap).
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    ### Returns

    `dict[str, str]` with:

    - `output_subdir`: Directory containing DENSS artifacts for this sample.
    - `density_map_path`: Primary density MRC (pilot map, average map, or refined map).
    - `avg_map_path`: Averaged MRC path when averaging ran; empty string for `pilot`.
    - `sigma_map_path`: Voxel-wise density σ MRC from denss-all `*_aligned.mrc` stack when averaging ran; empty string for `pilot`.
    - `fsc_path`: FSC curve path when averaging ran; empty string otherwise.
    - `map_fit_path`: Calculated vs experimental fit file when present; else empty.
    - `denss_log_path`: Main log for the completed mode.
    - `visuals_dir`, `slices_gif`, `midplanes_png` when `visualize_all=True` (empty strings otherwise).

    ### Python usage

    ```python
    from autosaxs.skill import model_density

    out = model_density(
        profile="subtracted/sub_sample_01.dat",
        output_dir="denss",
        mode="pilot",
        denss_mode="slow",
        visualize_all=False,
        use_cache=False,
    )

    print(out["density_map_path"])
    ```

    ### CLI usage

    ```bash
    autosaxs model-density subtracted/sub_sample_01.dat --output-dir denss --mode pilot --denss-mode slow
    autosaxs model-density subtracted/sub_sample_01.dat --output-dir denss --visualize-all
    ```
    """
    _ = config_path
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    protocol = _normalize_protocol_mode(mode)
    denss_tok = _normalize_denss_mode(denss_mode)
    if int(n_maps) < 2 and protocol in ("average", "refined"):
        raise ValueError("n_maps must be >= 2 for mode average/refined")
    if int(n_jobs) < 1:
        raise ValueError("n_jobs must be >= 1")
    profile = coerce_dat_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("model_density input files must have .dat extension")
    input_batch: List[Dict[str, Union[str, List[str]]]] = [{"profile": p} for p in expanded_profiles]
    if gnom_path is not None:
        gnom_expr = coerce_singleton_path_expression(gnom_path)
        gnom_single = gnom_expr.unwrap()[0]
        for inp in input_batch:
            inp["gnom_path"] = gnom_single
    return _model_density_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        mode=protocol,
        denss_mode=denss_tok,
        n_maps=int(n_maps),
        n_jobs=int(n_jobs),
        visualize_all=bool(visualize_all),
        event_bus=bus,
        use_cache=use_cache,
    )


def _stage_angstrom_dat(profile: str, output_dir: str, base: str) -> str:
    """Write ``{base}_denss_input.dat`` with q in Å⁻¹ for DENSS."""
    q, I, sigma = load_saxs_1d_any(profile)
    q, I, sigma = ensure_q_nm(q, I, sigma)
    q_a = q / 10.0
    dest = os.path.join(output_dir, f"{base}_denss_input.dat")
    write_saxs_atsas_format(dest, q_a, I, sigma)
    return os.path.normpath(os.path.abspath(dest))


def _dmax_angstrom_from_gnom(gnom_path: Optional[str]) -> Optional[float]:
    if not gnom_path:
        return None
    parsed = parse_gnom_out(gnom_path)
    rmax_nm = parsed.get("real_space_rmax")
    if rmax_nm is None:
        return None
    return float(rmax_nm) * 10.0


def _emit(event_bus: Optional[EventBus], text: str) -> None:
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": text})
    else:
        print(text, flush=True)


def _run_cmd(
    cmd: List[str],
    *,
    cwd: str,
    event_bus: Optional[EventBus],
    label: str,
) -> subprocess.CompletedProcess:
    """Run a DENSS CLI, streaming merged stdout/stderr line-by-line."""
    _emit(event_bus, f"model_density: {label}…")
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    chunks: List[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        # denss-all uses \\r progress updates; normalize to one visible line each time.
        line = raw.replace("\r", "\n").rstrip("\n")
        if not line:
            continue
        for part in line.split("\n"):
            part = part.strip()
            if not part:
                continue
            chunks.append(part)
            _emit(event_bus, part)
    returncode = proc.wait()
    combined = "\n".join(chunks)
    if returncode != 0:
        raise RuntimeError(
            f"model_density failed: {label} exited with code {returncode}\n"
            f"cmd: {' '.join(cmd)}\n"
            f"{combined}"
        )
    return subprocess.CompletedProcess(cmd, returncode, combined, "")


def _common_denss_args(
    *,
    denss_mode: str,
    dmax_a: Optional[float],
    output_prefix: str,
) -> List[str]:
    args = [
        "-m",
        denss_mode,
        "-o",
        output_prefix,
        "--plot_off",
        "-q",
    ]
    if dmax_a is not None and dmax_a > 0:
        args.extend(["-d", f"{dmax_a:.6g}"])
    return args


def _find_newest_avg_bundle(output_dir: str, prefix: str) -> Tuple[str, str]:
    """
    denss-all writes ``{prefix}/`` or ``{prefix}_N/`` under ``output_dir``.
    Return ``(bundle_dir, avg_mrc_path)``.
    """
    root = Path(output_dir)
    candidates: List[Tuple[float, Path, Path]] = []
    for p in root.iterdir():
        if not p.is_dir():
            continue
        if p.name != prefix and not p.name.startswith(prefix + "_"):
            continue
        avgs = sorted(p.glob("*_avg.mrc"))
        if not avgs:
            continue
        candidates.append((p.stat().st_mtime, p, avgs[0]))
    if not candidates:
        raise RuntimeError(
            f"model_density: denss-all finished but no *_avg.mrc found under {output_dir} "
            f"(expected folder named {prefix!r} or {prefix}_*)"
        )
    candidates.sort(key=lambda t: t[0])
    _, bundle, avg = candidates[-1]
    return str(bundle.resolve()), str(avg.resolve())


def _first_existing(*paths: str) -> str:
    for p in paths:
        if p and os.path.isfile(p):
            return os.path.normpath(os.path.abspath(p))
    return ""


def _parse_fsc_resolution_a(fsc_path: str) -> Optional[float]:
    if not fsc_path or not os.path.isfile(fsc_path):
        return None
    try:
        with open(fsc_path, "r", encoding="utf-8", errors="replace") as f:
            header = f.readline()
        m = re.search(r"Resolution\s*=\s*([0-9.]+)", header, flags=re.IGNORECASE)
        if m:
            return float(m.group(1))
    except (OSError, ValueError):
        return None
    return None


def _list_aligned_mrcs(bundle_dir: str) -> List[str]:
    """Collect denss-all aligned replica maps (`*_aligned.mrc`), excluding supports."""
    paths = sorted(
        str(p.resolve())
        for p in Path(bundle_dir).glob("*_aligned.mrc")
        if p.is_file() and "_support" not in p.name.lower()
    )
    return paths


def _write_sigma_map_from_aligned(
    bundle_dir: str,
    *,
    output_path: str,
    event_bus: Optional[EventBus] = None,
) -> str:
    """
    Voxel-wise standard deviation across denss-all aligned density maps.

    Uses denss-all's already-aligned `*_aligned.mrc` stack (enantiomer-selected +
    CC-aligned to the denss-all reference). Population std (ddof=0).
    """
    import denss

    aligned = _list_aligned_mrcs(bundle_dir)
    if len(aligned) < 2:
        raise RuntimeError(
            f"model_density: need ≥2 aligned MRC maps for σ map; found {len(aligned)} in {bundle_dir}"
        )
    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {"text": f"model_density: computing σ map from {len(aligned)} aligned densities…"},
        )
    rhos: List[np.ndarray] = []
    side0 = None
    shape0 = None
    for path in aligned:
        rho, side = denss.read_mrc(path)
        rho = np.asarray(rho, dtype=np.float64)
        if shape0 is None:
            shape0 = rho.shape
            side0 = side
        elif rho.shape != shape0:
            raise RuntimeError(
                f"model_density: aligned map shape mismatch: {path} has {rho.shape}, expected {shape0}"
            )
        rhos.append(rho)
    stack = np.stack(rhos, axis=0)
    sigma = np.std(stack, axis=0, ddof=0)
    denss.write_mrc(sigma, side0, filename=output_path)
    return os.path.normpath(os.path.abspath(output_path))


@apply_batch(stem_from_keys="profile", per_sample_subdir="always")
@run_with_cache(
    path_keys_for_hash=["profile", "gnom_path"],
    kwargs_for_hash=None,
    kwargs_for_hash_keys=["mode", "denss_mode", "n_maps", "n_jobs", "visualize_all"],
    include_config_in_hash=False,
    warn_if_no_cache=True,
)
def _model_density_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    mode: str = "pilot",
    denss_mode: str = "SLOW",
    n_maps: int = 20,
    n_jobs: int = 1,
    visualize_all: bool = False,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
    protocol = _normalize_protocol_mode(mode) if str(mode).lower() in _PROTOCOL_MODES else mode
    if protocol not in _PROTOCOL_MODES:
        protocol = _normalize_protocol_mode(str(mode))
    denss_tok = (
        denss_mode
        if str(denss_mode).upper() in ("SLOW", "FAST", "MEMBRANE")
        else _normalize_denss_mode(str(denss_mode))
    )

    profile = input_paths.get("profile")
    user_gnom_path = input_paths.get("gnom_path")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if isinstance(user_gnom_path, list):
        user_gnom_path = user_gnom_path[0] if user_gnom_path else None
    if profile is not None:
        profile = os.path.expanduser(str(profile))
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("model_density requires input_paths['profile']")

    profile = os.path.normpath(os.path.abspath(profile))
    gnom_path: Optional[str] = None
    if user_gnom_path:
        gnom_path = os.path.normpath(os.path.abspath(os.path.expanduser(str(user_gnom_path))))
        if not os.path.isfile(gnom_path):
            raise FileNotFoundError(f"model_density gnom_path not found: {gnom_path}")

    os.makedirs(output_dir, exist_ok=True)
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(profile))[0])
    staged = _stage_angstrom_dat(profile, output_dir, base)
    dmax_a = _dmax_angstrom_from_gnom(gnom_path)

    denss_bin = _denss_executable("denss")
    denss_all_bin = _denss_executable("denss-all")
    denss_refine_bin = _denss_executable("denss-refine")

    density_map_path = ""
    avg_map_path = ""
    sigma_map_path = ""
    fsc_path = ""
    map_fit_path = ""
    denss_log_path = ""
    bundle_dir = output_dir

    common = _common_denss_args(denss_mode=denss_tok, dmax_a=dmax_a, output_prefix=base)

    if protocol == "pilot":
        _run_cmd(
            [denss_bin, "-f", staged, *common],
            cwd=output_dir,
            event_bus=event_bus,
            label=f"denss pilot ({denss_tok})",
        )
        density_map_path = _first_existing(os.path.join(output_dir, f"{base}.mrc"))
        map_fit_path = _first_existing(os.path.join(output_dir, f"{base}_map.fit"))
        denss_log_path = _first_existing(os.path.join(output_dir, f"{base}.log"))
        if not density_map_path:
            raise RuntimeError(f"model_density: denss pilot finished but {base}.mrc not found in {output_dir}")

    else:
        _run_cmd(
            [
                denss_all_bin,
                "-f",
                staged,
                "-nm",
                str(int(n_maps)),
                "-j",
                str(int(n_jobs)),
                *common,
            ],
            cwd=output_dir,
            event_bus=event_bus,
            label=f"denss-all ({denss_tok}, n_maps={int(n_maps)}, n_jobs={int(n_jobs)})",
        )
        bundle_dir, avg_map_path = _find_newest_avg_bundle(output_dir, base)
        fsc_path = _first_existing(
            os.path.join(bundle_dir, f"{base}_fsc.dat"),
            *[str(p) for p in sorted(Path(bundle_dir).glob("*_fsc.dat"))],
        )
        map_fit_path = _first_existing(
            os.path.join(bundle_dir, f"{base}_map.fit"),
            *[str(p) for p in sorted(Path(bundle_dir).glob("*_map.fit"))],
        )
        denss_log_path = _first_existing(
            os.path.join(bundle_dir, f"{base}_final.log"),
            *[str(p) for p in sorted(Path(bundle_dir).glob("*_final.log"))],
        )
        density_map_path = avg_map_path
        sigma_dest = os.path.join(bundle_dir, f"{base}_sigma.mrc")
        sigma_map_path = _write_sigma_map_from_aligned(
            bundle_dir, output_path=sigma_dest, event_bus=event_bus
        )

        if protocol == "refined":
            refine_prefix = f"{base}_refined"
            _run_cmd(
                [
                    denss_refine_bin,
                    "-f",
                    staged,
                    "-rho",
                    avg_map_path,
                    "-m",
                    denss_tok,
                    "-o",
                    refine_prefix,
                    "--plot_off",
                    "-q",
                    *(["-d", f"{dmax_a:.6g}"] if dmax_a is not None and dmax_a > 0 else []),
                ],
                cwd=output_dir,
                event_bus=event_bus,
                label=f"denss-refine ({denss_tok})",
            )
            density_map_path = _first_existing(os.path.join(output_dir, f"{refine_prefix}.mrc"))
            refined_fit = _first_existing(os.path.join(output_dir, f"{refine_prefix}_map.fit"))
            if refined_fit:
                map_fit_path = refined_fit
            denss_log_path = _first_existing(
                os.path.join(output_dir, f"{refine_prefix}.log"),
                denss_log_path,
            )
            if not density_map_path:
                raise RuntimeError(
                    f"model_density: denss-refine finished but {refine_prefix}.mrc not found in {output_dir}"
                )

    res_a = _parse_fsc_resolution_a(fsc_path)
    from autosaxs.core.report_fragments import write_skill_report_fragments

    md_parts = [
        "### DENSS electron density (model_density)\n",
        f"- Protocol mode: **{protocol}**; DENSS mode: **{denss_tok}**\n",
    ]
    if protocol != "pilot":
        md_parts.append(f"- Maps averaged: **{int(n_maps)}**\n")
    if res_a is not None:
        md_parts.append(f"- FSC resolution (0.5): **{res_a:.1f} Å**\n")
    if density_map_path:
        md_parts.append(
            f"- Primary density map: `{os.path.relpath(density_map_path, output_dir)}`\n"
        )
    if avg_map_path and avg_map_path != density_map_path:
        md_parts.append(f"- Average map: `{os.path.relpath(avg_map_path, output_dir)}`\n")
    if sigma_map_path:
        md_parts.append(
            f"- Density σ map (aligned ensemble): `{os.path.relpath(sigma_map_path, output_dir)}`\n"
        )
    if fsc_path:
        md_parts.append(f"- FSC: `{os.path.relpath(fsc_path, output_dir)}`\n")

    visuals_dir = ""
    slices_gif = ""
    midplanes_png = ""
    if bool(visualize_all) and density_map_path:
        from .vis import write_visuals

        vis_out = write_visuals(
            output_dir,
            density_map_path=density_map_path,
            event_bus=event_bus,
        )
        visuals_dir = str(vis_out.get("visuals_dir") or "")
        slices_gif = str(vis_out.get("slices_gif") or "")
        midplanes_png = str(vis_out.get("midplanes_png") or "")
        if visuals_dir:
            md_parts.append(
                f"- Visuals: `{os.path.relpath(visuals_dir, output_dir)}`\n"
            )

    summary_refs: List[Dict[str, Any]] = []
    if density_map_path:
        summary_refs.append(
            {
                "role": "density_map",
                "path": os.path.relpath(density_map_path, output_dir),
                "format": "text",
            }
        )
    if sigma_map_path:
        summary_refs.append(
            {
                "role": "sigma_map",
                "path": os.path.relpath(sigma_map_path, output_dir),
                "format": "text",
            }
        )
    if fsc_path:
        summary_refs.append(
            {
                "role": "fsc",
                "path": os.path.relpath(fsc_path, output_dir),
                "format": "text",
            }
        )

    write_skill_report_fragments(
        output_dir,
        base,
        "model_density",
        "".join(md_parts),
        summary_references=summary_refs or None,
        summary_extra={
            "protocol_mode": protocol,
            "denss_mode": denss_tok,
            "n_maps": int(n_maps) if protocol != "pilot" else 1,
            "fsc_resolution_A": res_a,
        },
        write_summary_yaml=True,
    )

    return {
        "output_subdir": output_dir,
        "density_map_path": density_map_path,
        "avg_map_path": avg_map_path or "",
        "sigma_map_path": sigma_map_path or "",
        "fsc_path": fsc_path or "",
        "map_fit_path": map_fit_path or "",
        "denss_log_path": denss_log_path or "",
        "visuals_dir": visuals_dir,
        "slices_gif": slices_gif,
        "midplanes_png": midplanes_png,
    }
