---
name: report-individual
description: SAXS / small-angle x-ray scattering: build a per-sample PDF report from an existing pipeline directory (SAXS report / plots + tables). The skill scans `directory` for paths matching the provided `basename` and then assembles the report sections.
catalog-hidden: true
---

# `autosaxs report-individual` (subskill)

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs report-individual ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs report-individual ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This skill wraps the `autosaxs report-individual` CLI command / `autosaxs.skill.report_individual` Python entry point.

## When to use me

- You want to run `autosaxs report-individual` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs report-individual …`** (or `autosaxs report-individual …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs report-individual …`**.
- If you know the correct env is active on `PATH`, **`autosaxs report-individual …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.report_individual`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: build a per-sample PDF report from an existing pipeline directory (SAXS report / plots + tables). The skill scans `directory` for paths matching the provided `basename` and then assembles the report sections.

### Arguments

- `directory` (str): Path to the existing pipeline output directory (the place where intermediate results live).
- `basename` (str): Sample identifier used to match intermediate artifacts within `directory`.
- `output_dir` (str, default `.`): Directory where the PDF report is written.
- `output_path` (str | None, default `None`): Optional explicit output PDF path. If not provided, defaults to `<output_dir>/<basename>_report.pdf`.
- `use_cache` (bool, default `False`): Present for CLI parity; report generation does not use caching.

### Returns

`dict[str, Any]` with:

- `report_pdf_path`: Path to the generated PDF.

### Python usage

```python
from autosaxs.skill import report_individual

out = report_individual(
    directory="pipeline_out",
    basename="sample_01",
    output_dir="reports",
)

print(out["report_pdf_path"])
```

### CLI usage

```bash
autosaxs report_individual pipeline_out sample_01 --output-dir reports
```
