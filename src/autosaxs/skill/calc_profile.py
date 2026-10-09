from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Union

import numpy as np

from .common import (
    ConfigPathExpressionArg,
    DatPathExpressionArg,
    PathExpressionArg,
    coerce_dat_path_expression,
    coerce_path_expression,
    expand_files_from_unwrapped,
)
from .deps import (
    EventBus,
    EventType,
    _strip_sub_int_prefix,
    apply_batch,
    ensure_q_nm,
    load_saxs_1d_any,
    require_atsas,
    run_with_cache,
    write_saxs,
    write_saxs_atsas_format,
)

_STRUCTURE_EXTS = (".pdb", ".cif", ".ent", ".xyz")
_CRYSOL_NATIVE_EXTS = (".pdb", ".cif", ".ent")


def calc_profile(
    structure: PathExpressionArg,
    output_dir: str = ".",
    *,
    profile: Optional[DatPathExpressionArg] = None,
    config_path: Optional[ConfigPathExpressionArg] = None,
    n_points: int = 101,
    smax_nm: float = 5.0,
    lm: int = 20,
    constant: bool = False,
    use_cache: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    """
    SAXS / small-angle x-ray scattering: compute a theoretical I(q) from an atomic model via ATSAS CRYSOL.

    Accepts `.pdb` / `.cif` / `.ent` directly. For `.xyz`, writes a pseudo-PDB (element columns +
    `--implicit-hydrogen=0`) and then runs CRYSOL. Optional experimental `.dat` enables CRYSOL fit
    mode (`--units=2`, nm⁻¹).

    Output profile `.dat` uses autosaxs convention: **q in nm⁻¹**. CRYSOL subprocess I/O stays in
    Å / Å⁻¹ at the boundary (``smax`` is converted from ``smax_nm``).

    ### Arguments

    - `structure` (str): Path expression for atomic coordinate file(s) (`.pdb`/`.cif`/`.ent`/`.xyz`).
      Directories expand non-recursively to those extensions.
    - `output_dir` (str, default `.`): Directory where outputs are written.
    - `profile` (str | None, default `None`): Optional experimental 1D `.dat` for CRYSOL fit mode.
    - `config_path` (str | None, default `None`): Deprecated. Unused (reserved for skill-keyed config).
    - `n_points` (int, default `101`): CRYSOL `--ns` (number of calculated points).
    - `smax_nm` (float, default `5.0`): Maximum q in **nm⁻¹** (converted to Å⁻¹ for CRYSOL `--smax`).
    - `lm` (int, default `20`): Number of spherical harmonics (`--lm`).
    - `constant` (bool, default `False`): Enable CRYSOL constant subtraction when fitting.
    - `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

    ### Returns

    `dict[str, str]` with:

    - `profile_path`: Theoretical I(q) as autosaxs `.dat` (q in nm⁻¹).
    - `int_path`: Raw CRYSOL `.int` (q in Å⁻¹).
    - `abs_path`: Raw CRYSOL `.abs` when present, else empty.
    - `log_path`: CRYSOL `.log`.
    - `fit_path`: Autosaxs-style fit comparison `.dat` when `profile` was given, else empty.
    - `fit_plot_path`: Overlay PNG when fitting, else empty.
    - `structure_used_path`: Coordinate file actually passed to CRYSOL (pseudo-PDB for `.xyz`).
    - `output_subdir`: Output directory for this sample.

    Requires ATSAS CRYSOL on `PATH` (see `autosaxs doctor`).

    ### Python usage

    ```python
    from autosaxs.skill import calc_profile

    out = calc_profile("model.pdb", output_dir="crysol_out", smax_nm=5.0)
    print(out["profile_path"])

    out_xyz = calc_profile("cluster.xyz", output_dir="crysol_xyz")
    out_fit = calc_profile("model.pdb", output_dir="crysol_fit", profile="exp.dat")
    ```

    ### CLI usage

    ```bash
    autosaxs calc-profile model.pdb --output-dir crysol_out --smax-nm 5.0
    autosaxs calc-profile cluster.xyz -o crysol_xyz
    autosaxs calc-profile model.pdb --profile exp.dat -o crysol_fit
    ```
    """
    _ = config_path
    bus = EventBus()
    bus.subscribe(EventType.MESSAGE, lambda data: print((data or {}).get("text", ""), file=sys.stdout))

    structure_pe = coerce_path_expression(structure)
    # Unwrap then filter by allowed structure extensions (dirs need explicit expansion).
    raw_items = structure_pe.unwrap()
    expanded: List[str] = []
    for item in raw_items:
        if os.path.isdir(item):
            expanded.extend(
                str(p)
                for p in sorted(Path(item).iterdir())
                if p.is_file() and p.suffix.lower() in _STRUCTURE_EXTS
            )
        elif os.path.isfile(item):
            if Path(item).suffix.lower() not in _STRUCTURE_EXTS:
                raise ValueError(
                    f"calc_profile: structure must be {_STRUCTURE_EXTS}, got {Path(item).suffix!r}"
                )
            expanded.append(item)
    if not expanded:
        raise FileNotFoundError("calc_profile: no structure files found after expansion")

    exp_path: Optional[str] = None
    if profile is not None:
        pe = coerce_dat_path_expression(profile)
        dats = expand_files_from_unwrapped(pe.unwrap(), kind="1d_dat")
        if len(dats) != 1:
            raise ValueError(
                f"calc_profile: profile must resolve to exactly one .dat when provided, got {len(dats)}"
            )
        exp_path = dats[0]

    input_batch = [{"structure": p} for p in expanded]
    return _calc_profile_paths(
        input_paths=input_batch[0] if len(input_batch) == 1 else input_batch,
        output_dir=output_dir,
        event_bus=bus,
        use_cache=use_cache,
        experimental_profile=exp_path,
        n_points=int(n_points),
        smax_nm=float(smax_nm),
        lm=int(lm),
        constant=bool(constant),
    )


def xyz_to_pseudo_pdb(xyz_path: str, pdb_path: str) -> str:
    """
    Convert `.xyz` to a minimal PDB CRYSOL can read.

    Writes element symbols into the atom-name and element columns; callers should pass
    ``--implicit-hydrogen=0`` to CRYSOL for these pseudo-PDBs.
    """
    try:
        from ase.io import read as ase_read
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("calc_profile .xyz support requires ase (declared autosaxs dependency)") from exc

    atoms = ase_read(str(xyz_path))
    lines: List[str] = []
    for i, atom in enumerate(atoms, start=1):
        el = str(atom.symbol)
        aname = (el + "   ")[:4]
        x, y, z = (float(v) for v in atom.position)
        lines.append(
            f"ATOM  {i:5d} {aname:4s} MOL A{i:4d}    "
            f"{x:8.3f}{y:8.3f}{z:8.3f}  1.00 20.00          {el:>2s}"
        )
    lines.append("END")
    Path(pdb_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pdb_path


def _parse_crysol_table(path: str, *, min_cols: int = 2) -> np.ndarray:
    """Parse numeric rows from a CRYSOL `.int` / `.fit` / `.abs` text table."""
    rows: List[List[float]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.split()
            if len(parts) < min_cols:
                continue
            try:
                vals = [float(x) for x in parts[: max(min_cols, len(parts))]]
            except ValueError:
                continue
            if len(vals) >= min_cols:
                rows.append(vals)
    if not rows:
        raise ValueError(f"No numeric data rows in CRYSOL output: {path}")
    # Ragged → pad to common width
    width = max(len(r) for r in rows)
    arr = np.full((len(rows), width), np.nan, dtype=float)
    for i, r in enumerate(rows):
        arr[i, : len(r)] = r
    return arr


def _parse_rg_angstrom_from_log(log_text: str) -> Optional[float]:
    """Best-effort Rg [Å] from CRYSOL log (prefer slope / net intensity line)."""
    patterns = (
        r"Rg from the slope of net intensity \[A\]\s*\.+\s*:\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)",
        r"Rg \(Atoms - Excluded volume \+ Shell\) \[A\]\s*\.+\s*:\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)",
        r"Electron Rg \[A\]\s*\.+\s*:\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)",
    )
    for pat in patterns:
        m = re.search(pat, log_text)
        if m:
            return float(m.group(1))
    return None


def _require_crysol() -> str:
    path = shutil.which("crysol")
    if not path:
        raise RuntimeError(
            "calc_profile requires ATSAS CRYSOL on PATH (`crysol` not found). "
            "Install ATSAS 3.2.1 and re-run `autosaxs doctor`."
        )
    return path


@apply_batch(stem_from_keys="structure", per_sample_subdir="always")
@require_atsas
@run_with_cache(
    path_keys_for_hash=["structure"],
    kwargs_for_hash_keys=["experimental_profile", "n_points", "smax_nm", "lm", "constant"],
    include_config_in_hash=False,
    warn_if_no_cache=True,
)
def _calc_profile_paths(
    input_paths: Dict[str, Union[str, List[str]]],
    output_dir: str,
    config: Optional[Dict] = None,
    event_bus: Optional[EventBus] = None,
    use_cache: bool = False,
    sample_index: int = 0,
    experimental_profile: Optional[str] = None,
    n_points: int = 101,
    smax_nm: float = 5.0,
    lm: int = 20,
    constant: bool = False,
) -> Dict[str, Union[str, List[str]]]:
    _ = config, use_cache, sample_index
    _require_crysol()

    structure = input_paths.get("structure")
    if isinstance(structure, list):
        structure = structure[0] if structure else None
    structure = os.path.normpath(os.path.abspath(os.path.expanduser(str(structure or ""))))
    if not structure or not os.path.isfile(structure):
        raise FileNotFoundError("calc_profile requires input_paths['structure']")

    suffix = Path(structure).suffix.lower()
    if suffix not in _STRUCTURE_EXTS:
        raise ValueError(f"calc_profile: unsupported structure extension {suffix!r}")

    os.makedirs(output_dir, exist_ok=True)
    base = _strip_sub_int_prefix(os.path.splitext(os.path.basename(structure))[0])
    if event_bus:
        event_bus.publish(EventType.MESSAGE, {"text": f"calc_profile: CRYSOL on {os.path.basename(structure)}…"})

    structure_for_crysol = structure
    xyz_converted = False
    if suffix == ".xyz":
        pseudo = os.path.join(output_dir, f"{base}_from_xyz.pdb")
        xyz_to_pseudo_pdb(structure, pseudo)
        structure_for_crysol = pseudo
        xyz_converted = True
    elif suffix not in _CRYSOL_NATIVE_EXTS:
        raise ValueError(f"calc_profile: unsupported structure extension {suffix!r}")

    if smax_nm <= 0:
        raise ValueError("calc_profile: smax_nm must be > 0")
    smax_A = float(smax_nm) / 10.0  # nm^-1 → Å^-1
    if smax_A > 2.0:
        raise ValueError(
            f"calc_profile: smax_nm={smax_nm} → smax={smax_A:.3f} Å^-1 exceeds CRYSOL max 2.0 Å^-1"
        )

    prefix = "crysol"
    cmd: List[str] = [
        "crysol",
        f"--ns={int(n_points)}",
        f"--smax={smax_A:.6g}",
        f"--lm={int(lm)}",
        "-p",
        prefix,
    ]
    if xyz_converted:
        cmd.append("--implicit-hydrogen=0")

    exp_atsas: Optional[str] = None
    if experimental_profile:
        exp_abs = os.path.normpath(os.path.abspath(os.path.expanduser(str(experimental_profile))))
        if not os.path.isfile(exp_abs):
            raise FileNotFoundError(f"calc_profile: experimental profile not found: {exp_abs}")
        q, I, sigma = load_saxs_1d_any(exp_abs)
        q, I, sigma = ensure_q_nm(q, I, sigma)
        exp_atsas = os.path.join(output_dir, f"{base}_exp_atsas.dat")
        write_saxs_atsas_format(exp_atsas, q, I, sigma)
        cmd.append("--units=2")  # experimental q in nm^-1
        if constant:
            cmd.append("--constant")
        cmd.append(structure_for_crysol)
        cmd.append(os.path.basename(exp_atsas))
    else:
        cmd.append(structure_for_crysol)

    proc = subprocess.run(cmd, cwd=output_dir, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "calc_profile failed: crysol exited with code "
            f"{proc.returncode}\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )

    int_path = os.path.join(output_dir, f"{prefix}.int")
    log_path = os.path.join(output_dir, f"{prefix}.log")
    abs_path = os.path.join(output_dir, f"{prefix}.abs")
    raw_fit = os.path.join(output_dir, f"{prefix}.fit")

    if not os.path.isfile(int_path):
        raise RuntimeError(f"calc_profile: CRYSOL did not write {int_path}")

    table = _parse_crysol_table(int_path, min_cols=2)
    q_A = table[:, 0]
    I_calc = table[:, 1]
    q_nm = q_A * 10.0
    profile_path = os.path.join(output_dir, f"{base}_crysol.dat")
    write_saxs(
        profile_path,
        q_nm,
        I_calc,
        None,
        {
            "type": "calc_profile",
            "source": "crysol",
            "structure": structure,
            "structure_used": structure_for_crysol,
            "smax_nm": float(smax_nm),
            "n_points": int(n_points),
        },
    )

    fit_path = ""
    fit_plot_path = ""
    if experimental_profile and os.path.isfile(raw_fit):
        fit_tab = _parse_crysol_table(raw_fit, min_cols=4)
        # CRYSOL .fit: s[Å^-1], I_exp, err, I_fit
        q_fit_nm = fit_tab[:, 0] * 10.0
        I_exp = fit_tab[:, 1]
        sigma_fit = fit_tab[:, 2]
        I_fit = fit_tab[:, 3]
        fit_path = os.path.join(output_dir, f"{base}_crysol_fit.dat")
        write_saxs(
            fit_path,
            q_fit_nm,
            I_fit,
            sigma_fit,
            {
                "type": "calc_profile_fit",
                "I_exp_column_note": "see overlay plot; this .dat stores I_fit",
                "structure": structure,
            },
        )
        # Also write a 4-column comparison for convenience
        cmp_path = os.path.join(output_dir, f"{base}_crysol_fit_compare.dat")
        with open(cmp_path, "w", encoding="utf-8") as f:
            f.write("# q_nm^-1\tI_exp\tsigma\tI_fit\n")
            for i in range(len(q_fit_nm)):
                f.write(
                    f"{q_fit_nm[i]:.8g}\t{I_exp[i]:.8g}\t{sigma_fit[i]:.8g}\t{I_fit[i]:.8g}\n"
                )
        fit_path = cmp_path
        fit_plot_path = os.path.join(output_dir, f"{base}_crysol_fit.png")
        try:
            import matplotlib.pyplot as plt

            fig, ax = plt.subplots()
            ax.plot(q_fit_nm, I_exp, "k-", lw=0.8, label="experiment")
            ax.plot(q_fit_nm, I_fit, "-", lw=1.4, alpha=0.8, label="CRYSOL fit")
            ax.set_yscale("log")
            ax.set_xlabel(r"$q$ (nm$^{-1}$)")
            ax.set_ylabel(r"$I(q)$ (a.u.)")
            ax.set_title(f"CRYSOL fit: {base}")
            ax.legend(loc="best")
            ax.grid(True, alpha=0.3)
            fig.tight_layout()
            fig.savefig(fit_plot_path, dpi=150, bbox_inches="tight")
            plt.close(fig)
        except Exception as exc:
            if event_bus:
                event_bus.publish(EventType.MESSAGE, {"text": f"calc_profile: fit plot skipped ({exc})"})
            fit_plot_path = ""

    if event_bus and os.path.isfile(log_path):
        log_text = Path(log_path).read_text(encoding="utf-8", errors="replace")
        rg_A = _parse_rg_angstrom_from_log(log_text)
        if rg_A is not None:
            event_bus.publish(
                EventType.MESSAGE,
                {"text": f"calc_profile: Rg ≈ {rg_A / 10.0:.3f} nm (from CRYSOL log)"},
            )

    return {
        "profile_path": profile_path,
        "int_path": int_path,
        "abs_path": abs_path if os.path.isfile(abs_path) else "",
        "log_path": log_path if os.path.isfile(log_path) else "",
        "fit_path": fit_path,
        "fit_plot_path": fit_plot_path,
        "structure_used_path": structure_for_crysol,
        "output_subdir": output_dir,
    }
