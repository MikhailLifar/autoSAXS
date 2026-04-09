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
    compute_dammif_descriptors,
    read_bodies_cif,
    read_saxs,
    run_with_cache,
)
from .common import (
    PathExpressionArg,
    SingletonPathExpressionArg,
    coerce_path_expression,
    coerce_singleton_path_expression,
    expand_files_from_unwrapped,
)


def fit_dammif(
    profile: PathExpressionArg,
    output_dir: str = ".",
    *,
    gnom_path: Optional[SingletonPathExpressionArg] = None,
    use_cache: bool = True,
) -> Dict[str, Union[str, List[str]]]:
    """
    Run ATSAS `dammif` (ab initio shape reconstruction) on a 1D profile. If a GNOM output file is available, you can provide it; otherwise the profile is used.
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    profile = coerce_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("fit_dammif input files must have .dat extension")
    input_batch: List[Dict[str, Union[str, List[str]]]] = [{"profile": p} for p in expanded_profiles]
    if gnom_path is not None:
        gnom_expr = coerce_singleton_path_expression(gnom_path)
        gnom_single = gnom_expr.unwrap()[0]
        for inp in input_batch:
            inp["gnom_path"] = gnom_single
    return _fit_dammif_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
    )


DAMMIF_REPS_NUM = 2


@apply_batch(stem_from_keys="profile", per_sample_subdir="always")
@run_with_cache(
    path_keys_for_hash=["profile", "gnom_path"],
    kwargs_for_hash=None,
    include_config_in_hash=False,
)
def _fit_dammif_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = True,
    sample_index: int = 0,
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
    profile = input_paths.get("profile")
    gnom_path = input_paths.get("gnom_path") or profile
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if isinstance(gnom_path, list):
        gnom_path = gnom_path[0] if gnom_path else None
    if not gnom_path or not os.path.isfile(gnom_path):
        raise FileNotFoundError("fit_dammif requires input_paths['profile'] or input_paths['gnom_path']")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "DAMMIF fit…"})
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(gnom_path))[0])
    os.makedirs(output_dir, exist_ok=True)
    dammif_prefix = os.path.join(output_dir, "dammif")
    for i in range(1, int(DAMMIF_REPS_NUM) + 1):
        proc = subprocess.run(
            ["dammif", f"--prefix={dammif_prefix}-{i}", "--mode=fast", str(gnom_path)],
            cwd=output_dir,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"fit_dammif failed: dammif exited with code {proc.returncode}\n{proc.stderr}")

    profile_1d = profile or gnom_path
    q, I, sigma, _ = read_saxs(profile_1d)
    to_plot = []
    fits_data = []
    for i in range(DAMMIF_REPS_NUM):
        fir_path = os.path.join(output_dir, f"dammif-{i+1}.fir")
        cif_path = os.path.join(output_dir, f"dammif-{i+1}-1.cif")
        if not os.path.isfile(fir_path):
            continue
        data = np.loadtxt(fir_path, skiprows=1, dtype=np.float64)
        q_fit, I_fit, sigma_d = data[:, 0], data[:, 3], data[:, 2]
        q_fit = q_fit * 10.0
        idx = q <= q_fit[-1]
        q_int, I_int = q[idx], I[idx]
        sigma_interp = np.interp(q_int, q_fit, sigma_d)
        I_fit_interp = np.interp(q_int, q_fit, I_fit)
        chi2 = calc_chi2(I_int, I_fit_interp, sigma_interp)
        atoms = read_bodies_cif(cif_path) if os.path.isfile(cif_path) else None
        descr = compute_dammif_descriptors(atoms) if atoms is not None else {}
        fits_data.append((f"dammif-{i}", {**descr, "chi2": float(chi2)}, q_int, I_fit_interp))
        to_plot.extend([q_int, I_fit_interp, f"dammif-{i}; $\\chi^2$: {chi2:.2f}"])
        if atoms is not None:
            PLTViewer.plot_3d_views_and_scattering(
                atoms,
                q_int,
                I_int,
                sigma_interp,
                I_fit_interp,
                plotFilePath=os.path.join(output_dir, f"dammif-{i}_view.png"),
            )
    dammif_fits_yml = os.path.join(output_dir, "dammif_fits.yml")
    dammif_fits_csv = os.path.join(output_dir, "dammif_fits.csv")
    dammif_fits_png = os.path.join(output_dir, f"{base}_fits.png")
    if fits_data:
        fits_yml = {k: {kk: float(vv) for kk, vv in d.items()} for k, d, _q, _i in fits_data}
        with open(dammif_fits_yml, "w") as f:
            yaml.dump(fits_yml, f, default_flow_style=False)
        q_max = max(to_plot[i][-1] for i in range(0, len(to_plot), 3))
        idx2 = q <= q_max
        csv_cols = ["q", "exp"] + [k for k, *_ in fits_data]
        csv_arrays = [q[idx2], I[idx2]] + [np.interp(q[idx2], _q, _i) for _k, _d, _q, _i in fits_data]
        pd.DataFrame(dict(zip(csv_cols, csv_arrays))).to_csv(dammif_fits_csv, index=False)
        to_plot2 = [q[idx2], I[idx2], {"label": "exp", "lw": 4}] + to_plot
        if sigma is None:
            PLTViewer.view_curves(
                *to_plot2,
                title=f"Fits comparison for\n{base}",
                xlabel="q (nm-1)",
                ylabel="I",
                legend=True,
                plotFilePath=dammif_fits_png,
            )
        else:
            PLTViewer.view_curves(
                *to_plot2,
                sigmas=(sigma[idx2],),
                title=f"Fits comparison for\n{base}",
                xlabel="q (nm-1)",
                ylabel="I",
                legend=True,
                plotFilePath=dammif_fits_png,
            )
    return {"output_subdir": output_dir}

