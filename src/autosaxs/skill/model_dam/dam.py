from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np
import pandas as pd
import yaml

from autosaxs.core.pddf import (
    pddf_from_dammif_atoms,
    save_pddf_dat,
    save_pddf_png,
)
from ..deps import (
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
from autosaxs.core.viewer import write_iq_fit_comparison_png
from ..common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    SingletonPathExpressionArg,
    coerce_dat_path_expression,
    coerce_singleton_path_expression,
    expand_files_from_unwrapped,
)

_DAMMIF_MODES = {"fast": "FAST", "slow": "SLOW"}
_BEST_CIF_NAME = "best.cif"


def _normalize_dammif_mode(dammif_mode: str) -> str:
    key = str(dammif_mode).strip().lower()
    if key not in _DAMMIF_MODES:
        allowed = ", ".join(sorted(_DAMMIF_MODES))
        raise ValueError(f"dammif_mode must be one of: {allowed} (got {dammif_mode!r})")
    return _DAMMIF_MODES[key]


def model_dam(
    profile: DatPathExpressionArg,
    output_dir: str = ".",
    *,
    config_path: Optional[ConfigPathExpressionArg] = None,
    gnom_path: Optional[SingletonPathExpressionArg] = None,
    n_runs: int = 1,
    dammif_mode: str = "fast",
    visualize_all: bool = False,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    SAXS / small-angle x-ray scattering: ab initio bead-model shape reconstruction with ATSAS DAMMIF, optionally followed by DAMAVER ensemble averaging (shape reconstruction / bead model / occupancy map). When no GNOM `.out` is supplied, `fit_distances` is run in-process to obtain one.

    With `n_runs=1`, runs a single DAMMIF reconstruction. With `n_runs>1`, runs independent DAMMIF replicas then DAMAVER (NSD alignment, outlier rejection, frequency/occupancy map). The data-fitting final shape is the most probable DAMMIF replica (`best.cif` symlink); the DAMAVER frequency map is the stability product. DAMMIN refinement is not performed.

    ### Arguments

    - `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
    - `output_dir` (str, default `.`): Directory where DAMMIF / DAMAVER outputs are written.
    - `gnom_path` (str | None, default `None`): Optional path to a GNOM/DATGNOM `.out` file for DAMMIF. If omitted, `fit_distances` is run in-process on `profile` and its `best_gnom_out_path` is used.
    - `n_runs` (int, default `1`): Number of independent DAMMIF runs. When `>1`, DAMAVER is run on the particle models.
    - `dammif_mode` (str, default `fast`): DAMMIF annealing mode: `fast` or `slow`.
    - `visualize_all` (bool, default `False`): When True, write PNGs/GIFs under `{output}/visuals/` (synced per-run rotation GIFs, overlap, occupancy threshold; nm scale bar; no run/title captions).
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    ### Short parameter list

    - n_runs: 1 for fast pilot view; 5, 10 or 20 - for reliable averaged shape
    - dammif_mode: FAST or SLOW, default FAST; recommended not to change the default
    - visualize_all: Heavy visualization with nice GIF's. Not fast, rather production level artifacts

    ### Returns

    `dict[str, str | list[str]]` with:

    - `output_subdir`: Directory containing DAMMIF fit artifacts (FIR/CIF and summary files). Each replica also gets `{rep}_pr.dat` and `{rep}_pr.png` (GNOM-style p(r) from DAM bead pairs via Monte Carlo).
    - `best_cif_path`: Symlink `best.cif` pointing at the most probable particle CIF (the sole run when `n_runs=1`).
    - `best_view_path`: Path to ``best_view.png`` (isosurface + fit overlay for the best model); empty if unavailable.
    - `frequency_map_path`: Path to the DAMAVER frequency/occupancy map CIF (empty string when `n_runs=1`).
    - `visuals_dir`, `overlap_png`, `overlap_gif`, `occupancy_png`, `occupancy_gif`, `occupancy_thresholds_png`, `run_gifs` when `visualize_all=True` (empty strings / empty list otherwise).

    ### Python usage

    ```python
    from autosaxs.skill import model_dam

    out = model_dam(
        profile="subtracted/sub_sample_01.dat",
        output_dir="dammif",
        gnom_path="guinier/sample_01_gnom.out",
        n_runs=1,
        dammif_mode="fast",
        visualize_all=False,
        use_cache=False,
    )

    print(out["output_subdir"], out["best_cif_path"])
    ```

    ### CLI usage

    ```bash
    autosaxs model-dam subtracted/sub_sample_01.dat --output-dir dammif --n-runs 1 --dammif-mode fast
    autosaxs model-dam subtracted/sub_sample_01.dat --output-dir dammif --n-runs 5 --visualize-all
    ```
    """
    _ = config_path
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))
    if int(n_runs) < 1:
        raise ValueError("n_runs must be >= 1")
    mode_atsas = _normalize_dammif_mode(dammif_mode)
    profile = coerce_dat_path_expression(profile)
    expanded_profiles = expand_files_from_unwrapped(profile.unwrap(), kind="1d_dat")
    for p in expanded_profiles:
        if Path(p).suffix.lower() != ".dat":
            raise ValueError("model_dam input files must have .dat extension")
    input_batch: List[Dict[str, Union[str, List[str]]]] = [{"profile": p} for p in expanded_profiles]
    if gnom_path is not None:
        gnom_expr = coerce_singleton_path_expression(gnom_path)
        gnom_single = gnom_expr.unwrap()[0]
        for inp in input_batch:
            inp["gnom_path"] = gnom_single
    return _model_dam_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        n_runs=int(n_runs),
        dammif_mode=mode_atsas,
        visualize_all=bool(visualize_all),
        event_bus=bus,
        use_cache=use_cache,
    )


def _gnom_path_from_fit_distances(
    profile: str,
    output_dir: str,
    event_bus: Optional[EventBus],
) -> str:
    """Run fit_distances in-process and return the selected DATGNOM ``.out`` path."""
    from ..fit_distances import _fit_distances_paths

    distances_dir = os.path.join(output_dir, "_fit_distances_for_dammif")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "model_dam: running fit_distances (in-process)…"})
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
    from ..gnom_fit_common import failure_message_from_result, is_atsas_fit_ok

    if not is_atsas_fit_ok(result):
        raise RuntimeError(
            failure_message_from_result(result, skill_id="fit_distances")
            or "model_dam: fit_distances did not produce a GNOM .out; cannot run DAMMIF."
        )
    gnom_path = result.get("best_gnom_out_path")
    gnom_path = os.path.normpath(os.path.abspath(os.path.expanduser(str(gnom_path))))
    if not os.path.isfile(gnom_path):
        raise RuntimeError(f"model_dam: GNOM .out not found after resolve: {gnom_path}")
    if event_bus:
        event_bus.publish(
            EventType.MESSAGE,
            {"text": f"model_dam: fit_distances completed (gnom={os.path.basename(str(gnom_path))})."},
        )
    return str(gnom_path)


def _symlink_best_cif(output_dir: str, target_cif: str) -> str:
    """Create ``best.cif`` in ``output_dir`` as a relative symlink to ``target_cif``."""
    target_abs = os.path.normpath(os.path.abspath(target_cif))
    if not os.path.isfile(target_abs):
        raise FileNotFoundError(f"model_dam: cannot symlink best.cif; missing {target_abs}")
    link_path = os.path.join(output_dir, _BEST_CIF_NAME)
    rel_target = os.path.relpath(target_abs, start=os.path.abspath(output_dir))
    if os.path.lexists(link_path):
        os.unlink(link_path)
    try:
        os.symlink(rel_target, link_path)
    except OSError as exc:
        raise RuntimeError(
            f"model_dam: failed to create symlink {link_path!r} -> {rel_target!r}: {exc}"
        ) from exc
    return os.path.normpath(os.path.abspath(link_path))


def _particle_cif_paths(output_dir: str, n_runs: int) -> List[str]:
    paths: List[str] = []
    for i in range(1, int(n_runs) + 1):
        cif = os.path.join(output_dir, f"dammif-{i}-1.cif")
        if os.path.isfile(cif):
            paths.append(os.path.normpath(os.path.abspath(cif)))
    return paths


def _parse_damaver_most_probable(summary_path: str, particle_cifs: List[str]) -> Optional[str]:
    """Return absolute path of the most probable particle CIF from a DAMAVER summary, if found."""
    if not summary_path or not os.path.isfile(summary_path):
        return None
    text = Path(summary_path).read_text(encoding="utf-8", errors="replace")
    basenames = {os.path.basename(p): p for p in particle_cifs}
    # Prefer explicit "most probable" / "representative" / "most typical" mentions.
    for pat in (
        r"(?i)most\s+probable[^\n]*?([A-Za-z0-9_.\-]+\.(?:cif|pdb))",
        r"(?i)most\s+representative[^\n]*?([A-Za-z0-9_.\-]+\.(?:cif|pdb))",
        r"(?i)most\s+typical[^\n]*?([A-Za-z0-9_.\-]+\.(?:cif|pdb))",
        r"(?i)representative\s+model[^\n]*?([A-Za-z0-9_.\-]+\.(?:cif|pdb))",
    ):
        m = re.search(pat, text)
        if m:
            name = os.path.basename(m.group(1))
            if name in basenames:
                return basenames[name]
            # Summary may list dammif-1.cif while particle is dammif-1-1.cif
            stem = Path(name).stem
            for bn, full in basenames.items():
                if bn.startswith(stem) or stem in bn:
                    return full
    # First "Include" line naming a known particle model.
    for line in text.splitlines():
        if re.search(r"(?i)\binclude\b", line):
            for bn, full in basenames.items():
                if bn in line or Path(bn).stem in line:
                    return full
    return None


def _lowest_chi2_particle_cif(output_dir: str, particle_cifs: List[str]) -> Optional[str]:
    yml_path = os.path.join(output_dir, "dammif_fits.yml")
    if not os.path.isfile(yml_path) or not particle_cifs:
        return particle_cifs[0] if particle_cifs else None
    with open(yml_path, "r", encoding="utf-8") as f:
        fits = yaml.safe_load(f) or {}
    best_key = None
    best_chi2 = None
    for key, meta in fits.items():
        if not isinstance(meta, dict):
            continue
        chi2 = meta.get("chi2")
        if chi2 is None:
            continue
        chi2_f = float(chi2)
        if best_chi2 is None or chi2_f < best_chi2:
            best_chi2 = chi2_f
            best_key = str(key)
    if best_key is None:
        return particle_cifs[0]
    candidate = os.path.join(output_dir, f"{best_key}-1.cif")
    if os.path.isfile(candidate):
        return os.path.normpath(os.path.abspath(candidate))
    return particle_cifs[0]


def _replica_tag_from_particle_cif(cif_path: str) -> Optional[str]:
    """``…/dammif-3-1.cif`` → ``dammif-3``."""
    name = os.path.basename(cif_path)
    m = re.match(r"(dammif-\d+)-1\.cif$", name, re.IGNORECASE)
    return m.group(1) if m else None


def _write_best_model_view(
    output_dir: str,
    *,
    best_cif_path: str,
    best_target: str,
    gnom_path: str,
    event_bus: Optional[EventBus] = None,
) -> str:
    """
    Write ``best_view.png``: isosurface views of the selected best particle model
    plus the DAMMIF fit overlay (same layout as per-replica ``dammif-N_view.png``).
    """
    from autosaxs.core.utils import read_bodies_cif

    best_view_path = os.path.join(output_dir, "best_view.png")
    atoms = read_bodies_cif(best_cif_path)
    if atoms is None:
        atoms = read_bodies_cif(best_target)
    if atoms is None:
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": "model_dam: best_view.png not written (could not read best CIF)."},
            )
        return ""

    rep = _replica_tag_from_particle_cif(best_target)
    fir_path = os.path.join(output_dir, f"{rep}.fir") if rep else ""
    q_fit: Optional[np.ndarray] = None
    I_exp: Optional[np.ndarray] = None
    sigma_fit: Optional[np.ndarray] = None
    I_fit: Optional[np.ndarray] = None
    if fir_path and os.path.isfile(fir_path):
        data = np.loadtxt(fir_path, skiprows=1, dtype=np.float64)
        q_fit = data[:, 0]
        I_exp = data[:, 1]
        sigma_fit = data[:, 2]
        I_fit = data[:, 3]
        if str(gnom_path).lower().endswith(".out"):
            q_fit = q_fit * 10.0

    if q_fit is None or I_exp is None or I_fit is None:
        # Fallback: reuse an existing per-replica view if present.
        if rep:
            existing = os.path.join(output_dir, f"{rep}_view.png")
            if os.path.isfile(existing):
                import shutil

                shutil.copy2(existing, best_view_path)
                return best_view_path
        if event_bus:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": "model_dam: best_view.png not written (missing FIR for best replica)."},
            )
        return ""

    PLTViewer.plot_3d_views_and_scattering(
        atoms,
        q_fit,
        I_exp,
        sigma_fit,
        I_fit,
        plotFilePath=best_view_path,
    )
    return best_view_path if os.path.isfile(best_view_path) else ""


def _run_damaver(
    output_dir: str,
    particle_cifs: List[str],
    event_bus: Optional[EventBus],
) -> Tuple[str, Optional[str], str]:
    """
    Run ATSAS damaver on particle CIFs.

    Returns (frequency_map_path, summary_path_or_None, damaver_dir).
    """
    if shutil.which("damaver") is None:
        raise RuntimeError(
            "model_dam: ATSAS `damaver` executable not found on PATH "
            "(required when n_runs > 1)."
        )
    if len(particle_cifs) < 2:
        raise RuntimeError(
            f"model_dam: DAMAVER needs at least 2 particle CIFs; found {len(particle_cifs)}"
        )
    damaver_dir = os.path.join(output_dir, "damaver")
    os.makedirs(damaver_dir, exist_ok=True)
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": "model_dam: running DAMAVER…"})
    # Absolute paths so cwd can be damaver_dir for tidy outputs.
    cmd = [
        "damaver",
        "--prefix=damaver",
        "--method=nsd",
        *particle_cifs,
    ]
    proc = subprocess.run(
        cmd,
        cwd=damaver_dir,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"model_dam failed: damaver exited with code {proc.returncode}\n{proc.stderr}"
        )

    frequency_map = ""
    for name in (
        "damaver-global-damaver.cif",
        "damaver-global-damaver.pdb",
        "damaver.cif",
        "damaver.pdb",
    ):
        cand = os.path.join(damaver_dir, name)
        if os.path.isfile(cand):
            frequency_map = os.path.normpath(os.path.abspath(cand))
            break
    if not frequency_map:
        # Glob any *-damaver.cif produced under damaver_dir
        matches = sorted(Path(damaver_dir).glob("*damaver*.cif")) + sorted(
            Path(damaver_dir).glob("*damaver*.pdb")
        )
        # Prefer names containing 'global-damaver' then plain 'damaver' without damfilt/damstart
        preferred = [
            p
            for p in matches
            if "damfilt" not in p.name.lower()
            and "damstart" not in p.name.lower()
            and "damaver" in p.name.lower()
        ]
        if preferred:
            frequency_map = str(preferred[0].resolve())
    if not frequency_map:
        raise RuntimeError(
            f"model_dam: DAMAVER finished but no frequency/occupancy map was found under {damaver_dir}"
        )

    summary_path = None
    for name in ("damaver-global-summary.txt", "damaver-summary.txt"):
        cand = os.path.join(damaver_dir, name)
        if os.path.isfile(cand):
            summary_path = cand
            break
    if summary_path is None:
        summaries = sorted(Path(damaver_dir).glob("*summary*.txt"))
        if summaries:
            summary_path = str(summaries[0])

    return frequency_map, summary_path, damaver_dir


@apply_batch(stem_from_keys="profile", per_sample_subdir="always")
@run_with_cache(
    path_keys_for_hash=["profile", "gnom_path"],
    kwargs_for_hash=None,
    kwargs_for_hash_keys=["n_runs", "dammif_mode", "visualize_all"],
    include_config_in_hash=False,
    warn_if_no_cache=True,
)
def _model_dam_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    n_runs: int = 1,
    dammif_mode: str = "FAST",
    visualize_all: bool = False,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
    if int(n_runs) < 1:
        raise ValueError("n_runs must be >= 1")
    # Accept already-normalized ATSAS tokens or user-facing names.
    mode_atsas = (
        dammif_mode
        if str(dammif_mode).upper() in ("FAST", "SLOW")
        else _normalize_dammif_mode(str(dammif_mode))
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
        raise FileNotFoundError("model_dam requires input_paths['profile']")

    profile = os.path.normpath(os.path.abspath(profile))
    if user_gnom_path:
        gnom_path = os.path.normpath(os.path.abspath(os.path.expanduser(str(user_gnom_path))))
    else:
        gnom_path = _gnom_path_from_fit_distances(profile, output_dir, event_bus)

    if not os.path.isfile(gnom_path):
        raise FileNotFoundError(f"model_dam gnom_path not found after resolve: {gnom_path}")
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": f"DAMMIF fit ({mode_atsas}, n_runs={int(n_runs)})…"})
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(gnom_path))[0])
    os.makedirs(output_dir, exist_ok=True)
    for i in range(1, int(n_runs) + 1):
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
                f"--mode={mode_atsas}",
                "--unit=NANOMETRE",
                str(gnom_path),
            ],
            cwd=output_dir,
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"model_dam failed: dammif exited with code {proc.returncode}\n{proc.stderr}")

    profile_1d = profile or gnom_path
    q, I, sigma, _ = read_saxs(profile_1d)
    # Autosaxs pipeline convention is q in nm^-1; incoming files may be in Å^-1.
    q, I, sigma = ensure_q_nm(q, I, sigma)
    fits_data = []
    # Reference "experimental" curve for the comparison plot/CSV.
    # If DAMMIF is given a GNOM .out, its .fir contains the exact curve DAMMIF fitted (often scaled/regularized),
    # which may not be on the same intensity scale as the original .dat.
    exp_ref: Optional[tuple[np.ndarray, np.ndarray, Optional[np.ndarray]]] = None
    for i in range(int(n_runs)):
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
                        {"text": f"model_dam: p(r) not written for {rep_tag!r} ({exc})"},
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

        # Prefer lowest-chi2 replica as the residual reference.
        primary_index = 0
        best_chi2 = float("inf")
        fit_curves = []
        for idx, (rep_tag, descr, q_int, I_fit_interp) in enumerate(fits_data):
            chi2 = float(descr.get("chi2", float("nan")))
            label = f"{rep_tag}; $\\chi^2$: {chi2:.2f}"
            fit_curves.append((np.interp(q_exp, q_int, I_fit_interp), label))
            if np.isfinite(chi2) and chi2 < best_chi2:
                best_chi2 = chi2
                primary_index = idx
        write_iq_fit_comparison_png(
            dammif_fits_png,
            q_exp,
            I_exp,
            fit_curves,
            sigma=sigma_exp,
            title=f"Fits comparison for {base}",
            primary_index=primary_index,
        )

    particle_cifs = _particle_cif_paths(output_dir, int(n_runs))
    frequency_map_path = ""
    best_target: Optional[str] = None

    if int(n_runs) > 1:
        frequency_map_path, summary_path, _damaver_dir = _run_damaver(
            output_dir, particle_cifs, event_bus
        )
        best_target = _parse_damaver_most_probable(summary_path or "", particle_cifs)
        if best_target is None:
            best_target = _lowest_chi2_particle_cif(output_dir, particle_cifs)
    else:
        best_target = particle_cifs[0] if particle_cifs else None

    if best_target is None:
        raise RuntimeError("model_dam: no particle CIF available to create best.cif symlink")

    best_cif_path = _symlink_best_cif(output_dir, best_target)

    best_view_path = _write_best_model_view(
        output_dir,
        best_cif_path=best_cif_path,
        best_target=best_target,
        gnom_path=str(gnom_path),
        event_bus=event_bus,
    )

    from autosaxs.core.report_fragments import write_skill_report_fragments

    md_parts = ["### ATSAS DAMMIF / DAMAVER\n"]
    if best_view_path and os.path.isfile(best_view_path):
        md_parts.append(f"![Best DAMMIF model]({os.path.basename(best_view_path)})\n")
    if os.path.isfile(dammif_fits_png):
        md_parts.append(f"![Fits comparison]({os.path.basename(dammif_fits_png)})\n")
    if frequency_map_path:
        md_parts.append(f"- Frequency/occupancy map: `{os.path.relpath(frequency_map_path, output_dir)}`\n")
    md_parts.append(f"- Best model (symlink): `{_BEST_CIF_NAME}` → `{os.path.basename(best_target)}`\n")
    summary_refs_d: List[Dict[str, Any]] = []
    if os.path.isfile(dammif_fits_yml):
        summary_refs_d.append({"role": "dammif_fits_yml", "path": os.path.basename(dammif_fits_yml), "format": "text"})
    if best_view_path and os.path.isfile(best_view_path):
        summary_refs_d.append(
            {"role": "best_view", "path": os.path.basename(best_view_path), "format": "png"}
        )
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
        "model_dam",
        "".join(md_parts),
        summary_references=summary_refs_d or None,
        write_summary_yaml=bool(summary_refs_d),
    )
    out: Dict[str, Union[str, List[str]]] = {
        "output_subdir": output_dir,
        "best_cif_path": best_cif_path,
        "best_view_path": best_view_path,
        "frequency_map_path": frequency_map_path,
        "visuals_dir": "",
        "overlap_png": "",
        "occupancy_png": "",
        "occupancy_thresholds_png": "",
        "overlap_gif": "",
        "occupancy_gif": "",
        "run_gifs": [],
    }
    if bool(visualize_all):
        from .vis import write_visuals

        vis_out = write_visuals(
            output_dir,
            best_cif_path=best_cif_path,
            frequency_map_path=frequency_map_path,
            event_bus=event_bus,
        )
        out.update(vis_out)
        vis_dir = str(vis_out.get("visuals_dir") or "").strip()
        if vis_dir:
            # Amend individual report with a short pointer (best-effort).
            try:
                md_path = Path(output_dir) / f"{base}_report_individual.md"
                if md_path.is_file():
                    with md_path.open("a", encoding="utf-8") as fh:
                        fh.write(f"\n- Visuals: `{os.path.relpath(vis_dir, output_dir)}/`\n")
            except OSError:
                pass
    return out
