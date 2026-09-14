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

This procedure wraps the `autosaxs subtract` CLI command / `autosaxs.skill.subtract` Python entry point.

## When to use me

- You want to run `autosaxs subtract` on SAXS data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. Run **`/path/to/myenv/bin/autosaxs subtract …`** (or `autosaxs subtract …` when the right env is active), or call the Python function.
3. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs subtract …`**.
- If you know the correct env is active on `PATH`, **`autosaxs subtract …`** is fine.
- Prefer the Python API (`autosaxs.skill.subtract`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: subtract a buffer curve from a sample 1D profile (background subtraction).

Default scaling is ``minimal_ratio`` in an auto pre-knee ``q`` band detected on the
buffer alone. Legacy ``point_match`` / ``match_tail`` remain available via ``method``.

### Arguments

- `sample_1d` (str): Sample path expression (file/dir/glob). Directories expand to `*.dat` (non-recursive).
- `buffer_1d` (str): Path to the buffer 1D `.dat` curve (must be an existing file).
- `output_dir` (str, default `.`): Directory where subtraction outputs are written.
- `config_path` (str | None, default `None`): Optional path to a YAML config file with a `subtract` section. When omitted, bundled defaults apply.
- `method` (str | None, default `None`): `minimal_ratio` (default), `point_match`, or `match_tail`.
- `q_min` / `q_max` (float | None): Matching q-window (nm⁻¹). Optional; when omitted, auto from buffer pre-knee detection.
- `sample_form` / `buffer_form` (str | None): For `point_match` only — `linear`, `Porod`, or `Porod-plus-linear`.
- `point_match_factor` (float | None): For `point_match` only.
- `window_q_fraction` / `pre_knee_fraction` / `snr_min` / `approach_factor`: For `minimal_ratio` (and pre-knee auto band).
- `scaling_factor` (float | None): Manual scale override (finite, > 0).
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Notes
Correctness criteria: buffer and sample visually matched at the high-q tail / pre-knee region.
Negative values in the subtracted curve are possible due to high variance at the tail.

### Short parameter list

- method: default `minimal_ratio` (set `point_match` to restore the previous default)
- q_min / q_max: optional; auto pre-knee band when omitted
- window_q_fraction: default 0.05
- pre_knee_fraction: default 0.35
- snr_min: default 2.0
- scaling_factor: Manual scaling factor; replaces auto-scale when set

### Returns

`dict[str, str]` with:

- `subtracted_1d`: Path to the subtracted curve `.dat`.
- `sub_plot_path`: Path to the subtracted curve PNG (log I vs q).
- `diff_plot_path`: Path to a diff plot PNG.
- `diff_log_plot_path`: Path to a diff plot PNG with log(I) vs q.
Subtraction quality (`correct` or `over-subtracted`) is written into the subtracted `.dat` metadata
(``subtract.correctness``) and into per-sample report fragments (individual Markdown and summary YAML).
The individual report shows subtraction quality, re-plots the log-scale difference
curves from ``diff_log_*.dat``, then the subtracted curve (log I vs q) from the ``.dat``.

### Python usage

```python
from autosaxs.skill import subtract

out = subtract(
    sample_1d="integration/int_sample_01.dat",
    buffer_1d="integration/int_buffer.dat",
    output_dir="subtracted",
    use_cache=False,
)

print(out["subtracted_1d"])
```

### CLI usage

```bash
autosaxs subtract integration/int_sample_01.dat integration/int_buffer.dat \
  --output-dir subtracted
# optional restore: --method point_match --q-min 4.0 --q-max 6.0
```
