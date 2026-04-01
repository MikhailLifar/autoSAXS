from __future__ import annotations

import ast
import inspect
from pathlib import Path


HEADER = """# autosaxs Skills Explained

This document explains the public *skills* exposed by the `autosaxs` package.

Skills are Python functions in `repos/autosaxs/skill.py` with a fixed signature designed to be callable both from Python and from the `autosaxs` CLI.

## CLI vs Python (how commands are wired)

The `autosaxs` command dispatches subcommands to the corresponding skill functions by introspecting their signatures. In practice:

- Run a skill from the CLI as `autosaxs <command> ...`.
- Every skill supports `--output-dir <path>` (maps to the skill's `output_dir` argument, default: `.`).
- Every skill supports caching by default; use `--no-cache` to disable it (maps to `use_cache=False` in Python).
- Positional arguments in the CLI match the skill signature order.
- Keyword options use `--kebab-case` names (underscores become `-`).

### Path expansion (important API behavior)

Most skills take a **path expression** rather than a strict “single file”:

- A file path is used as-is.
- A directory expands to matching files (non-recursive):
  - 2D inputs: `*.tif`
  - 1D inputs: `*.dat`
- A glob expression is allowed (including `**`); results are sorted, and **empty expansion is an error**.

Note: `autosaxs integrate` accepts either a single path expression **or** multiple image paths on the CLI (the CLI passes a list; the skill normalizes it).

Caching details (enabled by default):

- When `use_cache=True`, the skill may write/read a hidden `.cache` YAML file inside its output directory.
- Re-running with the same inputs and relevant options can reuse previously generated output paths if the files still exist and are recent enough (output-integrity check).
- On cache hits, the returned dict includes `from_cache=True` in addition to the usual output path keys.
"""


def _skill_names_in_source_order(skill_py: Path) -> list[str]:
    tree = ast.parse(skill_py.read_text(encoding="utf-8"))
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            name = node.name
            if name.startswith("_"):
                continue
            # Only include functions that have a docstring (skills should).
            if ast.get_docstring(node) is None:
                continue
            names.append(name)
    return names


def main() -> int:
    repo_root = Path(__file__).resolve().parent
    skill_py = repo_root / "autosaxs" / "skill.py"
    out_md = repo_root / "autosaxs_skills_explained.md"

    from autosaxs import skill as skill_mod

    names = _skill_names_in_source_order(skill_py)

    parts: list[str] = [HEADER.rstrip() + "\n\n---\n"]
    for name in names:
        fn = getattr(skill_mod, name, None)
        if fn is None or not inspect.isfunction(fn):
            continue
        if getattr(fn, "__module__", None) != skill_mod.__name__:
            continue
        doc = inspect.getdoc(fn) or ""
        parts.append(f"\n## `{name}`\n\n{doc.rstrip()}\n\n---\n")

    out_md.write_text("".join(parts).rstrip() + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

