# `autosaxs plot` (subskill)

Ordinary procedure doc for the orchestrator — **not** a Cursor Agent Skill (`SKILL.md`). Open this file when the plan reaches this step.

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs plot ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs plot ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This procedure wraps the `autosaxs plot` CLI command / `autosaxs.skill.plot` Python entry point.

## When to use me

- You want to run `autosaxs plot` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs plot …`** (or `autosaxs plot …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs plot …`**.
- If you know the correct env is active on `PATH`, **`autosaxs plot …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.plot`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: generate standard diagnostic plots for a 1D curve (Guinier, Kratky, log-log):

- Guinier plot (log(I) vs q^2)
- Kratky plot (I*q^2 vs q)
- log-log plot (log(I) vs log(q))

Also writes a Guinier `.dat` file (ln(I) vs q²) used downstream.

### Arguments

- `profile` (str): 1D path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where plot files are written.
- `guinier_q_min` (float | None, default `None`): Lower q bound for selecting Guinier range (enables `guinier_dat_path`).
- `guinier_q_max` (float | None, default `None`): Upper q bound for selecting Guinier range.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

Important constraint:

- If you set `guinier_q_max`, you must also set `guinier_q_min` (otherwise the skill raises `ValueError`).

### Returns

`dict[str, str]` with:

- `guinier_plot_path`: Path to the Guinier PNG.
- `kratky_plot_path`: Path to the Kratky PNG.
- `loglog_plot_path`: Path to the log-log PNG.
- `guinier_dat_path`: Path to the Guinier `.dat` (q², ln(I)) written by the skill (always written; independent of `guinier_q_min/max`).

### Python usage

```python
from autosaxs.skill import plot

out = plot(
    profile="subtracted/sub_sample_01.dat",
    output_dir="plots",
    guinier_q_min=0.01,
    guinier_q_max=0.05,
    use_cache=False,
)

print(out["guinier_dat_path"])
```

### CLI usage

```bash
autosaxs plot subtracted/sub_sample_01.dat --output-dir plots --guinier-q-min 0.01 --guinier-q-max 0.05
```
