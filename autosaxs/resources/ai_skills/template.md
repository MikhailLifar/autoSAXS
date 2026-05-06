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

**Do not assume `autosaxs` is an ordinary system command.** It is installed **into a Python environment** (for example via `pip install autosaxs`). Pip installs a launcher script in that environment’s `bin/` directory (next to `python`, `pip`, etc.). **Always run the CLI via that launcher** — especially use an explicit path when the active shell might be the wrong interpreter.

**Preferred invocation (explicit, unambiguous):**

```bash
/path/to/myenv/bin/autosaxs {{command}} ...
```

If the correct environment is activated so its `bin/` is on `PATH`, the same command is:

```bash
autosaxs {{command}} ...
```

**What does not work:** `python -m autosaxs …` — the package has no top-level `__main__.py`. Do not try to substitute other `-m` module paths here; **use `<env>/bin/autosaxs` instead.**

Do **not** insert a stray `-m` between `autosaxs` and the subcommand (e.g. `autosaxs -m {{command}}`); the subcommand name comes immediately after the launcher: `autosaxs {{command}} …`.

If you see **`autosaxs: command not found`** (or similar), the agent **must not** treat this as a broken skill: call **`/path/to/the/environment/bin/autosaxs`** (resolve the env where `autosaxs` is installed). Never invent a fake `autosaxs` binary path.

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
2. Run **`/path/to/myenv/bin/autosaxs {{command}} …`** (or `autosaxs {{command}} …` when the right env is active), or call the Python function.
3. Use the returned/written output paths.

## Output requirements

See the docstring section **Returns** below.

## Tooling rules

- **`autosaxs` is always tied to a Python environment** — see **Critical: `autosaxs` is a Python package** above before running anything.
- When in doubt (CI, fresh terminals, mixed conda/system shells), **always use the full path:** **`<path-to-env>/bin/autosaxs {{command}} …`**.
- If you know the correct env is active on `PATH`, **`autosaxs {{command}} …`** is fine.
- Prefer the Python API (`autosaxs.skill.{{python_name}}`) for scripting or tight integration inside Python.

## Autosaxs skill docstring

{{docstring}}

