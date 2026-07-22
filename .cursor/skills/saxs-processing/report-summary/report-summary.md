# `autosaxs report-summary` (subskill)

Ordinary procedure doc for the orchestrator — **not** a Cursor Agent Skill (`SKILL.md`). Open this file when the plan reaches this step.

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs report-summary ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs report-summary ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

## What I do

This procedure wraps the `autosaxs report-summary` CLI command / `autosaxs.skill.report_summary` Python entry point.

## When to use me

- You want to run `autosaxs report-summary` on real data.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. If this skill requires a config file (look for a required argument like `config_path` / `config` in **Arguments** below) and you do not have one yet, generate the default config into your working directory:

```bash
/path/to/myenv/bin/autosaxs get-default-config -o /path/to/directory
```

Then use the created `config_base.conf` (or a copy of it) as the config input path and edit it if your setup requires changes.
3. Run **`/path/to/myenv/bin/autosaxs report-summary …`** (or `autosaxs report-summary …` when the right env is active), or call the Python function.
4. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs report-summary …`**.
- If you know the correct env is active on `PATH`, **`autosaxs report-summary …`** is fine.
- If the skill requires a config path (e.g. `config_path` / `config`) and no config file exists yet, run **`autosaxs get-default-config -o <dir>`** to materialize the bundled default config (`config_base.conf`) into a real file, then pass that path to the skill.
- Prefer the Python API (`autosaxs.skill.report_summary`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

SAXS / small-angle x-ray scattering: build a summary report for all samples in a pipeline directory.

Merges decentralized ``*_report_summary.yaml`` files into Markdown under
``<directory>/reports/summary_assembled_report.md`` and renders the PDF with **ReportLab**
from that Markdown.

### Arguments

- `directory` (str): Path to the existing pipeline output directory.
- `output_dir` (str, default `.`): Unused for default paths; outputs go under ``<directory>/reports/``.
- `output_path` (str | None, default `None`): Output PDF path; default ``<directory>/reports/summary_report.pdf``.
- `output_md_path` (str | None, default `None`): Output path for merged summary Markdown.
- `write_pdf` (bool, default `True`): Whether to emit a PDF.
- `use_cache` (bool, default `False`): Present for CLI parity; unused.

### Returns

`dict[str, Any]` with:

- `report_pdf_path`: Path to the generated PDF when requested.
- `assembled_summary_md_path`: Merged Markdown path.

### Python usage

```python
from autosaxs.skill import report_summary

out = report_summary(
    directory="pipeline_out",
    output_dir="reports",
)

print(out["report_pdf_path"])
```

### CLI usage

```bash
autosaxs report-summary pipeline_out --output-dir reports
```
