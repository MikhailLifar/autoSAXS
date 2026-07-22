from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd
import yaml

from autosaxs.core.pddf import (
    pddf_from_bodies_shape,
    save_pddf_dat,
    save_pddf_png,
)
from .deps import (
    EventBus,
    EventType,
    PLTViewer,
    _strip_sub_int_prefix,
    apply_batch,
    calc_chi2,
    ensure_q_nm,
    load_saxs_1d_any,
    run_guinier_analysis,
    run_with_cache,
    write_saxs_atsas_format,
)
from .common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    coerce_dat_path_expression,
    expand_files_from_unwrapped,
)


def _write_bodies_invoke_log(output_dir: str, commands: List[List[str]]) -> None:
    """Write shell lines to re-run each ``bodies`` call (same cwd as the skill uses)."""
    cwd_abs = os.path.abspath(output_dir)
    lines = [
        "# model_bodies: subprocess invocations (cwd matches subprocess cwd= for each run).",
        f"# output_dir (absolute): {cwd_abs}",
        "",
    ]
    for i, cmd in enumerate(commands, start=1):
        quoted = " ".join(shlex.quote(str(a)) for a in cmd)
        lines.append(f"# --- run {i} ---")
        lines.append(f"cd {shlex.quote(cwd_abs)} && {quoted}")
        lines.append("")
    log_path = os.path.join(output_dir, "bodies_invoke.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def model_bodies(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    shapes: Optional[List[str]] = None,
    first: Optional[int] = None,
    last: Optional[int] = None,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    SAXS / small-angle x-ray scattering: run ATSAS `bodies` shape fitting for multiple candidate shapes on a 1D profile, exporting fit files (FIR, PNG, YAML, CSV) and a comparison figure.

    ### Arguments

    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where `bodies` outputs are written.
    - `config_path` (str | None, default `None`): Optional YAML config path for CLI parity; this skill does not read a `model_bodies` section (no bundled defaults).
    - `shapes` (list[str] | None, default `None`): Subset of body model names to fit (`BODIES_SHAPES_LIST`). `None` or empty means fit **all** models (single `bodies` invocation). A non-empty list runs `bodies --body=...` per shape.
    - `first` (int | None, default `None`): Passed to `bodies` as `--first` (1-based data point index). If omitted, taken from the low-q end of the Guinier interval from in-process `fit_guinier`.
    - `last` (int | None, default `None`): Passed to `bodies` as `--last` (1-based data point index). Omitted when `None`.
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    ### Returns

    `dict[str, str]` with:

    - `output_subdir`: Directory containing the exported `bodies` fit artifacts.

    The directory typically contains multiple per-shape FIT files plus aggregated `bodies_fits.yml` and `bodies_fits.csv` if any shapes successfully fit. Each fitted shape also gets `{shape}_pr.dat` and `{shape}_pr.png` (GNOM-style p(r) from the voxel DAM used for 3D views, via Monte Carlo bead-pair sampling).

    ### Python usage

    ```python
    from autosaxs.skill import model_bodies

    out = model_bodies(
        profile="subtracted/sub_sample_01.dat",
        output_dir="bodies",
        shapes=["cylinder", "ellipsoid"],
        first=10,
        last=120,
        use_cache=False,
    )

    print(out["output_subdir"])
    ```

    ### CLI usage

    ```bash
    autosaxs model-bodies subtracted/sub_sample_01.dat --output-dir bodies --shapes cylinder ellipsoid --first 10 --last 120
    ```
    """
    _ = config_path
    shapes_norm: Optional[List[str]] = None
    if shapes:
        shapes_norm = list(shapes)
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    profile = coerce_dat_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("model_bodies input files must have .dat extension")
    input_batch = [{"profile": p} for p in expanded_profiles]
    return _model_bodies_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        shapes=shapes_norm,
        first=first,
        last=last,
    )


BODIES_SHAPES_LIST = [
    "cylinder",
    "dumbbell",
    "ellipsoid",
    "elliptic-cylinder",
    "hollow-cylinder",
    "hollow-sphere",
    "parallelepiped",
    "rotation-ellipsoid",
]


def _guinier_from_profile(
    q_nm: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray],
    atsas_dat_path: str,
) -> Dict[str, Any]:
    """In-process fit_guinier for Guinier interval (used to derive ``--first``)."""
    results = run_guinier_analysis(q_nm, I, sigma, atsas_dat_path=atsas_dat_path)
    if results.get("chosen") is None:
        raise RuntimeError(
            "model_bodies: fit_guinier (Guinier analysis) did not return a chosen result; "
            "cannot derive --first."
        )
    ch_int = results.get("chosen_interval")
    return {
        "rg": results.get("chosen_Rg"),
        "rg_min": results.get("rg_min"),
        "rg_max": results.get("rg_max"),
        "q_min": ch_int[0] if ch_int else None,
        "q_max": ch_int[1] if ch_int else None,
        "chosen_interval": ch_int,
        "quality_class": results.get("quality_class"),
    }


def _q_to_first_point_1based(q_nm: np.ndarray, q_target: float) -> int:
    q_nm = np.asarray(q_nm, dtype=float)
    if not np.isfinite(q_target):
        raise ValueError("model_bodies: Guinier q_min is not finite")
    idx = int(np.argmin(np.abs(q_nm - float(q_target))))
    return idx + 1


def _slice_exp_dat_columns(
    q: np.ndarray,
    I: np.ndarray,
    sigma: Optional[np.ndarray],
    first: Optional[int],
    last: Optional[int],
) -> tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]:
    """
    Restrict experimental columns to the same 1-based point range as BODIES ``--first`` / ``--last``
    (inclusive on ``last``). If both are omitted, return inputs unchanged.
    """
    n = int(len(q))
    if n == 0 or (first is None and last is None):
        return q, I, sigma
    i0 = max(0, int(first) - 1) if first is not None else 0
    i1 = min(n, int(last)) if last is not None else n
    if i0 >= i1:
        return q, I, sigma
    sl = slice(i0, i1)
    s_exp = sigma[sl] if sigma is not None else None
    return q[sl], I[sl], s_exp


@apply_batch(stem_from_keys="profile", per_sample_subdir="always")
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash_keys=["shapes", "first", "last"],
    include_config_in_hash=False,
    warn_if_no_cache=True,
)
def _model_bodies_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
    shapes: Optional[List[str]] = None,
    first: Optional[int] = None,
    last: Optional[int] = None,
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
    profile = input_paths.get("profile")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    profile = os.path.expanduser(str(profile))
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("model_bodies requires input_paths['profile']")
    # Bodies runs with cwd=output_dir; the data path must be absolute, otherwise ATSAS resolves it
    # relative to output_dir (e.g. subtracted/foo.dat -> output_dir/subtracted/foo.dat → missing file).
    profile = os.path.normpath(os.path.abspath(profile))
    if not os.path.isfile(profile):
        raise FileNotFoundError(f"model_bodies profile path not found after resolve: {profile}")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "BODIES fit…"})
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(profile))[0])
    os.makedirs(output_dir, exist_ok=True)

    user_first = first
    q_nm, I, sigma = load_saxs_1d_any(profile)
    q_nm, I, sigma = ensure_q_nm(q_nm, I, sigma)
    n_pts = int(len(q_nm))

    guinier_info: Optional[Dict[str, Any]] = None
    if user_first is None:
        if event_bus:
            event_bus.publish(EventType.MESSAGE, {"text": "model_bodies: running fit_guinier (in-process)…"})
        atsas_dat_path = os.path.join(output_dir, f"{base}_atsas.dat")
        write_saxs_atsas_format(atsas_dat_path, q_nm, I, sigma)
        guinier_info = _guinier_from_profile(q_nm, I, sigma, atsas_dat_path)
        if event_bus:
            event_bus.publish(EventType.MESSAGE, {"text": "model_bodies: fit_guinier completed."})
        if guinier_info.get("q_min") is None:
            raise RuntimeError("model_bodies: cannot derive --first without fit_guinier q_min.")
        first_pt = _q_to_first_point_1based(q_nm, float(guinier_info["q_min"]))
    else:
        first_pt = int(user_first)

    last_pt: Optional[int] = int(last) if last is not None else None
    if first_pt < 1 or first_pt >= n_pts:
        raise ValueError(
            f"model_bodies: require 1 <= first < n_points ({n_pts}); got first={first_pt}",
        )
    if last_pt is not None:
        if last_pt < 1 or last_pt > n_pts or first_pt >= last_pt:
            raise ValueError(
                f"model_bodies: require 1 <= first < last <= n_points ({n_pts}); "
                f"got first={first_pt}, last={last_pt}",
            )

    # ``cwd=output_dir`` means BODIES ``--prefix`` must be relative (basename only), same as DAMMIF:
    # an absolute prefix under ``output_dir`` can make ATSAS write under output_dir/output_dir/… so
    # ``bodies_fit-<shape>.fir`` is not found next to the skill’s expected paths.
    bodies_prefix = "bodies_fit"

    shapes_requested: Optional[List[str]] = None
    if shapes:
        seen: List[str] = []
        for s in shapes:
            if s not in BODIES_SHAPES_LIST:
                raise ValueError(
                    f"Unknown bodies shape {s!r}; allowed: {BODIES_SHAPES_LIST}"
                )
            if s not in seen:
                seen.append(s)
        shapes_requested = seen

    def _bodies_cmd(*, body: Optional[str] = None) -> List[str]:
        cmd: List[str] = ["bodies", f"--prefix={bodies_prefix}"]
        if body is not None:
            cmd.append(f"--body={body}")
        cmd.append(f"--first={int(first_pt)}")
        if last_pt is not None:
            cmd.append(f"--last={int(last_pt)}")
        cmd.append(profile)
        return cmd

    if shapes_requested is None:
        _write_bodies_invoke_log(output_dir, [_bodies_cmd()])
    else:
        _write_bodies_invoke_log(output_dir, [_bodies_cmd(body=s) for s in shapes_requested])

    if shapes_requested is None:
        proc = subprocess.run(
            _bodies_cmd(),
            cwd=output_dir,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"model_bodies failed: bodies exited with code {proc.returncode}\n{proc.stderr}"
            )
        shapes_to_scan = list(BODIES_SHAPES_LIST)
    else:
        shapes_to_scan = list(shapes_requested)
        for shape in shapes_requested:
            proc = subprocess.run(
                _bodies_cmd(body=shape),
                cwd=output_dir,
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                if event_bus:
                    event_bus.publish(
                        EventType.MESSAGE,
                        {
                            "text": f"bodies: fit failed for {shape!r} (exit {proc.returncode}), skipping",
                        },
                    )
                continue

    q_exp, I_exp, sigma_exp = _slice_exp_dat_columns(q_nm, I, sigma, first_pt, last_pt)
    fits_data = []
    to_plot = []
    for shape in shapes_to_scan:
        fir_path = os.path.join(output_dir, f"bodies_fit-{shape}.fir")
        if not os.path.isfile(fir_path):
            continue
        with open(fir_path, "r") as f:
            first_line = f.readline().strip()
        params_dict = {}
        import re

        match = re.match(r"^(?P<shape>[\w\-]+):\s*(?P<params>.+)$", first_line)
        if match:
            for param_assignment in match.group("params").split(","):
                param_assignment = param_assignment.strip()
                kv = re.match(r"^(\w+)\s*=\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)$", param_assignment)
                if kv:
                    params_dict[kv.group(1)] = float(kv.group(2))
        data = np.loadtxt(fir_path, skiprows=1, dtype=np.float64)
        q_fit, I_fit, sigma_bodies = data[:, 0], data[:, 3], data[:, 2]
        idx = q_exp <= q_fit[-1]
        q_int, I_int = q_exp[idx], I_exp[idx]
        sigma_interp = np.interp(q_int, q_fit, sigma_bodies)
        I_fit_interp = np.interp(q_int, q_fit, I_fit)
        chi2 = calc_chi2(I_int, I_fit_interp, sigma_interp)
        fits_data.append((shape, params_dict, chi2, q_int, I_fit_interp))
        to_plot.extend([q_int, I_fit_interp, f"{shape}; $\\chi^2$: {chi2:.2f}"])
        PLTViewer.plot_3d_views_and_scattering(
            (shape, params_dict),
            q_int,
            I_int,
            sigma_interp,
            I_fit_interp,
            plotFilePath=os.path.join(output_dir, f"{shape}_view.png"),
        )
        if params_dict:
            try:
                r_pr, p_pr = pddf_from_bodies_shape(shape, params_dict)
                save_pddf_dat(os.path.join(output_dir, f"{shape}_pr.dat"), r_pr, p_pr)
                save_pddf_png(
                    os.path.join(output_dir, f"{shape}_pr.png"),
                    r_pr,
                    p_pr,
                    title=f"BODIES {shape} p(r)",
                )
            except Exception as exc:
                if event_bus:
                    event_bus.publish(
                        EventType.MESSAGE,
                        {"text": f"model_bodies: p(r) not written for {shape!r} ({exc})"},
                    )
    bodies_fits_yml = os.path.join(output_dir, "bodies_fits.yml")
    bodies_fits_csv = os.path.join(output_dir, "bodies_fits.csv")
    bodies_fits_png = os.path.join(output_dir, f"{base}_fits.png")
    fit_params_path = os.path.join(output_dir, f"{base}_fit_bodies_fit_params.yml")
    fit_params_doc: Dict[str, Any] = {
        "first": int(first_pt),
        "last": last_pt,
        "fit_param_sources": {
            "first": "user" if user_first is not None else "fit_guinier",
        },
    }
    if guinier_info is not None:
        fit_params_doc["fit_guinier"] = {
            "rg": guinier_info.get("rg"),
            "rg_min": guinier_info.get("rg_min"),
            "rg_max": guinier_info.get("rg_max"),
            "q_min": guinier_info.get("q_min"),
            "q_max": guinier_info.get("q_max"),
            "chosen_interval": guinier_info.get("chosen_interval"),
            "quality_class": guinier_info.get("quality_class"),
        }
    with open(fit_params_path, "w") as fp:
        yaml.dump(fit_params_doc, fp, default_flow_style=False)

    if fits_data:
        fits_yml = {s: {**p, "chi2": float(c)} for s, p, c, _q, _i in fits_data}
        with open(bodies_fits_yml, "w") as f:
            yaml.dump(fits_yml, f, default_flow_style=False)
        q_max = max(float(np.max(to_plot[i])) for i in range(0, len(to_plot), 3))
        idx2 = q_exp <= q_max
        q_csv, I_exp_csv = q_exp[idx2], I_exp[idx2]
        csv_cols = ["q", "exp"] + [s for s, *_ in fits_data]
        csv_arrays = [q_csv, I_exp_csv] + [np.interp(q_csv, _q, _i) for _s, _p, _c, _q, _i in fits_data]
        pd.DataFrame(dict(zip(csv_cols, csv_arrays))).to_csv(bodies_fits_csv, index=False)
        to_plot2 = [q_exp[idx2], I_exp[idx2], {"label": "exp", "lw": 4}] + to_plot
        if sigma_exp is None:
            PLTViewer.view_curves(
                *to_plot2,
                title=f"Fits comparison for\n{base}",
                xlabel="q (nm-1)",
                ylabel="I",
                legend=True,
                plotFilePath=bodies_fits_png,
            )
        else:
            PLTViewer.view_curves(
                *to_plot2,
                sigmas=(sigma_exp[idx2],),
                title=f"Fits comparison for\n{base}",
                xlabel="q (nm-1)",
                ylabel="I",
                legend=True,
                plotFilePath=bodies_fits_png,
            )
    from autosaxs.core.report_fragments import write_skill_report_fragments

    md_parts = ["### ATSAS bodies\n"]
    if os.path.isfile(bodies_fits_png):
        md_parts.append(f"![Fits comparison]({os.path.basename(bodies_fits_png)})\n")
    summary_refs: List[Dict[str, Any]] = []
    if os.path.isfile(bodies_fits_yml):
        summary_refs.append({"role": "bodies_fits_yml", "path": os.path.basename(bodies_fits_yml), "format": "text"})
    if os.path.isfile(bodies_fits_csv):
        summary_refs.append(
            {
                "role": "bodies_fits_csv_preview",
                "path": os.path.basename(bodies_fits_csv),
                "format": "csv",
                "row": 0,
                "columns": ["q", "exp"],
            }
        )
    write_skill_report_fragments(
        output_dir,
        base,
        "model_bodies",
        "".join(md_parts),
        summary_references=summary_refs or None,
        write_summary_yaml=bool(summary_refs),
    )
    return {"output_subdir": output_dir}

