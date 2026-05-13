---
name: fit-dammif
description: SAXS / small-angle x-ray scattering: run ATSAS `dammif` (ab initio shape reconstruction) on a 1D profile (shape reconstruction / bead model). If a GNOM output file is available, you can provide it; otherwise the profile is used.
catalog-hidden: true
---

# `autosaxs fit-dammif` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs fit-dammif ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs fit-dammif ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This skill wraps the `autosaxs fit-dammif` CLI command / `autosaxs.skill.fit_dammif` Python entry point.

## When to use me

- You want to run `autosaxs fit-dammif` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs fit-dammif …`** (or `autosaxs fit-dammif …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs fit-dammif …`**.
- If you know the correct env is active on `PATH`, **`autosaxs fit-dammif …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.fit_dammif`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: run ATSAS `dammif` (ab initio shape reconstruction) on a 1D profile (shape reconstruction / bead model). If a GNOM output file is available, you can provide it; otherwise the profile is used.

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where `dammif` outputs are written.
- `gnom_path` (str | None, default `None`): Optional path to a GNOM `.out` file. If provided, `dammif` uses it.
- `dammif_reps_num` (int, default `1`): Number of independent DAMMIF runs (replicas) to execute.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

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
    dammif_reps_num=1,
    use_cache=False,
)

print(out["output_subdir"])
```

### CLI usage

```bash
autosaxs fit_dammif subtracted/sub_sample_01.dat --output-dir dammif --gnom-path guinier/sample_01_gnom.out --dammif-reps-num 1
```
