# `autosaxs fit-porod` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs fit-porod ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs fit-porod ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This procedure wraps the `autosaxs fit-porod` CLI command / `autosaxs.skill.fit_porod` Python entry point.

## When to use me

- You want to run `autosaxs fit-porod` on SAXS data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. Run **`/path/to/myenv/bin/autosaxs fit-porod …`** (or `autosaxs fit-porod …` when the right env is active), or call the Python function.
3. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs fit-porod …`**.
- If you know the correct env is active on `PATH`, **`autosaxs fit-porod …`** is fine.
- Prefer the Python API (`autosaxs.skill.fit_porod`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: high-q Porod asymptote + Porod–Debye diagnostic.

Fits a background-aware model ``I(q) ≈ B + A·q^α`` on a high-q window
(Pauw 2013; Li 2016 Chem. Rev. letter H / PDF p.8 — sharp-interface solids → q⁻⁴
when q ≫ π/Rg; Jacques & Trewhella 2010 PDF p.8 — blank errors dominate high q).
Emits a Porod–Debye ``q⁴I`` vs ``q⁴`` plateau score (Rambo & Tainer 2011 Biopolymers
PDF pp.3,5,16). Soft metrics only — never hard-gates modeling.

**Primary fit:** ``alpha_mode=free`` (α floated near ``alpha_nominal``, default −4).
A secondary fixed-α=−4 fit always reports classical ``porod_constant`` ≡ lim q⁴(I−B)
as a diagnostic companion. ``porod_volume_nm3`` is **omitted by default**
(``porod_volume_method=none``); raw truncated / regularized invariant paths are
not enabled in v1 (Trewhella 2017; Rambo & Tainer 2013).

Units: ``q`` in nm⁻¹, lengths in nm.

### Arguments

- `profile` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where outputs are written.
- `config_path` (str | None, default `None`): Deprecated YAML/`fit_porod` section; ignored.
- `rg_nm` (float | None, default `None`): Radius of gyration in nm. If omitted, taken from in-process Guinier.
- `alpha_mode` (str, default `free`): Primary asymptote mode — `free` or `fixed_minus4`.
- `alpha_nominal` (float, default `-4.0`): Target exponent for window search / free-mode seed.
- `alpha_tol` (float, default `0.5`): Acceptance band around `alpha_nominal` for a porod-like window.
- `fit_background` (bool, default `True`): Fit constant background ``B`` in ``B + A q^α``.
- `qR_min` (float, default `2.0`): Theoretical gate ``q ≥ qR_min / Rg`` when Rg is known.
- `n_min` / `max_pts` (int): Min / max points in the Porod window search.
- `porod_volume_method` (str, default `none`): `none` (omit volume) | reserved `raw_truncated` /
  `regularized_from_pr` (not implemented in v1 — raises if requested).
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict` with:

- `results_path`: Text passport mirror.
- `porod_plot_path`: Windowed log–log / model PNG.
- `porod_debye_plot_path`: Porod–Debye ``q⁴I`` vs ``q⁴`` PNG.
- `porod_fit_dat_path`: Tabular ``q, I, I_fit`` companion.
- Passport fields: `q_min_nm`, `q_max_nm`, `n_points`, `alpha`, `alpha_mode`,
  `porod_amplitude`, `porod_constant`, `background`, `fit_r2`,
  `theoretical_range_used`, `theoretical_range_absent`, `porod_debye_plateau`,
  `porod_volume_method`, optional `blank_sensitivity_hint`.
  `porod_volume_nm3` is omitted when method is `none`.

### Python usage

```python
from autosaxs.skill import fit_porod

out = fit_porod(
    profile="subtracted/sub_sample_01.dat",
    output_dir="porod",
    rg_nm=3.2,
    use_cache=False,
)
print(out["alpha"], out["porod_debye_plateau"])
```

### CLI usage

```bash
autosaxs fit-porod subtracted/sub_sample_01.dat --output-dir porod
autosaxs fit-porod subtracted/sub_sample_01.dat --rg-nm 3.2 --output-dir porod
```
