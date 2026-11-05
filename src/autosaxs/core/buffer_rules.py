"""
Ordered ``buffer_rules`` matcher with ``{capture}`` patterns.

Shared SSOT for TIFF batch (``tiff_pipeline`` / ``process-full``) and liveview
``plan_for``. Outcomes:

- ``PAIRED`` — exactly one sample-expr and exactly one buffer hit
- ``NONE`` — sample-expr matched; RHS is ``""`` (explicit no-buffer sample)
- ``UNMATCHED`` — no sample-expr matched (not claimed as a sample)
- ``CONFLICT`` — multi sample-expr, or non-empty RHS with 0/>1 buffer hits

Patterns use ``*`` wildcards and zero or more ``{name}`` captures. Capture names
must agree across LHS/RHS when RHS is non-empty. ``{`` / ``}`` are reserved.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple

# Default capture-aware pairing (replaces classify-only ``*_sample*``/``*_buffer*``
# plus substring / shared-single-buffer magic). Preserves ihs / Pt ``*_sample*`` /
# ``*_buffer*`` naming without an implicit shared-buffer fallback.
DEFAULT_BUFFER_RULES: Tuple[Tuple[str, str], ...] = (
    ("*{stem}_sample*", "*{stem}_buffer*"),
)


class MatchKind(Enum):
    PAIRED = "paired"
    NONE = "none"
    UNMATCHED = "unmatched"
    CONFLICT = "conflict"


class BufferMatchConflict(RuntimeError):
    """Raised when ``match_sample_to_buffer`` yields ``CONFLICT`` (CLI / plan_for)."""

    def __init__(self, result: "MatchResult") -> None:
        self.result = result
        super().__init__(result.diagnostics)


@dataclass(frozen=True)
class MatchResult:
    kind: MatchKind
    sample: str
    buffer: Optional[str] = None
    rule_index: Optional[int] = None
    sample_expr: Optional[str] = None
    buffer_expr: Optional[str] = None
    captures: Mapping[str, str] = field(default_factory=dict)
    diagnostics: str = ""
    conflicting_exprs: Tuple[str, ...] = ()
    buffer_hits: Tuple[str, ...] = ()


_CAPTURE_TOKEN_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def normalize_buffer_rules(
    rules: Mapping[str, str] | Sequence[Tuple[str, str]] | None,
    *,
    default: Sequence[Tuple[str, str]] = DEFAULT_BUFFER_RULES,
) -> Tuple[Tuple[str, str], ...]:
    """
    Coerce YAML map / list-of-pairs into an ordered tuple of ``(lhs, rhs)``.

    Empty RHS (after strip) is allowed and means explicit no-buffer (``NONE``).
    Empty LHS is invalid. ``None`` / omitted → ``default``. Empty mapping → error.
    """
    if rules is None:
        return tuple((str(a), str(b)) for a, b in default)
    pairs: List[Tuple[str, str]] = []
    if isinstance(rules, Mapping):
        if not rules:
            raise ValueError("buffer_rules must be a non-empty mapping of sample_expr → buffer_expr")
        for sample_expr, buffer_expr in rules.items():
            pairs.append((str(sample_expr), "" if buffer_expr is None else str(buffer_expr)))
    else:
        seq = list(rules)
        if not seq:
            raise ValueError("buffer_rules must be a non-empty sequence of (sample_expr, buffer_expr)")
        for item in seq:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise TypeError(
                    "buffer_rules sequence entries must be (sample_expr, buffer_expr) pairs"
                )
            pairs.append((str(item[0]), "" if item[1] is None else str(item[1])))
    out: List[Tuple[str, str]] = []
    for lhs, rhs in pairs:
        sg = lhs.strip()
        bg = rhs.strip()
        if not sg:
            raise ValueError("buffer_rules sample expressions must be non-empty")
        out.append((sg, bg))
    return tuple(out)


def rules_as_mapping(rules: Sequence[Tuple[str, str]]) -> dict[str, str]:
    """Ordered dict view (last duplicate LHS wins — prefer unique LHS)."""
    return {lhs: rhs for lhs, rhs in rules}


def pattern_to_pool_glob(pattern: str) -> str:
    """Replace unbound ``{capture}`` tokens with ``*`` for pool-membership fnmatch."""
    out = _CAPTURE_TOKEN_RE.sub("*", pattern)
    # Collapse consecutive wildcards produced by adjacent ``*{cap}*`` forms.
    while "**" in out:
        out = out.replace("**", "*")
    return out


def validate_rule_pair(sample_expr: str, buffer_expr: str) -> Optional[str]:
    """
    Return an error message if the rule pair is invalid, else ``None``.

    Checks balanced ``{}``, capture token syntax, and that RHS capture names are
    a subset of LHS when RHS is non-empty.
    """
    lhs = (sample_expr or "").strip()
    rhs = (buffer_expr or "").strip()
    if not lhs:
        return "sample expression must be non-empty"
    for side, label in ((lhs, "sample"), (rhs, "buffer")):
        if side.count("{") != side.count("}"):
            return f"{label} expression has unbalanced braces"
        # Reject empty / invalid capture names and stray braces content
        i = 0
        while i < len(side):
            if side[i] == "{":
                j = side.find("}", i + 1)
                if j < 0:
                    return f"{label} expression has unbalanced braces"
                name = side[i + 1 : j]
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name or ""):
                    return f"{label} expression has invalid capture name {{{name}}}"
                i = j + 1
            elif side[i] == "}":
                return f"{label} expression has unbalanced braces"
            else:
                i += 1
    if rhs:
        lhs_names = set(_CAPTURE_TOKEN_RE.findall(lhs))
        rhs_names = set(_CAPTURE_TOKEN_RE.findall(rhs))
        missing = sorted(rhs_names - lhs_names)
        if missing:
            return (
                "buffer expression capture(s) not present on sample side: "
                + ", ".join(missing)
            )
    return None


def _tokenize_pattern(pattern: str) -> List[Tuple[str, str]]:
    """Tokenize into ``('lit'|'star'|'cap', value)`` pieces."""
    tokens: List[Tuple[str, str]] = []
    i = 0
    lit: List[str] = []

    def _flush_lit() -> None:
        nonlocal lit
        if lit:
            tokens.append(("lit", "".join(lit)))
            lit = []

    while i < len(pattern):
        ch = pattern[i]
        if ch == "{":
            _flush_lit()
            j = pattern.find("}", i + 1)
            if j < 0:
                raise ValueError(f"unbalanced braces in pattern {pattern!r}")
            name = pattern[i + 1 : j]
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError(f"invalid capture name {{{name}}} in {pattern!r}")
            tokens.append(("cap", name))
            i = j + 1
        elif ch == "*":
            _flush_lit()
            tokens.append(("star", "*"))
            i += 1
        elif ch == "?":
            _flush_lit()
            # Treat lone ``?`` as a single-char literal wildcard via regex later
            tokens.append(("lit", "?"))
            i += 1
        else:
            lit.append(ch)
            i += 1
    _flush_lit()
    return tokens


def _compile_pattern(pattern: str) -> Tuple[re.Pattern[str], Tuple[str, ...]]:
    """
    Compile a basename pattern with ``*`` and ``{name}`` into a regex.

    Captures bind non-empty substrings. Leading/unanchored ``*`` before a capture
    is non-greedy so ``*{stem}_sample*`` binds ``stem`` immediately before
    ``_sample``. Same capture name twice → back-reference.
    """
    tokens = _tokenize_pattern(pattern)
    names: List[str] = []
    seen: set[str] = set()
    parts: List[str] = ["^"]
    for idx, (kind, value) in enumerate(tokens):
        if kind == "star":
            # Non-greedy when a capture follows so literals after the capture win.
            nxt = tokens[idx + 1][0] if idx + 1 < len(tokens) else None
            parts.append(".*?" if nxt == "cap" else ".*")
        elif kind == "cap":
            name = value
            if name in seen:
                parts.append(f"(?P={name})")
            else:
                seen.add(name)
                names.append(name)
                nxt = tokens[idx + 1][0] if idx + 1 < len(tokens) else None
                # Before a literal: greedy (backtracks to the literal).
                # Before a trailing ``*`` / end: still greedy so ``*{stem}*`` can
                # bind the whole middle; a following ``*`` then matches empty.
                # Before ``*`` + more tokens: non-greedy so later literals win.
                if nxt == "lit" or nxt is None:
                    parts.append(f"(?P<{name}>.+)")
                elif nxt == "star":
                    nxt2 = tokens[idx + 2][0] if idx + 2 < len(tokens) else None
                    parts.append(f"(?P<{name}>.+)" if nxt2 is None else f"(?P<{name}>.+?)")
                else:
                    parts.append(f"(?P<{name}>.+)")
        else:  # lit
            if value == "?":
                parts.append(".")
            else:
                parts.append(re.escape(value))
    parts.append("$")
    return re.compile("".join(parts), re.IGNORECASE | re.DOTALL), tuple(names)


def match_basename(pattern: str, basename: str) -> Optional[dict[str, str]]:
    """Match ``basename`` against ``pattern``; return captures or ``None``."""
    try:
        rx, names = _compile_pattern(pattern)
    except ValueError:
        return None
    m = rx.match(basename)
    if not m:
        return None
    caps = {n: m.group(n) for n in names}
    if any(not v for v in caps.values()):
        return None
    return caps


def expand_pattern(pattern: str, captures: Mapping[str, str]) -> str:
    """Substitute ``{name}`` in ``pattern`` from ``captures`` (missing → leave token)."""

    def _sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name in captures:
            return captures[name]
        return m.group(0)

    return _CAPTURE_TOKEN_RE.sub(_sub, pattern)


def _basename(path: str) -> str:
    return os.path.basename(str(path))


def _diag_conflict_multi_lhs(
    sample: str,
    exprs: Sequence[str],
) -> str:
    base = _basename(sample)
    listed = ", ".join(repr(e) for e in exprs)
    return (
        f"Buffer rules conflict for sample {base!r}: matched multiple sample "
        f"expressions ({listed}). Edit buffer_rules so exactly one sample "
        f"expression matches this file."
    )


def _diag_conflict_buffers(
    sample: str,
    *,
    sample_expr: str,
    buffer_expr: str,
    expanded: str,
    hits: Sequence[str],
    captures: Mapping[str, str],
) -> str:
    base = _basename(sample)
    hit_names = [_basename(h) for h in hits]
    cap_txt = ", ".join(f"{k}={v!r}" for k, v in captures.items()) or "(none)"
    if not hits:
        return (
            f"Buffer rules conflict for sample {base!r}: rule "
            f"{sample_expr!r} → {buffer_expr!r} (expanded buffer pattern "
            f"{expanded!r}, captures: {cap_txt}) matched 0 buffer candidates. "
            f"Add a matching buffer file or fix the rule."
        )
    listed = ", ".join(repr(n) for n in hit_names)
    return (
        f"Buffer rules conflict for sample {base!r}: rule "
        f"{sample_expr!r} → {buffer_expr!r} (expanded buffer pattern "
        f"{expanded!r}, captures: {cap_txt}) matched {len(hits)} buffers "
        f"({listed}). Narrow the buffer expression so exactly one buffer matches."
    )


def match_sample_to_buffer(
    sample_path: str,
    buffer_candidates: Sequence[str],
    rules: Sequence[Tuple[str, str]],
) -> MatchResult:
    """
    Resolve one sample path against ordered ``(sample_expr, buffer_expr)`` rules.

    Multi-LHS matches are always ``CONFLICT`` (order does not pick a winner).
    """
    sample = str(sample_path)
    sample_base = _basename(sample)
    lhs_hits: List[Tuple[int, str, str, dict[str, str]]] = []
    for idx, (sample_expr, buffer_expr) in enumerate(rules):
        caps = match_basename(sample_expr, sample_base)
        if caps is None:
            continue
        lhs_hits.append((idx, sample_expr, buffer_expr, caps))

    if not lhs_hits:
        return MatchResult(
            kind=MatchKind.UNMATCHED,
            sample=sample,
            diagnostics=(
                f"No buffer_rules sample expression matched {_basename(sample)!r}."
            ),
        )

    if len(lhs_hits) > 1:
        exprs = tuple(h[1] for h in lhs_hits)
        diag = _diag_conflict_multi_lhs(sample, exprs)
        return MatchResult(
            kind=MatchKind.CONFLICT,
            sample=sample,
            diagnostics=diag,
            conflicting_exprs=exprs,
        )

    idx, sample_expr, buffer_expr, captures = lhs_hits[0]
    if not buffer_expr.strip():
        return MatchResult(
            kind=MatchKind.NONE,
            sample=sample,
            rule_index=idx,
            sample_expr=sample_expr,
            buffer_expr="",
            captures=dict(captures),
            diagnostics=(
                f"Sample {_basename(sample)!r} matched {sample_expr!r} with "
                f"explicit empty buffer (no subtraction)."
            ),
        )

    expanded = expand_pattern(buffer_expr, captures)
    hits: List[str] = []
    for cand in buffer_candidates:
        if match_basename(expanded, _basename(cand)) is not None:
            hits.append(str(cand))

    if len(hits) == 1:
        return MatchResult(
            kind=MatchKind.PAIRED,
            sample=sample,
            buffer=hits[0],
            rule_index=idx,
            sample_expr=sample_expr,
            buffer_expr=buffer_expr,
            captures=dict(captures),
            diagnostics=(
                f"Paired {_basename(sample)!r} → {_basename(hits[0])!r} "
                f"via {sample_expr!r} → {buffer_expr!r}."
            ),
            buffer_hits=(hits[0],),
        )

    diag = _diag_conflict_buffers(
        sample,
        sample_expr=sample_expr,
        buffer_expr=buffer_expr,
        expanded=expanded,
        hits=hits,
        captures=captures,
    )
    return MatchResult(
        kind=MatchKind.CONFLICT,
        sample=sample,
        rule_index=idx,
        sample_expr=sample_expr,
        buffer_expr=buffer_expr,
        captures=dict(captures),
        diagnostics=diag,
        buffer_hits=tuple(hits),
    )


def match_all_samples(
    sample_paths: Sequence[str],
    buffer_candidates: Sequence[str],
    rules: Sequence[Tuple[str, str]],
) -> List[MatchResult]:
    """Match each sample; collect all results (including conflicts)."""
    return [
        match_sample_to_buffer(s, buffer_candidates, rules) for s in sample_paths
    ]


def aggregate_conflict_diagnostics(results: Iterable[MatchResult]) -> str:
    """Join diagnostics for all ``CONFLICT`` results (CLI / modal text)."""
    parts = [r.diagnostics for r in results if r.kind == MatchKind.CONFLICT and r.diagnostics]
    return "\n".join(parts)


def raise_on_conflicts(results: Sequence[MatchResult]) -> None:
    """Raise ``BufferMatchConflict`` with aggregated diagnostics if any conflict."""
    conflicts = [r for r in results if r.kind == MatchKind.CONFLICT]
    if not conflicts:
        return
    # Prefer the first conflict as ``.result``; message aggregates all.
    msg = aggregate_conflict_diagnostics(conflicts)
    err = BufferMatchConflict(conflicts[0])
    err.args = (msg,)
    raise err


def pair_samples_with_rules(
    sample_paths: Sequence[str],
    buffer_candidates: Sequence[str],
    rules: Sequence[Tuple[str, str]],
) -> Tuple[List[Tuple[str, str]], List[str], List[str]]:
    """
    Apply rules to samples.

    Returns ``(paired, none_samples, unmatched_samples)``.
    Raises ``BufferMatchConflict`` if any sample is ``CONFLICT``.
    """
    results = match_all_samples(sample_paths, buffer_candidates, rules)
    raise_on_conflicts(results)
    paired: List[Tuple[str, str]] = []
    none_samples: List[str] = []
    unmatched: List[str] = []
    for r in results:
        if r.kind == MatchKind.PAIRED:
            assert r.buffer is not None
            paired.append((r.sample, r.buffer))
        elif r.kind == MatchKind.NONE:
            none_samples.append(r.sample)
        elif r.kind == MatchKind.UNMATCHED:
            unmatched.append(r.sample)
    return paired, none_samples, unmatched


def rules_from_session_yaml(raw: object) -> Optional[Tuple[Tuple[str, str], ...]]:
    """
    Parse liveview session ``buffer_rules`` (list of ``{sample_expr, buffer_expr}``
    or legacy mapping). Returns ``None`` if absent / invalid empty.
    """
    if raw is None:
        return None
    if isinstance(raw, Mapping):
        if not raw:
            return None
        return normalize_buffer_rules(raw)
    if isinstance(raw, list):
        pairs: List[Tuple[str, str]] = []
        for item in raw:
            if isinstance(item, Mapping):
                lhs = item.get("sample_expr", item.get("sample", ""))
                rhs = item.get("buffer_expr", item.get("buffer", ""))
                pairs.append((str(lhs), "" if rhs is None else str(rhs)))
            elif isinstance(item, (tuple, list)) and len(item) == 2:
                pairs.append((str(item[0]), "" if item[1] is None else str(item[1])))
            else:
                raise TypeError(
                    "buffer_rules list entries must be mappings with sample_expr/buffer_expr"
                )
        if not pairs:
            return None
        return normalize_buffer_rules(pairs)
    raise TypeError(f"buffer_rules must be a mapping or list, got {type(raw).__name__}")


def session_rules_to_yaml(rules: Sequence[Tuple[str, str]]) -> List[dict[str, str]]:
    """Serialize rules for ``session.yaml`` (list of pairs; supports empty RHS)."""
    return [{"sample_expr": lhs, "buffer_expr": rhs} for lhs, rhs in rules]


def seed_literal_rule_from_buffer_path(buffer_path: str | Path) -> Tuple[Tuple[str, str], ...]:
    """Migrate old single-buffer sessions: ``\"*\": \"<basename>\"``."""
    base = os.path.basename(str(buffer_path))
    return (("*", base),)
