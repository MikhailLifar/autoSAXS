from __future__ import annotations

import inspect
from pathlib import Path
from typing import Dict


def _read_resource_text(name: str) -> str:
    """
    Load a markdown template shipped with the autosaxs.resources.readme package.

    Uses importlib.resources when available so this works from installed wheels.
    """
    try:
        from importlib.resources import files  # py3.9+

        return (files(__package__) / name).read_text(encoding="utf-8")
    except Exception:
        return Path(__file__).with_name(name).read_text(encoding="utf-8")


def _skill_sections() -> str:
    from autosaxs import skill as skill_mod

    skills = skill_mod.list_skills(include_reports=True)
    names = list(getattr(skill_mod, "SKILL_ORDER", []))
    for extra in sorted(set(skills) - set(names)):
        names.append(extra)

    parts: list[str] = []
    for name in names:
        fn = skills.get(name)
        if fn is None:
            continue
        doc = inspect.getdoc(fn) or ""
        parts.append(f"\n## `{name}`\n\n{doc.rstrip()}\n\n---\n")
    return "".join(parts)


def generate_docs(*, output_dir: str | Path = ".") -> Dict[str, Path]:
    """
    Generate package-facing docs under ``output_dir``:

    - ``README.md`` — short PyPI / GitHub landing page
    - ``autosaxs-docs/skills_reference.md`` — detailed per-skill reference

    Returns a mapping of logical names to written paths (``readme``, ``skills_reference``).
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    docs_dir = out_dir / "autosaxs-docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    readme_path = out_dir / "README.md"
    skills_path = docs_dir / "skills_reference.md"

    readme_path.write_text(
        _read_resource_text("readme_header.md").rstrip() + "\n",
        encoding="utf-8",
    )

    skills_preamble = _read_resource_text("skills_docs_header.md").rstrip()
    skills_body = _skill_sections()
    skills_path.write_text(
        (skills_preamble + "\n\n---\n" + skills_body).rstrip() + "\n",
        encoding="utf-8",
    )

    return {"readme": readme_path, "skills_reference": skills_path}


def main() -> int:
    paths = generate_docs(output_dir=".")
    for key, path in paths.items():
        print(f"{key}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
