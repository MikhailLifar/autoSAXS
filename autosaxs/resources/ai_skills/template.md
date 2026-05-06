---
name: {{name}}
description: {{description}}
license: MIT
compatibility: opencode
metadata:
  tool: autosaxs
  command: autosaxs {{command}}
---

## What I do

This skill wraps the `autosaxs {{command}}` CLI command / `autosaxs.skill.{{python_name}}` Python entry point.

## When to use me

- You want to run `autosaxs {{command}}` on real data.
- You want the canonical usage/arguments directly from the implementation docstring.

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

- Prefer calling `autosaxs {{command}}` (CLI) for reproducibility.
- Prefer the Python entry point for scripting/batch processing.

## Autosaxs skill docstring

{{docstring}}

