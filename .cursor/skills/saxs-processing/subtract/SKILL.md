---
name: subtract
description: SAXS / small-angle x-ray scattering: subtract a buffer curve from a sample 1D profile (background subtraction). Scaling uses either `point_match` (default)
catalog-hidden: true
---

# `autosaxs subtract` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs subtract ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs subtract ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This skill wraps the `autosaxs subtract` CLI command / `autosaxs.skill.subtract` Python entry point.

## When to use me

- You want to run `autosaxs subtract` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs subtract …`** (or `autosaxs subtract …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs subtract …`**.
- If you know the correct env is active on `PATH`, **`autosaxs subtract …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.subtract`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: subtract a buffer curve from a sample 1D profile (background subtraction). Scaling uses either `point_match` (default)
or legacy `match_tail`, optionally restricted to a q window (`q_min` / `q_max`).

### Arguments

- `sample_1d` (str): Sample path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `buffer_1d` (str): Path to the buffer 1D `.dat` curve (must be an existing file).
- `output_dir` (str, default `.`): Directory where subtraction outputs are written.
- `config_path` (str | None, default `None`): Optional path to a YAML config file with a `subtract` section. When omitted, bundled defaults apply for method/forms; q-window keys come from CLI or user file only.
- `method` (str | None, default `None`): `point_match` or `match_tail`. Defaults from bundled config when omitted.
- `q_min` (float | None, default `None`): Lower bound of q-range (CLI or user config; not in bundled template).
- `q_max` (float | None, default `None`): Upper bound of q-range; for `point_match` the match uses this as q intersect (upper edge of the window).
- `sample_form` / `buffer_form` (str | None): For `point_match` only — each is `linear`, `Porod`, or `Porod-plus-linear`.
- `point_match_factor` (float | None, default `None`): For `point_match`, scale satisfies `point_match_factor * I_sample_fit(q_max) = scale * I_buffer_fit(q_max)`.
- `scaling_factor` (float | None, default `None`): If provided, overrides automatic scaling and uses this factor directly (must be finite and > 0).
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

Required q window:

- `q_min` and `q_max` must both be set (CLI, Python API, or user config). There are no defaults; the skill raises `ValueError` if either is missing.

### Returns

`dict[str, str]` with:

- `subtracted_1d`: Path to the subtracted curve `.dat`.
- `diff_plot_path`: Path to a diff plot PNG.
- `diff_log_plot_path`: Path to a diff plot PNG with log(I) vs q.
- `sub_plot_path`: Path to a subtracted curve plot PNG.

Subtraction quality (`correct` or `over-subtracted`) is written into the subtracted `.dat` metadata
(``subtract.correctness``) and into per-sample report fragments (individual Markdown and summary YAML).

### Python usage

```python
from autosaxs.skill import subtract

out = subtract(
    sample_1d="integration/int_sample_01.dat",
    buffer_1d="integration/int_buffer.dat",
    output_dir="subtracted",
    method="point_match",
    q_min=4.0,
    q_max=6.0,
    use_cache=False,
)

print(out["subtracted_1d"])
```

### CLI usage

```bash
autosaxs subtract integration/int_sample_01.dat integration/int_buffer.dat       --output-dir subtracted --method point_match --q-min 4.0 --q-max 6.0
```
