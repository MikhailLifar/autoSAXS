# `autosaxs process-full` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs process-full ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs process-full ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This procedure wraps the `autosaxs process-full` CLI command / `autosaxs.skill.process_full` Python entry point.

## When to use me

- You want to run `autosaxs process-full` on SAXS data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. Run **`/path/to/myenv/bin/autosaxs process-full …`** (or `autosaxs process-full …` when the right env is active), or call the Python function.
3. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs process-full …`**.
- If you know the correct env is active on `PATH`, **`autosaxs process-full …`** is fine.
- Prefer the Python API (`autosaxs.skill.process_full`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: process a directory of TIFF frames to
per-sample PDF quality reports using a small YAML pipeline config.

This skill is the explicit **folder + config** entry. It requires top-level
``analysis: mono|poly``, then delegates to ``process_monodisperse`` or
``process_polydisperse`` with ``frames_dir`` as the first path argument. The
meta-skill infers TIFF full-pipeline mode (calibrate → integrate → average
as needed → subtract → analysis) when that directory contains TIFFs and a
config is available.

Equivalent to calling the mono/poly meta-skill directly with the frames
directory as the first argument (and a matching analysis choice).

### Config (``config.conf`` / ``.yaml`` in the frames directory, or ``--conf``)

```yaml
calibrant: "*AgBh*.tif"          # basename fnmatch glob (default)
buffer_rules:                    # sample_glob → buffer_glob (fnmatch only)
  "*_sample*.tif": "*_buffer*.tif"
analysis: mono                   # required: mono | poly
# optional leaf overrides:
# calibrate: { wavelength: 1.445 }
# subtract: { q_min: 4.5, q_max: 5.5 }
```

Matching is **fnmatch against basenames** (no regex). Calibrant: lexicographically
first match. Buffer pairing reuses ``map_sample_files_to_buffer_files``; if that
leaves unpaired samples and exactly one buffer exists, all samples share it.

### Arguments

- `frames_dir` (str): Directory of TIFF frames (non-recursive ``*.tif`` / ``*.tiff``).
- `output_dir` (str, default `.`): Pipeline root (`integrator/`, `averaged/`,
  `subtracted/`, per-sample analysis trees).
- `config_path` (str | None, default `None`): YAML config; default
  ``<frames_dir>/config.conf``.
- `use_cache` (bool, default `False`): Forwarded to leaf skills.
- `n_runs` (int, default `5`): Forwarded to ``process_monodisperse`` (DAMMIF replicas).
- `run_mixture` (bool, default `False`): Forwarded to ``process_polydisperse``.

### Returns

`dict` with the analysis meta-skill return keys, plus:

- `analysis`: ``mono`` or ``poly``.
- `config_path`: Resolved config path.
- `frames_dir`: Absolute frames directory.
- `report_pdf_path`: PDF path(s) from the analysis meta-skill.
- `tiff_front`: Present when the TIFF front-end ran (usual for this entry).
- `analysis_out`: Full return dict from ``process_monodisperse`` / ``process_polydisperse``.

### Python usage

```python
from autosaxs.skill import process_full

out = process_full("/data/run01", output_dir="/data/run01/out")
print(out["report_pdf_path"])
```

### CLI usage

```bash
autosaxs process-full /data/run01
autosaxs process-full /data/run01 --conf /data/run01/config.conf -o /data/run01/out
# same TIFF inference via mono/poly first arg:
autosaxs process-monodisperse /data/run01 --conf /data/run01/config.conf -o /data/run01/out
```
