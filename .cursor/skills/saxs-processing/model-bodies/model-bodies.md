# `autosaxs model-bodies` (subskill)

Ordinary procedure doc for the orchestrator — **not** a Cursor Agent Skill (`SKILL.md`). Open this file when the plan reaches this step.

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs model-bodies ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs model-bodies ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This procedure wraps the `autosaxs model-bodies` CLI command / `autosaxs.skill.model_bodies` Python entry point.

## When to use me

- You want to run `autosaxs model-bodies` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs model-bodies …`** (or `autosaxs model-bodies …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs model-bodies …`**.
- If you know the correct env is active on `PATH`, **`autosaxs model-bodies …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.model_bodies`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: run ATSAS `bodies` shape fitting for multiple candidate shapes on a 1D profile, exporting fit files (FIR, PNG, YAML, CSV) and a comparison figure.

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where `bodies` outputs are written.
- `config_path` (str | None, default `None`): Optional YAML config path for CLI parity; this skill does not read a `model_bodies` section (no bundled defaults).
- `shapes` (list[str] | None, default `None`): Subset of body model names to fit (`BODIES_SHAPES_LIST`). `None` or empty means fit **all** models (single `bodies` invocation). A non-empty list runs `bodies --body=...` per shape.
- `first` (int | None, default `None`): Passed to `bodies` as `--first` (1-based data point index). If omitted, taken from the low-q end of the Guinier interval from in-process `fit_guinier`.
- `last` (int | None, default `None`): Passed to `bodies` as `--last` (1-based data point index). Omitted when `None`.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict[str, str]` with:

- `output_subdir`: Directory containing the exported `bodies` fit artifacts.

The directory typically contains multiple per-shape FIT files plus aggregated `bodies_fits.yml` and `bodies_fits.csv` if any shapes successfully fit. Each fitted shape also gets `{shape}_pr.dat` and `{shape}_pr.png` (GNOM-style p(r) from the voxel DAM used for 3D views, via Monte Carlo bead-pair sampling).

### Python usage

```python
from autosaxs.skill import model_bodies

out = model_bodies(
    profile="subtracted/sub_sample_01.dat",
    output_dir="bodies",
    shapes=["cylinder", "ellipsoid"],
    first=10,
    last=120,
    use_cache=False,
)

print(out["output_subdir"])
```

### CLI usage

```bash
autosaxs model-bodies subtracted/sub_sample_01.dat --output-dir bodies --shapes cylinder ellipsoid --first 10 --last 120
```
