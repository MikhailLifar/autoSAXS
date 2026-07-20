---
name: model-dam
description: SAXS / small-angle x-ray scattering: ab initio bead-model shape reconstruction with ATSAS DAMMIF, optionally followed by DAMAVER ensemble averaging (shape reconstruction / bead model / occupancy map). When no GNOM `.out` is supplied, `fit_distances` is run in-process to obtain one.
catalog-hidden: true
---

# `autosaxs model-dam` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs model-dam ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs model-dam ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This skill wraps the `autosaxs model-dam` CLI command / `autosaxs.skill.model_dam` Python entry point.

## When to use me

- You want to run `autosaxs model-dam` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs model-dam …`** (or `autosaxs model-dam …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs model-dam …`**.
- If you know the correct env is active on `PATH`, **`autosaxs model-dam …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.model_dam`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: ab initio bead-model shape reconstruction with ATSAS DAMMIF, optionally followed by DAMAVER ensemble averaging (shape reconstruction / bead model / occupancy map). When no GNOM `.out` is supplied, `fit_distances` is run in-process to obtain one.

With `n_runs=1`, runs a single DAMMIF reconstruction. With `n_runs>1`, runs independent DAMMIF replicas then DAMAVER (NSD alignment, outlier rejection, frequency/occupancy map). The data-fitting final shape is the most probable DAMMIF replica (`best.cif` symlink); the DAMAVER frequency map is the stability product. DAMMIN refinement is not performed.

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where DAMMIF / DAMAVER outputs are written.
- `gnom_path` (str | None, default `None`): Optional path to a GNOM/DATGNOM `.out` file for DAMMIF. If omitted, `fit_distances` is run in-process on `profile` and its `best_gnom_out_path` is used.
- `n_runs` (int, default `1`): Number of independent DAMMIF runs. When `>1`, DAMAVER is run on the particle models.
- `dammif_mode` (str, default `fast`): DAMMIF annealing mode: `fast` or `slow`.
- `make_presentation_vis` (bool, default `False`): When True, write presentation PNGs/GIFs under `{output}/presentation/` (synced per-run rotation GIFs, overlap, occupancy threshold; nm scale bar; no run/title captions).
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict[str, str | list[str]]` with:

- `output_subdir`: Directory containing DAMMIF fit artifacts (FIR/CIF and summary files). Each replica also gets `{rep}_pr.dat` and `{rep}_pr.png` (GNOM-style p(r) from DAM bead pairs via Monte Carlo).
- `best_cif_path`: Symlink `best.cif` pointing at the most probable particle CIF (the sole run when `n_runs=1`).
- `best_view_path`: Path to ``best_view.png`` (isosurface + fit overlay for the best model); empty if unavailable.
- `frequency_map_path`: Path to the DAMAVER frequency/occupancy map CIF (empty string when `n_runs=1`).
- `presentation_dir` and related `presentation_*` keys when `make_presentation_vis=True` (empty strings / empty list otherwise).

### Python usage

```python
from autosaxs.skill import model_dam

out = model_dam(
    profile="subtracted/sub_sample_01.dat",
    output_dir="dammif",
    gnom_path="guinier/sample_01_gnom.out",
    n_runs=1,
    dammif_mode="fast",
    make_presentation_vis=False,
    use_cache=False,
)

print(out["output_subdir"], out["best_cif_path"])
```

### CLI usage

```bash
autosaxs model-dam subtracted/sub_sample_01.dat --output-dir dammif --n-runs 1 --dammif-mode fast
autosaxs model-dam subtracted/sub_sample_01.dat --output-dir dammif --n-runs 5 --make-presentation-vis
```
