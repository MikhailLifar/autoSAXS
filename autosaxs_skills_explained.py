from __future__ import annotations

import inspect
from pathlib import Path


def _read_readme_header(repo_root: Path) -> str:
    header_path = repo_root / "docs" / "readme_header.md"
    return header_path.read_text(encoding="utf-8")


def main() -> int:
    repo_root = Path(__file__).resolve().parent
    out_md = repo_root / "README.md"

    from autosaxs import skill as skill_mod

    skills = skill_mod.list_skills(include_reports=True)
    names = list(getattr(skill_mod, "SKILL_ORDER", []))
    # Fall back to deterministic order if SKILL_ORDER is missing or incomplete.
    for extra in sorted(set(skills) - set(names)):
        names.append(extra)

    readme_preamble = _read_readme_header(repo_root).rstrip()
    parts: list[str] = [readme_preamble + "\n\n---\n"]
    for name in names:
        fn = skills.get(name)
        if fn is None:
            continue
        doc = inspect.getdoc(fn) or ""
        parts.append(f"\n## `{name}`\n\n{doc.rstrip()}\n\n---\n")

    out_md.write_text("".join(parts).rstrip() + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

