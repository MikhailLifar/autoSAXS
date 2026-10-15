# `autosaxs model-lc` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs model-lc ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs model-lc ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This procedure wraps the `autosaxs model-lc` CLI command / `autosaxs.skill.model_lc` Python entry point.

## When to use me

- You want to run `autosaxs model-lc` on SAXS data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. Run **`/path/to/myenv/bin/autosaxs model-lc …`** (or `autosaxs model-lc …` when the right env is active), or call the Python function.
3. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs model-lc …`**.
- If you know the correct env is active on `PATH`, **`autosaxs model-lc …`** is fine.
- Prefer the Python API (`autosaxs.skill.model_lc`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: fit an experimental profile as a nonnegative linear
combination of library curves (custom NNLS — **not** ATSAS OLIGOMER).

Library input is either:

- one CSV: column 0 = q (nm⁻¹), remaining columns = component intensities; or
- a path expression / directory / glob / comma-list of `.dat` curves.

When the library has more curves than `n_components`, all combinations of size
``k = 1..n_components`` are scored with NNLS and the lowest χ² wins.

**Complexity:** for library size ``N`` and ``k_max = n_components``, the search evaluates
``Σ_{k=1}^{k_max} C(N,k)`` NNLS problems, each roughly ``O(n_q · k²)``. Prefer
``N ≲ 50`` for ``k_max=3`` (``C(50,3)=19600``). When ``N ≤ n_components``, a single
NNLS on all columns is used (fast path).

Curves are interpolated onto the experimental q grid (nm⁻¹). Points outside a component's
q coverage are excluded from that component's column (set NaN → row dropped if any NaN).

### Arguments

- `profile` (str): Experimental 1D path expression (file/directory/glob → `.dat`).
- `database` (str): Library CSV **or** `.dat` path expression / directory / glob / list.
- `output_dir` (str, default `.`): Directory where outputs are written.
- `config_path` (str | None, default `None`): Deprecated. Unused.
- `n_components` (int, default `3`): Maximum number of library curves in the combination (1–3).
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict[str, str]` with:

- `fit_path`: Fitted I(q) autosaxs `.dat` (q in nm⁻¹).
- `residual_path`: Residual (I_exp − I_fit) `.dat`.
- `weights_path`: CSV of chosen component names/indices and nonnegative weights.
- `plot_path`: Overlay PNG (experiment vs fit ± components).
- `components_used`: Comma-separated component labels chosen by the search.
- `chi2`: Reduced χ² string (for CLI/path-dict stability).
- `output_subdir`: Output directory for this sample.

Does **not** require ATSAS.

### Python usage

```python
from autosaxs.skill import model_lc

out = model_lc(
    profile="subtracted/sample.dat",
    database="library/",           # dir of .dat
    output_dir="lc_out",
    n_components=3,
)
# or: database="ff_library.csv"
print(out["weights_path"], out["components_used"])
```

### CLI usage

```bash
autosaxs model-lc subtracted/sample.dat library/ --output-dir lc_out --n-components 3
autosaxs model-lc sample.dat ff_library.csv -o lc_out --n-components 2
```
