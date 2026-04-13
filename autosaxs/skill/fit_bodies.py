from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np
import pandas as pd
import yaml

from .deps import (
    EventBus,
    EventType,
    PLTViewer,
    _strip_sub_int_prefix,
    apply_batch,
    calc_chi2,
    read_saxs,
    run_with_cache,
)
from .common import PathExpressionArg, coerce_path_expression, expand_files_from_unwrapped


def _write_bodies_invoke_log(output_dir: str, commands: List[List[str]]) -> None:
    """Write shell lines to re-run each ``bodies`` call (same cwd as the skill uses)."""
    cwd_abs = os.path.abspath(output_dir)
    lines = [
        "# fit_bodies: subprocess invocations (cwd matches subprocess cwd= for each run).",
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


def fit_bodies(
    profile: PathExpressionArg,
    output_dir: str = ".",
    *,
    shapes: Optional[List[str]] = None,
    first: Optional[int] = None,
    last: Optional[int] = None,
    use_cache: bool = True,
) -> Dict[str, Union[str, List[str]]]:
    """
    Run ATSAS `bodies` fits for multiple candidate shapes on a 1D profile, exporting fit files (FIR, PNG, YAML, CSV) and a comparison figure.

    ### Arguments

    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where `bodies` outputs are written.
    - `shapes` (list[str] | None, default `None`): Subset of body model names to fit (`BODIES_SHAPES_LIST`). `None` or empty means fit **all** models (single `bodies` invocation). A non-empty list runs `bodies --body=...` per shape.
    - `first` (int | None, default `None`): Passed to `bodies` as `--first` (1-based data point index). Omitted when `None`.
    - `last` (int | None, default `None`): Passed to `bodies` as `--last` (1-based data point index). Omitted when `None`.
    - `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

    ### Returns

    `dict[str, str]` with:

    - `output_subdir`: Directory containing the exported `bodies` fit artifacts.

    The directory typically contains multiple per-shape FIT files plus aggregated `bodies_fits.yml` and `bodies_fits.csv` if any shapes successfully fit.

    ### Python usage

    ```python
    from autosaxs.skill import fit_bodies

    out = fit_bodies(
        profile="subtracted/sub_sample_01.dat",
        output_dir="bodies",
        shapes=["cylinder", "ellipsoid"],
        first=10,
        last=120,
        use_cache=True,
    )

    print(out["output_subdir"])
    ```

    ### CLI usage

    ```bash
    autosaxs fit_bodies subtracted/sub_sample_01.dat --output-dir bodies --shapes cylinder ellipsoid --first 10 --last 120
    ```
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    profile = coerce_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("fit_bodies input files must have .dat extension")
    input_batch = [{"profile": p} for p in expanded_profiles]
    shapes_norm: Optional[List[str]] = None
    if shapes:
        shapes_norm = list(shapes)
    return _fit_bodies_paths(
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
)
def _fit_bodies_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = True,
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
        raise FileNotFoundError("fit_bodies requires input_paths['profile']")
    # Bodies runs with cwd=output_dir; the data path must be absolute, otherwise ATSAS resolves it
    # relative to output_dir (e.g. subtracted/foo.dat -> output_dir/subtracted/foo.dat → missing file).
    profile = os.path.normpath(os.path.abspath(profile))
    if not os.path.isfile(profile):
        raise FileNotFoundError(f"fit_bodies profile path not found after resolve: {profile}")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "BODIES fit…"})
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(profile))[0])
    os.makedirs(output_dir, exist_ok=True)
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
        if first is not None:
            cmd.append(f"--first={int(first)}")
        if last is not None:
            cmd.append(f"--last={int(last)}")
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
                f"fit_bodies failed: bodies exited with code {proc.returncode}\n{proc.stderr}"
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

    q_raw, I_raw, sigma_raw, _meta = read_saxs(profile)
    q_exp, I_exp, sigma_exp = _slice_exp_dat_columns(q_raw, I_raw, sigma_raw, first, last)
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
    bodies_fits_yml = os.path.join(output_dir, "bodies_fits.yml")
    bodies_fits_csv = os.path.join(output_dir, "bodies_fits.csv")
    bodies_fits_png = os.path.join(output_dir, f"{base}_fits.png")
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
    return {"output_subdir": output_dir}

