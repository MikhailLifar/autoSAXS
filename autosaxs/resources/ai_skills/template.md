---
name: {{name}}
description: {{description}}
license: MIT
compatibility: opencode
metadata:
  tool: autosaxs
  command: autosaxs {{command}}
---

## Critical: `autosaxs` is a Python package

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). It only runs when that environment’s interpreter can import and execute the package.

You **must** use one of these execution modes:

1. **Activated environment** — The same shell/session where `autosaxs` was installed is active (conda/venv/`pipx`, etc.), so the `autosaxs` console script exists on `PATH` and matches that interpreter.
2. **Explicit interpreter (recommended when unsure)** — Call the package through the interpreter that has `autosaxs` installed:

```bash
<path-to-python> -m autosaxs {{command}} ...
```

Examples:

```bash
/path/to/venv/bin/python -m autosaxs {{command}} ...
conda run -n myenv python -m autosaxs {{command}} ...
```

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: resolve which Python environment has `autosaxs`, then use mode (1) or (2). Never invent a fake `autosaxs` binary path.

## What I do

This skill wraps the `autosaxs {{command}}` CLI command / `autosaxs.skill.{{python_name}}` Python entry point.

## When to use me

- You want to run `autosaxs {{command}}` on real data.

## Do NOT use me when

- You want a multi-step workflow; compose multiple autosaxs skills instead.

## Required inputs

See the docstring section **Arguments** below.

## Procedure

1. Prepare input paths and choose an `output_dir` (if applicable).
2. Run `autosaxs {{command}} ...` (or call the Python function).
3. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- Prefer **`python -m autosaxs {{command}} ...`** with the **known-good interpreter path** when the active shell might not be the install environment (CI, fresh terminals, mixed conda/system Python).
- If the environment is guaranteed correct and `autosaxs` resolves on `PATH`, the bare `autosaxs {{command}}` form is fine.
- Prefer the Python API (`autosaxs.skill.{{python_name}}`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

{{docstring}}

