from __future__ import annotations

import inspect
from pathlib import Path


def _read_readme_header() -> str:
    """
    Load README header markdown shipped with the autosaxs package.

    Uses importlib.resources when available so this works from installed wheels.
    """
    try:
        from importlib.resources import files  # py3.9+

        return (files(__package__) / "readme_header.md").read_text(encoding="utf-8")
    except Exception:
        # Fallback for non-standard environments (editable installs, etc.)
        return Path(__file__).with_name("readme_header.md").read_text(encoding="utf-8")


def generate_readme(*, output_dir: str | Path = ".") -> Path:
    """
    Generate autosaxs README.md in the given output directory.
    Returns the written path.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_md = out_dir / "README.md"

    from autosaxs import skill as skill_mod

    skills = skill_mod.list_skills(include_reports=True)
    names = list(getattr(skill_mod, "SKILL_ORDER", []))
    # Fall back to deterministic order if SKILL_ORDER is missing or incomplete.
    for extra in sorted(set(skills) - set(names)):
        names.append(extra)

    readme_preamble = _read_readme_header().rstrip()
    parts: list[str] = [readme_preamble + "\n\n---\n"]
    for name in names:
        fn = skills.get(name)
        if fn is None:
            continue
        doc = inspect.getdoc(fn) or ""
        parts.append(f"\n## `{name}`\n\n{doc.rstrip()}\n\n---\n")

    out_md.write_text("".join(parts).rstrip() + "\n", encoding="utf-8")
    return out_md


def main() -> int:
    generate_readme(output_dir=".")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

