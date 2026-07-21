---
name: model-density
description: SAXS / small-angle x-ray scattering: ab initio continuous electron-density reconstruction with DENSS (Grant protocol; density map / FSC resolution / voxel σ map). Requires the DENSS package (`denss`, `denss-all`, `denss-refine`) installed in the active Python environment.
catalog-hidden: true
---

# `autosaxs model-density` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs model-density ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs model-density ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This skill wraps the `autosaxs model-density` CLI command / `autosaxs.skill.model_density` Python entry point.

## When to use me

- You want to run `autosaxs model-density` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs model-density …`** (or `autosaxs model-density …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs model-density …`**.
- If you know the correct env is active on `PATH`, **`autosaxs model-density …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.model_density`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: ab initio continuous electron-density reconstruction with DENSS (Grant protocol; density map / FSC resolution / voxel σ map). Requires the DENSS package (`denss`, `denss-all`, `denss-refine`) installed in the active Python environment.

Protocol `mode`: `pilot` runs a single DENSS reconstruction; `average` runs denss-all (N maps, enantiomer selection, alignment, averaging, FSC) and writes a voxel-wise σ map from the aligned replicas; `refined` runs denss-all then denss-refine of the average against the data (σ still from the denss-all aligned stack). Pipeline q is converted to Å⁻¹ for DENSS staging (never pass autosaxs nm GNOM `.out` files to DENSS unchanged). Alignment is denss-all's built-in procedure (no separate aligner).

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where DENSS outputs are written.
- `gnom_path` (str | None, default `None`): Optional GNOM/DATGNOM `.out` used only for \(D_{\max}\) (nm→Å). Smooth \(I(q)\) comes from the staged Å `.dat` (DENSS may fit internally).
- `mode` (str, default `pilot`): Protocol stage: `pilot`, `average`, or `refined`.
- `denss_mode` (str, default `slow`): DENSS algorithm mode: `slow`, `fast`, or `membrane`.
- `n_maps` (int, default `20`): Number of reconstructions for `average`/`refined` (ignored in `pilot`; must be ≥2 when used).
- `n_jobs` (int, default `1`): Parallel cores for denss-all.
- `visualize_all` (bool, default `False`): When True, write slice GIF/PNG under `{output}/visuals/` (synced YZ/XZ/XY cuts through the particle AABB; nm scale bar below panels; electron-ish colormap).
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict[str, str]` with:

- `output_subdir`: Directory containing DENSS artifacts for this sample.
- `density_map_path`: Primary density MRC (pilot map, average map, or refined map).
- `avg_map_path`: Averaged MRC path when averaging ran; empty string for `pilot`.
- `sigma_map_path`: Voxel-wise density σ MRC from denss-all `*_aligned.mrc` stack when averaging ran; empty string for `pilot`.
- `fsc_path`: FSC curve path when averaging ran; empty string otherwise.
- `map_fit_path`: Calculated vs experimental fit file when present; else empty.
- `denss_log_path`: Main log for the completed mode.
- `visuals_dir`, `slices_gif`, `midplanes_png` when `visualize_all=True` (empty strings otherwise).

### Python usage

```python
from autosaxs.skill import model_density

out = model_density(
    profile="subtracted/sub_sample_01.dat",
    output_dir="denss",
    mode="pilot",
    denss_mode="slow",
    visualize_all=False,
    use_cache=False,
)

print(out["density_map_path"])
```

### CLI usage

```bash
autosaxs model-density subtracted/sub_sample_01.dat --output-dir denss --mode pilot --denss-mode slow
autosaxs model-density subtracted/sub_sample_01.dat --output-dir denss --visualize-all
```
