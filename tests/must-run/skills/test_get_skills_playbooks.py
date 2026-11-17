"""Contract tests for ``autosaxs get-skills`` playbooks/examples export (D-ASX-011)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPOS = Path(__file__).resolve().parents[3]
_SRC = _REPOS / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_SAMPLE_PLAYBOOKS = (
    "globular-bio.md",
    "flexible-idp.md",
    "membrane-detergent-nanodisc.md",
    "core-shell-lnp-micelle.md",
    "inorganic-np.md",
    "rods-fibrils-platelets.md",
    "polymers-gels.md",
    "interacting-concentrated.md",
)
_STAGE_PLAYBOOKS = (
    "reduction-2d-masking.md",
    "radiation-averaging.md",
    "subtraction-diagnostics.md",
    "guinier-kratky-regimes.md",
    "ift-pr-dr-artifacts.md",
    "modeling-selection-and-ambiguity.md",
)
_ARCHETYPE_COUNT = 16

# Bare Å^-1 / A^-1 numeric thresholds without an explicit nm conversion note on the same line.
_BARE_ANGSTROM_Q = re.compile(
    r"(?<![\w.])(?:q\s*[><≈~=]=?\s*)?\d+(?:\.\d+)?\s*(?:Å|A)\^-1|(?:Å|A)⁻¹",
    re.IGNORECASE,
)
_NM_CONVERSION_HINT = re.compile(
    r"(nm\^-1|nm⁻¹|×\s*10|x10|×0\.1|x0\.1|Å→nm|A→nm|after Å|from Å)",
    re.IGNORECASE,
)


def _run_get_skills(tmp_path: Path) -> Path:
    out = tmp_path / "export"
    out.mkdir()
    cmd = [sys.executable, "-m", "autosaxs.cli.cli", "get-skills", "-o", str(out)]
    proc = subprocess.run(cmd, cwd=str(_REPOS), capture_output=True, text=True, check=False)
    assert proc.returncode == 0, f"get-skills failed:\n{proc.stdout}\n{proc.stderr}"
    bundle = out / "saxs-processing"
    assert bundle.is_dir(), f"missing bundle; stdout={proc.stdout!r}"
    return bundle


def test_get_skills_copies_playbooks_and_examples(tmp_path: Path) -> None:
    bundle = _run_get_skills(tmp_path)

    assert (bundle / "SKILL.md").is_file()
    skill_text = (bundle / "SKILL.md").read_text(encoding="utf-8")
    assert "playbooks/INDEX.md" in skill_text
    assert "examples/INDEX.md" in skill_text
    assert "D-ASX-001" in skill_text
    assert "unknown — standard diagnostic triage" in skill_text
    assert "visual-diagnosis" in skill_text

    playbooks = bundle / "playbooks"
    examples = bundle / "examples"
    assert (playbooks / "INDEX.md").is_file()
    assert (examples / "INDEX.md").is_file()
    assert (playbooks / "visual" / "visual-diagnosis.md").is_file()

    for name in _SAMPLE_PLAYBOOKS:
        assert (playbooks / "samples" / name).is_file(), name
    for name in _STAGE_PLAYBOOKS:
        assert (playbooks / "stages" / name).is_file(), name

    archetype_mds = sorted(p for p in examples.glob("*.md") if p.name != "INDEX.md")
    assert len(archetype_mds) == _ARCHETYPE_COUNT


def test_get_skills_index_links_resolve(tmp_path: Path) -> None:
    bundle = _run_get_skills(tmp_path)

    playbooks_idx = (bundle / "playbooks" / "INDEX.md").read_text(encoding="utf-8")
    for name in _SAMPLE_PLAYBOOKS:
        rel = f"samples/{name}"
        assert f"`{rel}`" in playbooks_idx
        assert (bundle / "playbooks" / rel).is_file()
    for name in _STAGE_PLAYBOOKS:
        rel = f"stages/{name}"
        assert f"`{rel}`" in playbooks_idx
        assert (bundle / "playbooks" / rel).is_file()
    assert "`visual/visual-diagnosis.md`" in playbooks_idx

    examples_idx = (bundle / "examples" / "INDEX.md").read_text(encoding="utf-8")
    linked = re.findall(r"`([a-z0-9-]+\.md)`", examples_idx)
    assert len(linked) >= _ARCHETYPE_COUNT
    for name in linked:
        assert (bundle / "examples" / name).is_file(), name


def test_playbook_rules_have_primary_citation(tmp_path: Path) -> None:
    bundle = _run_get_skills(tmp_path)
    missing: list[str] = []
    for path in sorted((bundle / "playbooks").rglob("*.md")):
        if path.name == "INDEX.md":
            continue
        text = path.read_text(encoding="utf-8")
        headings = list(re.finditer(r"^###\s+(.+)$", text, re.M))
        if not headings:
            # visual-diagnosis uses tables; require at least one author-year cite
            if not re.search(r"\b(19|20)\d{2}\b", text):
                missing.append(f"{path.relative_to(bundle)}: no year cite")
            continue
        for i, m in enumerate(headings):
            start = m.start()
            end = headings[i + 1].start() if i + 1 < len(headings) else len(text)
            block = text[start:end]
            if "Primary citation" not in block and not re.search(
                r"\([12]\d{3}\)", block
            ):
                missing.append(f"{path.relative_to(bundle)} :: {m.group(1).strip()}")
    assert not missing, "rules missing citations:\n" + "\n".join(missing)


def test_no_bare_angstrom_inverse_thresholds_without_nm_note(tmp_path: Path) -> None:
    """Flag bare Å^-1 / A^-1 numeric thresholds lacking an nm conversion note on the line."""
    bundle = _run_get_skills(tmp_path)
    offenders: list[str] = []
    roots = [bundle / "playbooks", bundle / "examples"]
    for root in roots:
        for path in sorted(root.rglob("*.md")):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if not _BARE_ANGSTROM_Q.search(line):
                    continue
                if _NM_CONVERSION_HINT.search(line):
                    continue
                offenders.append(f"{path.relative_to(bundle)}:{i}: {line.strip()}")
    assert not offenders, "bare Å^-1 thresholds without nm note:\n" + "\n".join(offenders)


@pytest.mark.parametrize(
    "skill_name,needles",
    [
        (
            "subtract",
            ("Jacques & Trewhella (2010)", "frac_neg", "5-pro0019-0642.pdf"),
        ),
        (
            "fit_distances",
            ("Jacques & Trewhella (2010)", "p0", "5-pro0019-0642.pdf"),
        ),
        (
            "fit_sizes",
            ("Jacques & Trewhella (2010)", "powerlaw_exponent", "Li et al. 2016"),
        ),
    ],
)
def test_playbook_backed_skill_docstrings_cite_literature(
    skill_name: str, needles: tuple[str, ...]
) -> None:
    from autosaxs.skill import get_skill

    doc = get_skill(skill_name).__doc__ or ""
    for needle in needles:
        assert needle in doc, f"{skill_name} docstring missing {needle!r}"
