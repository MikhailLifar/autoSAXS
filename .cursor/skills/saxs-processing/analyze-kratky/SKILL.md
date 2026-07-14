---
name: analyze-kratky
description: SAXS / small-angle x-ray scattering: dimensionless Kratky conformation analysis on a 1D profile.
catalog-hidden: true
---

# `autosaxs analyze-kratky` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs analyze-kratky ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs analyze-kratky ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This skill wraps the `autosaxs analyze-kratky` CLI command / `autosaxs.skill.analyze_kratky` Python entry point.

## When to use me

- You want to run `autosaxs analyze-kratky` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs analyze-kratky …`** (or `autosaxs analyze-kratky …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs analyze-kratky …`**.
- If you know the correct env is active on `PATH`, **`autosaxs analyze-kratky …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.analyze_kratky`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: dimensionless Kratky conformation analysis on a 1D profile.

Builds classical (I·q² vs q) and dimensionless ((q·Rg)²·I/I(0) vs q·Rg) Kratky plots,
locates the global peak, and assigns a model-free conformation class (globular / elongated /
coil / intermediate).

Unless both ``rg_nm`` and ``i0`` are supplied, runs in-process Guinier analysis to obtain them.

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where analysis outputs are written.
- `config_path` (str | None, default `None`): Optional YAML config path for CLI parity; unused by this skill.
- `rg_nm` (float | None, default `None`): Radius of gyration in nm. If omitted, taken from in-process Guinier.
- `i0` (float | None, default `None`): Forward scattering I(0). If omitted, taken from in-process Guinier.
- `q_min`, `q_max` (float | None): Optional q-range (nm⁻¹) applied before analysis.
- `globular_x_min`, `globular_x_max`, `globular_y_min`, `globular_y_max`: Globular peak bands (defaults from quality guide).
- `elongated_x_min`, `elongated_x_max`, `elongated_y_min`: Elongated peak bands.
- `coil_plateau_y`, `coil_plateau_tol`, `coil_high_x_min`: Coil / Debye-plateau detection.
- `x_search_min`, `x_search_max`: Peak search window in q·Rg.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict` with:

- `results_path`: Path to the text results file.
- `kratky_plot_path`: Path to the classical Kratky PNG (I·q² vs q).
- `kratky_dimensionless_plot_path`: Path to the dimensionless Kratky PNG.
- `kratky_classical_dat_path`: Path to classical Kratky `.dat`.
- `kratky_dimensionless_dat_path`: Path to dimensionless Kratky `.dat`.
- `classification`: Assigned conformation label.
- `x_max`, `y_max`: Dimensionless peak coordinates (q·Rg, Y).

### Python usage

```python
from autosaxs.skill import analyze_kratky

out = analyze_kratky(
    profile="subtracted/sub_sample_01.dat",
    output_dir="kratky",
    use_cache=False,
)

print(out["classification"])
```

### CLI usage

```bash
autosaxs analyze-kratky subtracted/sub_sample_01.dat --output-dir kratky
autosaxs analyze-kratky subtracted/sub_sample_01.dat --rg-nm 3.2 --i0 1.05 --output-dir kratky
```
