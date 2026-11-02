"""
Unified stem → on-disk artifact path resolve for liveview.

Module-level pure helpers only — no SampleAnalysisContext, no artifact fields on
Sample / history.yaml. Same ``(watchdir, stem, sample_path, mode)`` ⇒ same paths
for middle, ``discover_*``, controllers, and modeling consumers.

Stable names win. Residual newest-mtime globs are documented per helper and used
only when no stable candidate exists (legacy trees).
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Iterator, Optional

from ..ingest.sample_revision import is_dat_path
from .output_paths import (
    analysis_output_root,
    averaged_dir,
    fit_distances_dir,
    fit_sizes_dir,
    guinier_mono_dir,
    guinier_poly_dir,
    integrated_dat_path,
    mixture_dir,
    subtracted_dat_path,
    tiff_output_root,
)
from .state import LiveviewIntakeMode, LiveviewWatchMode

# ---------------------------------------------------------------------------
# Stable-name tables (skill SSOT names first; legacy aliases after)
# ---------------------------------------------------------------------------

# fit_distances / fit_sizes write ``gnom_best.out``. Legacy DATGNOM auto path and
# per-stem symlink names remain for older trees.
_FIT_DISTANCES_GNOM_STABLE = (
    "gnom_best.out",
    "{stem}_gnom.out",
    "{stem}.out",
    "datgnom_best.out",
)

_FIT_SIZES_GNOM_STABLE = (
    "gnom_best.out",
    "{stem}_gnom.out",
    "{stem}.out",
)

# Current fit_sizes YAML; ``*_fit_sizes_best/quality.yml`` are legacy discover globs.
_FIT_SIZES_HANDOFF_STABLE = ("{stem}_fit_sizes.yml",)
_FIT_SIZES_LOG_STABLE = ("{stem}_fit_sizes_log.yml",)
_FIT_SIZES_HANDOFF_LEGACY_GLOBS = ("*_fit_sizes_quality.yml", "*_fit_sizes_best.yml")
_FIT_SIZES_LOG_LEGACY_GLOBS = ("*_fit_sizes_best.yml", "*_fit_sizes_log.yml")


def profile_sample_stem(profile_abs: str) -> str:
    """Strip ``sub_`` / ``int_`` prefixes from a profile basename stem."""
    stem = Path(profile_abs or "").stem
    for prefix in ("sub_", "int_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix) :]
    return stem


def resolve_output_root(
    *,
    watchdir: Path,
    sample_path: str = "",
    mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
    boarding: LiveviewIntakeMode | None = None,
) -> Path:
    """Analysis / TIFF output root for this sample path (same rules as ``analysis_output_root``)."""
    return analysis_output_root(
        watchdir=watchdir,
        sample_path=(sample_path or "").strip(),
        mode=mode,
        boarding=boarding,
    )


def resolve_integrated_dat(
    *,
    watchdir: Path,
    stem: str,
    integrator_ready: bool,
    sample_path: str = "",
    mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
) -> str:
    """Existing ``int_<stem>.dat`` under the sample output root, else empty string."""
    st = (stem or "").strip()
    if not st:
        return ""
    root = tiff_output_root(watchdir=watchdir, tiff_path=sample_path, mode=mode)
    p = integrated_dat_path(root=root, stem=st, integrator_ready=integrator_ready)
    return str(p.resolve()) if p.is_file() else ""


def resolve_subtracted_dat(
    *,
    watchdir: Path,
    stem: str,
    sample_path: str = "",
    mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
) -> str:
    """Existing ``subtracted/sub_<stem>.dat`` under the sample output root, else empty string."""
    st = (stem or "").strip()
    if not st:
        return ""
    root = tiff_output_root(watchdir=watchdir, tiff_path=sample_path, mode=mode)
    p = subtracted_dat_path(root=root, stem=st)
    return str(p.resolve()) if p.is_file() else ""


def resolve_analysis_profile(
    *,
    watchdir: Path,
    stem: str,
    sample_path: str = "",
    watch_mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
) -> tuple[Path, str]:
    """
    Analysis root + usable profile for **this** stem only.

    Never consults session ``last_*``. Proxy / calibrant curves yield empty ``profile_path``.
    Precedence: boarded ``.dat`` path → ``sub_<stem>.dat`` → real ``averaged/int_*.dat``
    → integrator-ready integrated fallback (may be proxy only when that is all that exists).
    """
    from ..ingest.curve_classify import usable_analysis_curve_path

    tiff_path = (sample_path or "").strip()
    if is_dat_path(tiff_path):
        root = watchdir.expanduser().resolve()
    else:
        root = tiff_output_root(watchdir=watchdir, tiff_path=tiff_path, mode=watch_mode)
    st = (stem or "").strip()
    sub = subtracted_dat_path(root=root, stem=st)
    # Prefer real averaged/; never treat averaged_proxy/ as an analysis default.
    integ_real = averaged_dir(root) / f"int_{st}.dat"
    integ_fallback = integrated_dat_path(root=root, stem=st, integrator_ready=True)
    candidates: list[Path] = []
    if is_dat_path(tiff_path) and Path(tiff_path).is_file():
        candidates.append(Path(tiff_path).expanduser().resolve())
    candidates.append(sub)
    candidates.append(integ_real)
    if integ_fallback != integ_real:
        candidates.append(integ_fallback)
    profile_path = ""
    for cand in candidates:
        profile_path = usable_analysis_curve_path(cand)
        if profile_path:
            break
    return root, profile_path


def _stable_under(subdir: Path, stem: str, patterns: Iterable[str]) -> Iterator[Path]:
    st = (stem or "").strip()
    for patt in patterns:
        name = patt.format(stem=st)
        yield subdir / name


def _newest_glob(subdir: Path, pattern: str) -> list[Path]:
    if not subdir.is_dir():
        return []
    return sorted(subdir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)


def iter_fit_distances_gnom_candidates(*, root: Path, stem: str) -> Iterator[Path]:
    """
    Ordered GNOM/DATGNOM ``.out`` candidates under ``fit_distances/<stem>/``.

    Stable names first; then residual newest-mtime ``*.out`` not already yielded
    (legacy Rg-stamped / orphan outs).
    """
    st = (stem or "").strip()
    fd = fit_distances_dir(root) / st
    seen: set[str] = set()
    for cand in _stable_under(fd, st, _FIT_DISTANCES_GNOM_STABLE):
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        yield cand
    for cand in _newest_glob(fd, "*.out"):
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        yield cand


def resolve_fit_distances_gnom_out(*, root: Path, stem: str) -> Optional[Path]:
    """First existing fit_distances GNOM ``.out`` (stable names, then newest-glob fallback)."""
    for cand in iter_fit_distances_gnom_candidates(root=root, stem=stem):
        if cand.is_file():
            return cand.resolve()
    return None


def iter_fit_sizes_gnom_candidates(*, root: Path, stem: str) -> Iterator[Path]:
    """
    Ordered sizes GNOM ``.out`` candidates under ``fit_sizes/<stem>/``.

    Same stable-name table as distances (``gnom_best.out`` SSOT); residual
    newest-mtime ``*.out`` fallback for legacy trees.
    """
    st = (stem or "").strip()
    fs = fit_sizes_dir(root) / st
    seen: set[str] = set()
    for cand in _stable_under(fs, st, _FIT_SIZES_GNOM_STABLE):
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        yield cand
    for cand in _newest_glob(fs, "*.out"):
        key = str(cand)
        if key in seen:
            continue
        seen.add(key)
        yield cand


def resolve_fit_sizes_gnom_out(*, root: Path, stem: str) -> Optional[Path]:
    """First existing fit_sizes GNOM ``.out`` (stable names, then newest-glob fallback)."""
    for cand in iter_fit_sizes_gnom_candidates(root=root, stem=stem):
        if cand.is_file():
            return cand.resolve()
    return None


def resolve_guinier_results_txt(*, root: Path, stem: str, chain: str) -> Optional[Path]:
    """
    Guinier ``*_results.txt`` for ``guinier_{mono|poly}/<stem>/``.

    Stable preference: ``{stem}_results.txt``. Residual fallback: newest
    ``*_results.txt`` excluding kratky (pre-stable or renamed trees).
    """
    st = (stem or "").strip()
    if not st:
        return None
    chain_l = (chain or "").strip().lower()
    if chain_l == "poly":
        gstem = guinier_poly_dir(root) / st
    else:
        gstem = guinier_mono_dir(root) / st
    stable = gstem / f"{st}_results.txt"
    if stable.is_file():
        return stable.resolve()
    # Residual newest-glob: no single stable name when stem≠basename prefix.
    for txt in _newest_glob(gstem, "*_results.txt"):
        if "kratky" in txt.name.lower():
            continue
        return txt.resolve()
    return None


def resolve_fit_sizes_handoff_yml(*, root: Path, stem: str) -> Optional[Path]:
    """``{stem}_fit_sizes.yml`` handoff / quality passport; legacy quality/best globs next."""
    st = (stem or "").strip()
    fs = fit_sizes_dir(root) / st
    for cand in _stable_under(fs, st, _FIT_SIZES_HANDOFF_STABLE):
        if cand.is_file():
            return cand.resolve()
    for patt in _FIT_SIZES_HANDOFF_LEGACY_GLOBS:
        for cand in _newest_glob(fs, patt):
            if cand.is_file():
                return cand.resolve()
    return None


def resolve_fit_sizes_log_yml(*, root: Path, stem: str) -> Optional[Path]:
    """``{stem}_fit_sizes_log.yml``; legacy ``*_fit_sizes_best.yml`` next."""
    st = (stem or "").strip()
    fs = fit_sizes_dir(root) / st
    for cand in _stable_under(fs, st, _FIT_SIZES_LOG_STABLE):
        if cand.is_file():
            return cand.resolve()
    for patt in _FIT_SIZES_LOG_LEGACY_GLOBS:
        for cand in _newest_glob(fs, patt):
            if cand.is_file():
                return cand.resolve()
    return None


def resolve_mixture_results_csv(*, root: Path, stem: str) -> Optional[Path]:
    """``mixture/<stem>/mixture_results.csv`` when present (stable name only)."""
    st = (stem or "").strip()
    if not st:
        return None
    mx = mixture_dir(root) / st
    csv = mx / "mixture_results.csv"
    if csv.is_file():
        return csv.resolve()
    return None
