# `autosaxs analyze-loglog` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs analyze-loglog ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs analyze-loglog ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This procedure wraps the `autosaxs analyze-loglog` CLI command / `autosaxs.skill.analyze_loglog` Python entry point.

## When to use me

- You want to run `autosaxs analyze-loglog` on SAXS data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. Run **`/path/to/myenv/bin/autosaxs analyze-loglog …`** (or `autosaxs analyze-loglog …` when the right env is active), or call the Python function.
3. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs analyze-loglog …`**.
- If you know the correct env is active on `PATH`, **`autosaxs analyze-loglog …`** is fine.
- Prefer the Python API (`autosaxs.skill.analyze_loglog`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: whole-curve log–log regime analysis.

Provides the initial log[I] vs log[q] inspection after buffer subtraction
(Jacques & Trewhella 2010 PDF p.8) and soft-tags dimensionality / Porod-exponent
regimes −1 / −2 / −4 (rod / disk / sphere; Li 2016 Chem. Rev. letter H PDF pp.8–9)
plus polymer / fractal-like bands (Putnam 2007; Jeffries primer). Fits slopes on
original I(q) then annotates the log–log figure (Pauw 2013 linearization caution).

Soft metrics only — never hard-gates modeling. Replaces the former ``plot`` skill's
dumb ``loglog_*.png`` with an annotated whole-curve artifact + passport.

**Regime tags** (``regimes[*].tag`` / ``dominant_mid_q_tag``):
``rod_like`` | ``disk_like`` | ``coil_like`` | ``compact_porod_like`` |
``fractal_like`` | ``unclear``.

Units: ``q`` in nm⁻¹, lengths in nm. Shared slope math lives in
``autosaxs.core.profile_features`` (same SSOT as ``powerlaw_exponent`` / ``fit_porod``).

### Arguments

- `profile` (str): 1D path expression (file/directory/glob). Directories expand to `*.dat` (non-recursive).
- `output_dir` (str, default `.`): Directory where outputs are written.
- `config_path` (str | None, default `None`): Deprecated YAML/`analyze_loglog` section; ignored.
- `rg_nm` (float | None, default `None`): Optional Rg (nm) for Guinier-band annotation only.
  If omitted, a light in-process Guinier is attempted; failure is soft (no band).
- `n_min` (int, default `8`): Minimum points per slope window.
- `max_segments` (int, default `6`): Cap on retained regime segments.
- `use_cache` (bool, default `False`): Enable/disable caching for this skill run.

### Returns

`dict` with:

- `results_path`: Text passport mirror (`regimes`, tags).
- `loglog_plot_path`: Annotated whole-curve log–log PNG.
- `loglog_dat_path`: Tabular ``q, I`` companion (optional segment id).
- Passport fields: `regimes` (JSON string of segment list), `dominant_mid_q_tag`,
  `n_regimes`, `n_dropped_nonpositive`, `low_q_plateau_hint`,
  optional `blank_sensitivity_hint`, `rg_nm_context`.

### Python usage

```python
from autosaxs.skill import analyze_loglog

out = analyze_loglog(
    profile="subtracted/sub_sample_01.dat",
    output_dir="loglog",
    use_cache=False,
)
print(out["dominant_mid_q_tag"], out["n_regimes"])
```

### CLI usage

```bash
autosaxs analyze-loglog subtracted/sub_sample_01.dat --output-dir loglog
autosaxs analyze-loglog subtracted/sub_sample_01.dat --rg-nm 3.2 --output-dir loglog
```
