# `autosaxs calc-profile` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs calc-profile ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs calc-profile ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This procedure wraps the `autosaxs calc-profile` CLI command / `autosaxs.skill.calc_profile` Python entry point.

## When to use me

- You want to run `autosaxs calc-profile` on SAXS data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. Run **`/path/to/myenv/bin/autosaxs calc-profile …`** (or `autosaxs calc-profile …` when the right env is active), or call the Python function.
3. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs calc-profile …`**.
- If you know the correct env is active on `PATH`, **`autosaxs calc-profile …`** is fine.
- Prefer the Python API (`autosaxs.skill.calc_profile`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

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
