"""
Watchdir ``config.conf`` helpers for liveview buffer_rules.

Policy lives only in hierarchical ``config.conf`` files (not session.yaml).
Silent seed creates the watchdir-root file when missing. Nearest child with an
explicit ``buffer_rules`` key replaces parent rules for samples under it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from autosaxs.core.buffer_rules import (
    DEFAULT_BUFFER_RULES,
    normalize_buffer_rules,
    validate_rule_pair,
)

CONFIG_BASENAME = "config.conf"

# Teaching comments only — no product internals (session.yaml, migration, etc.).
_CONFIG_BUFFER_RULES_COMMENT_HEADER = (
    "# Sample ↔ buffer pairing rules (first matching rule wins).\n"
    "# Left side: sample filename glob. Right side: buffer filename glob.\n"
    "# Use * wildcards and {name} capture tokens shared across both sides.\n"
    '# Empty right side ("") = no buffer (skip subtract, still analyze).\n'
    '# One buffer for everything:  "*": "my_buffer.dat"\n'
    "# Example: samples named like runA_sample.dat pair with runA_buffer.dat\n"
)

DEFAULT_CONFIG_SEED_TEXT = (
    _CONFIG_BUFFER_RULES_COMMENT_HEADER
    + "buffer_rules:\n"
    + '  "*{stem}_sample*": "*{stem}_buffer*"\n'
)


def root_config_path(watchdir: Path) -> Path:
    return watchdir.expanduser().resolve() / CONFIG_BASENAME


def seed_root_config_conf(watchdir: Path) -> Path:
    """
    Ensure ``<watchdir>/config.conf`` exists; write defaults if missing.

    Does not overwrite an existing file. Returns the root config path.
    """
    path = root_config_path(watchdir)
    if path.is_file():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(DEFAULT_CONFIG_SEED_TEXT, encoding="utf-8")
    tmp.replace(path)
    return path


def _ancestors_to_watchdir(sample_path: Path, watchdir: Path) -> List[Path]:
    """Directories from sample parent up to watchdir (inclusive), nearest first."""
    wd = watchdir.expanduser().resolve()
    try:
        sample = sample_path.expanduser().resolve()
    except OSError:
        sample = Path(str(sample_path))
    start = sample.parent
    out: List[Path] = []
    cur = start
    for _ in range(64):
        out.append(cur)
        if cur == wd:
            break
        try:
            cur.relative_to(wd)
        except ValueError:
            break
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    if wd not in out:
        out.append(wd)
    return out


def effective_config_path(
    sample_path: str | Path,
    watchdir: Path,
    *,
    fallback_root: bool = True,
) -> Optional[Path]:
    """
    Nearest ``config.conf`` on the walk from sample parent up to watchdir.

    When none exists and ``fallback_root``, returns the would-be root path
    (may not exist yet — caller should seed).
    """
    for directory in _ancestors_to_watchdir(Path(sample_path), watchdir):
        cand = directory / CONFIG_BASENAME
        if cand.is_file():
            return cand
    if fallback_root:
        return root_config_path(watchdir)
    return None


def _read_yaml_mapping(path: Path) -> Dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, yaml.YAMLError, TypeError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def load_buffer_rules_from_config_path(
    path: Path,
) -> Optional[Tuple[Tuple[str, str], ...]]:
    """
    Load ``buffer_rules`` from one config file.

    Returns ``None`` when the key is absent (caller may fall through to parent).
    Raises ``ValueError`` when the key is present but empty/invalid.
    """
    if not path.is_file():
        return None
    raw = _read_yaml_mapping(path)
    if "buffer_rules" not in raw:
        return None
    return normalize_buffer_rules(raw.get("buffer_rules"))


def load_effective_buffer_rules(
    sample_path: str | Path,
    watchdir: Path,
) -> Tuple[Tuple[str, str], ...]:
    """
    Resolve rules for ``sample_path``: nearest conf with ``buffer_rules`` wins
    (replace semantics). Falls back to ``DEFAULT_BUFFER_RULES`` if none declare rules.
    """
    for directory in _ancestors_to_watchdir(Path(sample_path), watchdir):
        cand = directory / CONFIG_BASENAME
        if not cand.is_file():
            continue
        try:
            rules = load_buffer_rules_from_config_path(cand)
        except ValueError:
            continue
        if rules is not None:
            return rules
    return tuple(DEFAULT_BUFFER_RULES)


def validate_config_text(text: str) -> Optional[str]:
    """
    Validate Configuration editor text: YAML parse + ``buffer_rules`` schema.

    Returns an error message, or ``None`` when valid. Other top-level keys are
    allowed (preserved for process-full); ``buffer_rules`` must be present and
    non-empty with valid patterns.
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        return f"Invalid YAML: {e}"
    if raw is None:
        return "Configuration is empty"
    if not isinstance(raw, dict):
        return "Configuration root must be a YAML mapping"
    if "buffer_rules" not in raw:
        return "buffer_rules key is required"
    try:
        rules = normalize_buffer_rules(raw.get("buffer_rules"))
    except (TypeError, ValueError) as e:
        return str(e)
    for i, (lhs, rhs) in enumerate(rules):
        err = validate_rule_pair(lhs, rhs)
        if err:
            return f"Rule {i + 1}: {err}"
    return None


def write_config_text(path: Path, text: str) -> None:
    """Atomic write of comment-preserving editor text (caller must validate)."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def migrate_session_rules_into_root_config(
    watchdir: Path,
    session_rules: Mapping[str, str] | List[Tuple[str, str]] | None,
) -> bool:
    """
    One-shot: if session still carries ``buffer_rules``, write them into root
    ``config.conf`` when the file is missing or only has seeded defaults.

    Returns True when a write happened.
    """
    if not session_rules:
        return False
    try:
        pairs = normalize_buffer_rules(session_rules)
    except (TypeError, ValueError):
        return False
    root = root_config_path(watchdir)
    if root.is_file():
        try:
            existing = load_buffer_rules_from_config_path(root)
        except ValueError:
            existing = None
        if existing is not None and existing != tuple(DEFAULT_BUFFER_RULES):
            # User/agent already customized root — do not overwrite.
            return False
    # Same teaching comments as a fresh seed; migration itself stays silent.
    lines = [_CONFIG_BUFFER_RULES_COMMENT_HEADER.rstrip("\n"), "buffer_rules:"]
    for lhs, rhs in pairs:
        lines.append(f'  "{lhs}": "{rhs}"')
    lines.append("")
    write_config_text(root, "\n".join(lines))
    return True
