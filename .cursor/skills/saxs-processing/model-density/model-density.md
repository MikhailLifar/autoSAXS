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

This procedure wraps the `autosaxs model-density` CLI command / `autosaxs.skill.model_density` Python entry point.

## When to use me

- You want to run `autosaxs model-density` on SAXS data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. Run **`/path/to/myenv/bin/autosaxs model-density …`** (or `autosaxs model-density …` when the right env is active), or call the Python function.
3. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs model-density …`**.
- If you know the correct env is active on `PATH`, **`autosaxs model-density …`** is fine.
- Prefer the Python API (`autosaxs.skill.model_density`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: ab initio continuous electron-density reconstruction with DENSS (Grant protocol; density map / FSC resolution / voxel σ map).

### Arguments

- `profile` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where the outputs are written.
- `gnom_path` (str | None, default `None`): Optional GNOM/DATGNOM `.out` used only for \(D_{\max}\) (nm→Å). Smooth \(I(q)\) comes from the staged Å `.dat` (DENSS may fit internally).
- `mode` (str, default `pilot`): Protocol stage: `pilot`, `average`, or `refined`. Defaults to `pilot`.
- `denss_mode` (str, default `slow`): DENSS algorithm mode: `slow`, `fast`, or `membrane`. Defaults to `slow`.
- `n_maps` (int, default `20`): Number of reconstructions for `average`/`refined` (ignored in `pilot`; must be ≥2 when used). Defaults to 20.
- `n_jobs` (int, default `1`): Parallel cores for denss-all. Defaults to 1.
- `visualize_all` (bool, default `True`): When True, write slice GIF/PNG and rotating density/σ GIFs under `{output}/visuals/`. Defaults to `True`.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Short parameter list

- mode: Run mode: pilot - quick map view; average - average map across mutliple runs; refined - refined from averaged; default - pilot
- denss_mode: Internal parameter, recommended not to change, default: slow
- n_maps: Number of independent run for average; default 20
- visualize_all: Run visualizations, default: true

### Returns

`dict[str, str]` with:

- `output_subdir`: Directory containing DENSS artifacts for this sample.
- `density_map_path`: Primary density MRC (pilot map, average map, or refined map).
- `avg_map_path`: Averaged MRC path when averaging ran; empty string for `pilot`.
- `sigma_map_path`: Voxel-wise density σ MRC from denss-all `*_aligned.mrc` stack when averaging ran; empty string for `pilot`.
- `fsc_path`: FSC curve path when averaging ran; empty string otherwise.
- `map_fit_path`: Calculated vs experimental fit file when present; else empty.
- `denss_log_path`: Main log for the completed mode.
- `visuals_dir`, `slices_gif`, `midplanes_png`, `density_rotate_gif`, `sigma_rotate_gif` when `visualize_all=True` (empty strings otherwise; `sigma_rotate_gif` empty in `pilot`).

### Python usage

```python
from autosaxs.skill import model_density

out = model_density(
    profile="subtracted/sub_sample_01.dat",
    output_dir="denss",
    mode="pilot",
    denss_mode="slow",
    use_cache=False,
)

print(out["density_map_path"])
```

### CLI usage

```bash
autosaxs model-density subtracted/sub_sample_01.dat --output-dir denss/ 
autosaxs model-density subtracted/sub_sample_01.dat --mode average --denss-mode slow --n-maps 10 --n-jobs 4 -o denss/
```
