from __future__ import annotations

import os
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


def fit_bodies(
    profile: PathExpressionArg,
    output_dir: str = ".",
    *,
    use_cache: bool = True,
) -> Dict[str, Union[str, List[str]]]:
    """
    Run ATSAS `bodies` fits for multiple candidate shapes on a 1D profile, exporting fit files (FIR, PNG, YAML, CSV) and a comparison figure.
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    profile = coerce_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("fit_bodies input files must have .dat extension")
    input_batch = [{"profile": p} for p in expanded_profiles]
    return _fit_bodies_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
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


@apply_batch(stem_from_keys="profile", per_sample_subdir="always")
@run_with_cache(
    path_keys_for_hash=["profile"],
    kwargs_for_hash=None,
    include_config_in_hash=False,
)
def _fit_bodies_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = True,
    sample_index: int = 0,
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
    profile = input_paths.get("profile")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("fit_bodies requires input_paths['profile']")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "BODIES fit…"})
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(profile))[0])
    os.makedirs(output_dir, exist_ok=True)
    bodies_prefix = os.path.join(output_dir, "bodies_fit")
    proc = subprocess.run(
        ["bodies", f"--prefix={bodies_prefix}", profile],
        cwd=output_dir,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"fit_bodies failed: bodies exited with code {proc.returncode}\n{proc.stderr}")

    q, I, sigma, _meta = read_saxs(profile)
    fits_data = []
    to_plot = []
    for shape in BODIES_SHAPES_LIST:
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
        idx = q <= q_fit[-1]
        q_int, I_int = q[idx], I[idx]
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
        q_max = max(to_plot[i][-1] for i in range(0, len(to_plot), 3))
        idx2 = q <= q_max
        q_csv, I_exp_csv = q[idx2], I[idx2]
        csv_cols = ["q", "exp"] + [s for s, *_ in fits_data]
        csv_arrays = [q_csv, I_exp_csv] + [np.interp(q_csv, _q, _i) for _s, _p, _c, _q, _i in fits_data]
        pd.DataFrame(dict(zip(csv_cols, csv_arrays))).to_csv(bodies_fits_csv, index=False)
        to_plot2 = [q[idx2], I[idx2], {"label": "exp", "lw": 4}] + to_plot
        if sigma is None:
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
                sigmas=(sigma[idx2],),
                title=f"Fits comparison for\n{base}",
                xlabel="q (nm-1)",
                ylabel="I",
                legend=True,
                plotFilePath=bodies_fits_png,
            )
    return {"output_subdir": output_dir}

