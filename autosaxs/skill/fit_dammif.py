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
    ensure_q_nm,
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

    ### Arguments

    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where `dammif` outputs are written.
    - `gnom_path` (str | None, default `None`): Optional path to a GNOM `.out` file. If provided, `dammif` uses it.
    - `use_cache` (bool, default `True`): Enable/disable caching for this skill run.

    ### Returns

    `dict[str, str]` with:

    - `output_subdir`: Directory containing `dammif` fit artifacts (FIR/CIF and summary files).

    ### Python usage

    ```python
    from autosaxs.skill import fit_dammif

    out = fit_dammif(
        profile="subtracted/sub_sample_01.dat",
        output_dir="dammif",
        gnom_path="guinier/sample_01_gnom.out",
        use_cache=True,
    )

    print(out["output_subdir"])
    ```

    ### CLI usage

    ```bash
    autosaxs fit_dammif subtracted/sub_sample_01.dat --output-dir dammif --gnom-path guinier/sample_01_gnom.out
    ```
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
    gnom_path = os.path.expanduser(str(gnom_path)) if gnom_path else gnom_path
    if profile is not None:
        profile = os.path.expanduser(str(profile))
    if not gnom_path or not os.path.isfile(gnom_path):
        raise FileNotFoundError("fit_dammif requires input_paths['profile'] or input_paths['gnom_path']")
    # DAMMIF runs with cwd=output_dir; pass absolute paths into ATSAS (same issue as fit_bodies).
    gnom_path = os.path.normpath(os.path.abspath(gnom_path))
    if profile:
        profile = os.path.normpath(os.path.abspath(profile))
    if not os.path.isfile(gnom_path):
        raise FileNotFoundError(f"fit_dammif gnom_path not found after resolve: {gnom_path}")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "DAMMIF fit…"})
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(gnom_path))[0])
    os.makedirs(output_dir, exist_ok=True)
    for i in range(1, int(DAMMIF_REPS_NUM) + 1):
        # `cwd=output_dir` means DAMMIF prefixes should be relative,
        # otherwise it may attempt to write to output_dir/output_dir/...
        dammif_prefix = f"dammif-{i}"
        proc = subprocess.run(
            # ATSAS DAMMIF documentation uses FAST/SLOW/INTERACTIVE (case-sensitive in some builds).
            ["dammif", f"--prefix={dammif_prefix}", "--mode=FAST", str(gnom_path)],
            cwd=output_dir,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"fit_dammif failed: dammif exited with code {proc.returncode}\n{proc.stderr}")

    profile_1d = profile or gnom_path
    q, I, sigma, _ = read_saxs(profile_1d)
    # Autosaxs pipeline convention is q in nm^-1; incoming files may be in Å^-1.
    q, I, sigma = ensure_q_nm(q, I, sigma)
    to_plot = []
    fits_data = []
    # Reference "experimental" curve for the comparison plot/CSV.
    # If DAMMIF is given a GNOM .out, its .fir contains the exact curve DAMMIF fitted (often scaled/regularized),
    # which may not be on the same intensity scale as the original .dat.
    exp_ref: Optional[tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]] = None
    for i in range(DAMMIF_REPS_NUM):
        fir_path = os.path.join(output_dir, f"dammif-{i+1}.fir")
        cif_path = os.path.join(output_dir, f"dammif-{i+1}-1.cif")
        if not os.path.isfile(fir_path):
            continue
        data = np.loadtxt(fir_path, skiprows=1, dtype=np.float64)
        # DAMMIF .fir format:
        #   sExp | iExp | Err | iFit(+Const)  (header contains Chi^2)
        q_fit = data[:, 0]
        I_exp_fit = data[:, 1]
        sigma_fit = data[:, 2]
        I_fit = data[:, 3]

        # If DAMMIF was fed a GNOM .out, sExp is in Å^-1 (see dammif-*.log: "Angular units: angstrom").
        # Convert to nm^-1 so we can overlay with autosaxs' nm^-1 convention.
        if str(gnom_path).lower().endswith(".out"):
            q_fit = q_fit * 10.0
        if exp_ref is None:
            exp_ref = (q_fit, I_exp_fit, sigma_fit)

        # Compute chi2 in the same space DAMMIF reports it (against iExp/Err in .fir),
        # otherwise mixing with the raw profile grid/units can inflate chi2 dramatically.
        chi2 = calc_chi2(I_exp_fit, I_fit, sigma_fit)

        # Plot the fit curve (and the data DAMMIF actually fitted) on its native grid.
        q_int = q_fit
        I_fit_interp = I_fit
        atoms = read_bodies_cif(cif_path) if os.path.isfile(cif_path) else None
        descr = compute_dammif_descriptors(atoms) if atoms is not None else {}
        # Keys must match ATSAS prefixes (dammif-1, dammif-2, …) so dammif_fits.yml lines up with
        # ``dammif-{n}-1.cif`` (liveview and other UIs resolve the best model via ``{key}-1.cif``).
        rep_tag = f"dammif-{i + 1}"
        fits_data.append((rep_tag, {**descr, "chi2": float(chi2)}, q_int, I_fit_interp))
        to_plot.extend([q_int, I_fit_interp, f"{rep_tag}; $\\chi^2$: {chi2:.2f}"])
        if atoms is not None:
            PLTViewer.plot_3d_views_and_scattering(
                atoms,
                q_int,
                I_exp_fit,
                sigma_fit,
                I_fit_interp,
                plotFilePath=os.path.join(output_dir, f"{rep_tag}_view.png"),
            )
    dammif_fits_yml = os.path.join(output_dir, "dammif_fits.yml")
    dammif_fits_csv = os.path.join(output_dir, "dammif_fits.csv")
    dammif_fits_png = os.path.join(output_dir, f"{base}_fits.png")
    if fits_data:
        fits_yml = {k: {kk: float(vv) for kk, vv in d.items()} for k, d, _q, _i in fits_data}
        with open(dammif_fits_yml, "w") as f:
            yaml.dump(fits_yml, f, default_flow_style=False)
        if exp_ref is not None:
            q_exp, I_exp, sigma_exp = exp_ref
        else:
            q_exp, I_exp, sigma_exp = q, I, sigma

        csv_cols = ["q", "exp"] + [k for k, *_ in fits_data]
        csv_arrays = [q_exp, I_exp] + [np.interp(q_exp, _q, _i) for _k, _d, _q, _i in fits_data]
        pd.DataFrame(dict(zip(csv_cols, csv_arrays))).to_csv(dammif_fits_csv, index=False)

        to_plot2 = [q_exp, I_exp, {"label": "exp", "lw": 4}] + to_plot
        if sigma_exp is None:
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
                sigmas=(sigma_exp,),
                title=f"Fits comparison for\n{base}",
                xlabel="q (nm-1)",
                ylabel="I",
                legend=True,
                plotFilePath=dammif_fits_png,
            )
    return {"output_subdir": output_dir}

