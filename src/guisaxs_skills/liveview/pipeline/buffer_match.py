"""Liveview helpers: buffer candidate discovery + overlay-aware matching.

SSOT for “effective buffer / match outcome for the current sample”:
overlay → hierarchical ``config.conf`` rules, with the same curve-identity
probe order as ``plan_for`` (planned integrated ``.dat``, then source path).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Sequence

from autosaxs.core.buffer_rules import (
    MatchKind,
    MatchResult,
    match_sample_to_buffer,
)

from ..ingest.sample_revision import is_dat_path
from ..session.output_paths import integrated_dat_path, tiff_output_root
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


def sample_output_root_for_match(
    state: LiveviewSessionState,
    sample: Sample,
    *,
    sample_output_root: Optional[Path] = None,
) -> Path:
    """Output root used when collecting buffer candidates for ``sample``."""
    if sample_output_root is not None:
        return sample_output_root
    return tiff_output_root(
        watchdir=state.watchdir,
        tiff_path=sample.path,
        mode=state.watch_mode,
    )


def match_curve_candidates_for_sample(
    state: LiveviewSessionState,
    sample: Sample,
    *,
    sample_output_root: Optional[Path] = None,
) -> List[str]:
    """
    Ordered curve paths whose basenames are tried for buffer matching.

    Same order as ``plan_for`` for frames: planned ``int_<stem>.dat`` first
    (basename matters even before the file exists), then the source path.
    Curve boarding uses the ``.dat`` path alone.
    """
    source = sample.path
    if is_dat_path(source):
        return [source]

    root = sample_output_root_for_match(
        state, sample, sample_output_root=sample_output_root
    )
    integrator_ready = state.integrator_dir is not None and state.integrator_dir.is_dir()
    planned = str(
        integrated_dat_path(
            root=root, stem=sample.stem, integrator_ready=bool(integrator_ready)
        ).resolve()
    )
    out: List[str] = []
    for cand in (planned, source):
        key = _norm_key(cand)
        if not any(_norm_key(x) == key for x in out):
            out.append(cand)
    return out


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

    root = sample_output_root_for_match(
        state, sample, sample_output_root=sample_output_root
    )
    rules = load_effective_buffer_rules(sample.path, state.watchdir)
    candidates = collect_buffer_candidates(
        state,
        sample_output_root=root,
        exclude=[sample_curve, sample.path],
    )
    return match_sample_to_buffer(sample_curve, candidates, rules)


def resolve_liveview_match(
    state: LiveviewSessionState,
    sample: Sample,
    *,
    sample_output_root: Optional[Path] = None,
) -> MatchResult:
    """
    Effective match for UI / plan: probe curve identities until non-UNMATCHED.

    Falls through from planned integrated curve to source path only when the
    first probe is ``UNMATCHED`` (same as ``plan_for``).
    """
    root = sample_output_root_for_match(
        state, sample, sample_output_root=sample_output_root
    )
    curves = match_curve_candidates_for_sample(
        state, sample, sample_output_root=root
    )
    primary = resolve_match_for_sample(
        state,
        sample,
        sample_curve=curves[0],
        sample_output_root=root,
    )
    if primary.kind != MatchKind.UNMATCHED or len(curves) < 2:
        return primary
    return resolve_match_for_sample(
        state,
        sample,
        sample_curve=curves[1],
        sample_output_root=root,
    )


def sample_needs_manual_buffer(
    state: LiveviewSessionState,
    sample: Sample | None,
    *,
    sample_output_root: Optional[Path] = None,
) -> bool:
    """
    True only when effective match is ``UNMATCHED`` (coach: pulse Set buffer).

    ``PAIRED`` / ``NONE`` / soft-wait / conflict are covered by rules or overlay
    — do not push manual buffer.
    """
    if sample is None:
        return False
    if sample.key in (state.buffer_overrides or {}):
        return False
    try:
        match = resolve_liveview_match(
            state, sample, sample_output_root=sample_output_root
        )
    except Exception:
        return False
    return match.kind == MatchKind.UNMATCHED


def resolved_buffer_path_for_sample(
    state: LiveviewSessionState,
    sample: Sample | None,
    *,
    sample_curve: str | None = None,
    sample_output_root: Optional[Path] = None,
) -> Optional[str]:
    """
    Absolute buffer path for middle compare / UI, or None if not PAIRED.

    When ``sample_curve`` is omitted, uses :func:`resolve_liveview_match`
    (integrated then source). Explicit ``sample_curve`` keeps a single probe.
    """
    if sample is None:
        return None
    if sample_curve is None:
        result = resolve_liveview_match(
            state, sample, sample_output_root=sample_output_root
        )
    else:
        result = resolve_match_for_sample(
            state,
            sample,
            sample_curve=sample_curve,
            sample_output_root=sample_output_root,
        )
    if result.kind == MatchKind.PAIRED and result.buffer:
        return result.buffer
    return None


def effective_buffer_path_for_sample(
    state: LiveviewSessionState,
    sample: Sample | None,
    *,
    sample_output_root: Optional[Path] = None,
) -> Optional[Path]:
    """
    On-disk buffer for the current sample when pairing resolved one.

    Shared gate for Buffer pane preview, subtraction wizard, and middle dual
    layout — not the last-picked ``buffer_dat_path`` hint.
    """
    resolved = resolved_buffer_path_for_sample(
        state, sample, sample_output_root=sample_output_root
    )
    if not resolved:
        return None
    try:
        p = Path(resolved).expanduser().resolve()
    except OSError:
        p = Path(resolved)
    return p if p.is_file() else None


def _norm_key(path: str) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except OSError:
        return str(path).strip()
