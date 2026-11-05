"""Liveview helpers: buffer candidate discovery + overlay-aware matching."""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

from autosaxs.core.buffer_rules import (
    MatchKind,
    MatchResult,
    match_sample_to_buffer,
)

from ..session.sample import Sample
from ..session.state import LiveviewSessionState
from ..session.watchdir_config import load_effective_buffer_rules


def collect_buffer_candidates(
    state: LiveviewSessionState,
    *,
    sample_output_root: Optional[Path] = None,
    exclude: Sequence[str] | None = None,
) -> List[str]:
    """
    Discover ``.dat`` buffer candidates under the watchdir / averaged trees.

    Non-recursive under watchdir root and ``averaged/``. Also includes overlay
    and last-picked hint paths when present. ``exclude`` paths (the sample
    curve itself) are omitted.
    """
    exclude_keys = {_norm_key(p) for p in (exclude or ()) if p}
    found: dict[str, str] = {}

    def _add(path: Path) -> None:
        try:
            if not path.is_file():
                return
            if path.suffix.lower() != ".dat":
                return
            key = _norm_key(str(path))
            if key in exclude_keys:
                return
            found[key] = str(path.resolve())
        except OSError:
            return

    wd = state.watchdir.expanduser().resolve()
    roots: List[Path] = [wd, wd / "averaged"]
    if sample_output_root is not None:
        try:
            root = sample_output_root.expanduser().resolve()
            roots.append(root)
            roots.append(root / "averaged")
        except OSError:
            pass

    for root in roots:
        try:
            if not root.is_dir():
                continue
            for p in sorted(root.iterdir()):
                _add(p)
        except OSError:
            continue

    if state.buffer_dat_path is not None:
        _add(Path(state.buffer_dat_path))
    for buf in (state.buffer_overrides or {}).values():
        _add(Path(buf))

    return list(found.values())


def resolve_match_for_sample(
    state: LiveviewSessionState,
    sample: Sample,
    *,
    sample_curve: str,
    sample_output_root: Optional[Path] = None,
) -> MatchResult:
    """
    Overlay wins: if ``sample.key`` has a buffer override → force ``PAIRED``.

    Otherwise match against effective hierarchical ``config.conf`` rules.
    Raises nothing — CONFLICT is returned as ``MatchResult`` (caller may raise).
    """
    key = sample.key
    override = (state.buffer_overrides or {}).get(key)
    if override:
        try:
            buf = Path(override).expanduser().resolve()
        except OSError:
            buf = Path(override)
        if buf.is_file():
            return MatchResult(
                kind=MatchKind.PAIRED,
                sample=str(sample_curve),
                buffer=str(buf),
                diagnostics=(
                    f"Session overlay: paired {Path(sample_curve).name!r} → "
                    f"{buf.name!r}."
                ),
                buffer_hits=(str(buf),),
            )

    rules = load_effective_buffer_rules(sample.path, state.watchdir)
    candidates = collect_buffer_candidates(
        state,
        sample_output_root=sample_output_root,
        exclude=[sample_curve, sample.path],
    )
    return match_sample_to_buffer(sample_curve, candidates, rules)


def resolved_buffer_path_for_sample(
    state: LiveviewSessionState,
    sample: Sample | None,
    *,
    sample_curve: str | None = None,
    sample_output_root: Optional[Path] = None,
) -> Optional[str]:
    """Absolute buffer path for middle compare / UI, or None if not PAIRED."""
    if sample is None:
        return None
    curve = sample_curve or sample.path
    result = resolve_match_for_sample(
        state,
        sample,
        sample_curve=curve,
        sample_output_root=sample_output_root,
    )
    if result.kind == MatchKind.PAIRED and result.buffer:
        return result.buffer
    return None


def _norm_key(path: str) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(path).strip()
