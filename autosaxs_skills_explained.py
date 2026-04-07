from __future__ import annotations

import ast
import inspect
from pathlib import Path


def _read_readme_header(repo_root: Path) -> str:
    header_path = repo_root / "docs" / "readme_header.md"
    return header_path.read_text(encoding="utf-8")


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
    out_md = repo_root / "README.md"

    from autosaxs import skill as skill_mod

    names = _skill_names_in_source_order(skill_py)

    readme_preamble = _read_readme_header(repo_root).rstrip()
    parts: list[str] = [readme_preamble + "\n\n---\n"]
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

