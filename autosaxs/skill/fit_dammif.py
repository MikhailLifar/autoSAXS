from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd
import yaml

from autosaxs.core.pddf import (
    pddf_from_dammif_atoms,
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
    compute_dammif_descriptors,
    ensure_q_nm,
    read_bodies_cif,
    read_saxs,
    run_with_cache,
)
from .common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    SingletonPathExpressionArg,
    coerce_dat_path_expression,
    coerce_singleton_path_expression,
    expand_files_from_unwrapped,
)


def fit_dammif(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    gnom_path: Optional[SingletonPathExpressionArg] = None,
    dammif_reps_num: int = 1,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    SAXS / small-angle x-ray scattering: run ATSAS `dammif` (ab initio shape reconstruction) on a 1D profile (shape reconstruction / bead model). When no GNOM `.out` is supplied, `fit_distances` is run in-process to obtain one.

    ### Arguments

    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where `dammif` outputs are written.
    - `gnom_path` (str | None, default `None`): Optional path to a GNOM/DATGNOM `.out` file for DAMMIF. If omitted, `fit_distances` is run in-process on `profile` and its `best_gnom_out_path` is used.
    - `dammif_reps_num` (int, default `1`): Number of independent DAMMIF runs (replicas) to execute.
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    ### Returns

    `dict[str, str]` with:

    - `output_subdir`: Directory containing `dammif` fit artifacts (FIR/CIF and summary files). Each replica also gets `{rep}_pr.dat` and `{rep}_pr.png` (GNOM-style p(r) from DAM bead pairs via Monte Carlo).

    ### Python usage

    ```python
    from autosaxs.skill import fit_dammif

    out = fit_dammif(
        profile="subtracted/sub_sample_01.dat",
        output_dir="dammif",
        gnom_path="guinier/sample_01_gnom.out",
        dammif_reps_num=1,
        use_cache=False,
    )

    print(out["output_subdir"])
    ```

    ### CLI usage

    ```bash
    autosaxs fit-dammif subtracted/sub_sample_01.dat --output-dir dammif --dammif-reps-num 1
    ```
    """
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    if int(dammif_reps_num) < 1:
        raise ValueError("dammif_reps_num must be >= 1")
    profile = coerce_dat_path_expression(profile)
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
        dammif_reps_num=int(dammif_reps_num),
        event_bus=bus,
        use_cache=use_cache,
    )


def _gnom_path_from_fit_distances(
    profile: str,
    output_dir: str,
    event_bus: Optional[EventBus],
) -> str:
    """Run fit_distances in-process and return the selected DATGNOM ``.out`` path."""
    from .fit_distances import _fit_distances_paths

    distances_dir = os.path.join(output_dir, "_fit_distances_for_dammif")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "fit_dammif: running fit_distances (in-process)…"})
    result = _fit_distances_paths(
        input_paths={"profile": profile},
        output_dir=distances_dir,
        rg_nm=None,
        first=None,
        last=None,
        smooth=None,
        event_bus=event_bus,
        use_cache=False,
        per_sample_subdir_override="never",
    )
    from .gnom_fit_common import failure_message_from_result, is_atsas_fit_ok

    if not is_atsas_fit_ok(result):
        raise RuntimeError(
            failure_message_from_result(result, skill_id="fit_distances")
            or "fit_dammif: fit_distances did not produce a GNOM .out; cannot run DAMMIF."
        )
    gnom_path = result.get("best_gnom_out_path")
    gnom_path = os.path.normpath(os.path.abspath(os.path.expanduser(str(gnom_path))))
    if not os.path.isfile(gnom_path):
        raise RuntimeError(f"fit_dammif: GNOM .out not found after resolve: {gnom_path}")
    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {"text": f"fit_dammif: fit_distances completed (gnom={os.path.basename(str(gnom_path))})."},
        )
    return str(gnom_path)


@apply_batch(stem_from_keys="profile", per_sample_subdir="always")
@run_with_cache(
    path_keys_for_hash=["profile", "gnom_path"],
    kwargs_for_hash=None,
    include_config_in_hash=False,
    warn_if_no_cache=True,
)
def _fit_dammif_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    dammif_reps_num: int = 1,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
    if int(dammif_reps_num) < 1:
        raise ValueError("dammif_reps_num must be >= 1")
    profile = input_paths.get("profile")
    user_gnom_path = input_paths.get("gnom_path")
    if isinstance(profile, list):
        profile = profile[0] if profile else None
    if isinstance(user_gnom_path, list):
        user_gnom_path = user_gnom_path[0] if user_gnom_path else None
    if profile is not None:
        profile = os.path.expanduser(str(profile))
    if not profile or not os.path.isfile(profile):
        raise FileNotFoundError("fit_dammif requires input_paths['profile']")

    profile = os.path.normpath(os.path.abspath(profile))
    if user_gnom_path:
        gnom_path = os.path.normpath(os.path.abspath(os.path.expanduser(str(user_gnom_path))))
    else:
        gnom_path = _gnom_path_from_fit_distances(profile, output_dir, event_bus)

    if not os.path.isfile(gnom_path):
        raise FileNotFoundError(f"fit_dammif gnom_path not found after resolve: {gnom_path}")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "DAMMIF fit…"})
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(gnom_path))[0])
    os.makedirs(output_dir, exist_ok=True)
    for i in range(1, int(dammif_reps_num) + 1):
        # `cwd=output_dir` means DAMMIF prefixes should be relative,
        # otherwise it may attempt to write to output_dir/output_dir/...
        dammif_prefix = f"dammif-{i}"
        proc = subprocess.run(
            # Autosaxs GNOM/DATGNOM .out files use q in nm^-1 and lengths in nm.
            # DAMMIF --unit=UNKNOWN guesses from s-range and often picks ANGSTROM for
            # moderate-q curves, which makes Dmax ~10× too small ("initial shape
            # dimensions too small, check units"). Explicit NANOMETRE matches our convention.
            # ATSAS mode names are case-sensitive on some builds (FAST/SLOW/INTERACTIVE).
            [
                "dammif",
                f"--prefix={dammif_prefix}",
                "--mode=FAST",
                "--unit=NANOMETRE",
                str(gnom_path),
            ],
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
    for i in range(int(dammif_reps_num)):
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

        # DAMMIF always reports sExp in Å^-1 in .fir (internal unit), even when
        # --unit=NANOMETRE was used for the GNOM input. Convert to nm^-1 for autosaxs.
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
            try:
                r_pr, p_pr = pddf_from_dammif_atoms(atoms)
                save_pddf_dat(os.path.join(output_dir, f"{rep_tag}_pr.dat"), r_pr, p_pr)
                save_pddf_png(
                    os.path.join(output_dir, f"{rep_tag}_pr.png"),
                    r_pr,
                    p_pr,
                    title=f"DAMMIF {rep_tag} p(r)",
                )
            except Exception as exc:
                if event_bus:
                    event_bus.publish(
                        EventType.MESSAGE,
                        {"text": f"fit_dammif: p(r) not written for {rep_tag!r} ({exc})"},
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
    from autosaxs.core.report_fragments import write_skill_report_fragments

    md_parts = ["### ATSAS DAMMIF\n"]
    if os.path.isfile(dammif_fits_png):
        md_parts.append(f"![Fits comparison]({os.path.basename(dammif_fits_png)})\n")
    summary_refs_d: List[Dict[str, Any]] = []
    if os.path.isfile(dammif_fits_yml):
        summary_refs_d.append({"role": "dammif_fits_yml", "path": os.path.basename(dammif_fits_yml), "format": "text"})
    if os.path.isfile(dammif_fits_csv):
        summary_refs_d.append(
            {
                "role": "dammif_fits_csv_preview",
                "path": os.path.basename(dammif_fits_csv),
                "format": "csv",
                "row": 0,
                "columns": ["q", "exp"],
            }
        )
    write_skill_report_fragments(
        output_dir,
        base,
        "fit_dammif",
        "".join(md_parts),
        summary_references=summary_refs_d or None,
        write_summary_yaml=bool(summary_refs_d),
    )
    return {"output_subdir": output_dir}

