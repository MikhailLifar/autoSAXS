"""
Sole owner of right-column analysis artifact discovery and presentation entry.

Live skill results and history/disk loads both go through ``present_right``.
Presenters keep ``_ingest_*`` as the paint leaf.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

from ...session.output_paths import (
    analysis_output_root,
    dammif_dir,
    denss_dir,
    fit_distances_dir,
    fit_sizes_dir,
    mixture_dir,
    model_bodies_dir,
)
from ...session import stem_resolve as _stem_resolve
from ...session.stem_resolve import (
    resolve_fit_distances_gnom_out,
    resolve_fit_sizes_gnom_out,
    resolve_fit_sizes_handoff_yml,
    resolve_fit_sizes_log_yml,
    resolve_guinier_results_txt,
    resolve_mixture_results_csv,
)
from ...session.state import (
    LiveviewSessionState,
    LiveviewWatchMode,
    MonodisperseShapeMode,
    PolydisperseMixtureMode,
)


class RightPresentSource(str, Enum):
    LIVE = "live"
    DISK = "disk"


@dataclass
class MonodisperseArtifactBundle:
    profile_path: str = ""
    output_root: Optional[Path] = None
    guinier: Optional[Dict[str, Any]] = None
    gnom: Optional[Dict[str, Any]] = None
    shape_mode: Optional[str] = None  # dammif|bodies|denss when loading shape from disk
    stem: str = ""
    inferred_shape_mode: Optional[MonodisperseShapeMode] = None


@dataclass
class PolydisperseArtifactBundle:
    profile_path: str = ""
    output_root: Optional[Path] = None
    guinier: Optional[Dict[str, Any]] = None
    sizes: Optional[Dict[str, Any]] = None
    mixture: Optional[Dict[str, Any]] = None
    stem: str = ""
    inferred_mixture_mode: Optional[PolydisperseMixtureMode] = None


def resolve_analysis_profile(
    *,
    watchdir: Path,
    stem: str,
    sample_path: str = "",
    watch_mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
    tiff_path: str = "",
) -> tuple[Path, str]:
    """Delegate to ``session.stem_resolve`` (``tiff_path`` alias kept for callers)."""
    return _stem_resolve.resolve_analysis_profile(
        watchdir=watchdir,
        stem=stem,
        sample_path=sample_path or tiff_path,
        watch_mode=watch_mode,
    )


# Back-compat alias for older call sites / tests.
_resolve_profile = resolve_analysis_profile


def _stem_has_shape_artifacts(root: Path, stem: str, mode: MonodisperseShapeMode) -> bool:
    st = (stem or "").strip()
    if not st or mode == MonodisperseShapeMode.NONE:
        return False
    if mode == MonodisperseShapeMode.DAMMIF:
        dam = dammif_dir(root) / st
        return dam.is_dir() and (
            any(dam.glob("dammif-*.cif")) or (dam / "dammif_fits.yml").is_file()
        )
    if mode == MonodisperseShapeMode.BODIES:
        fb = model_bodies_dir(root) / st
        return fb.is_dir() and (
            (fb / "bodies_fits.yml").is_file() or any(fb.glob("*.fir"))
        )
    if mode == MonodisperseShapeMode.DENSS:
        dens = denss_dir(root) / st
        return dens.is_dir() and (
            any(dens.glob("*.mrc"))
            or any(dens.glob("*_denss_input.dat"))
            or any(
                p.is_dir() and any(p.glob("*_avg.mrc"))
                for p in dens.iterdir()
                if p.is_dir()
            )
        )
    return False


def infer_shape_mode_from_disk(root: Path, stem: str) -> Optional[MonodisperseShapeMode]:
    """Exclusive winner among on-disk shape dirs; None if ambiguous or empty."""
    dam = dammif_dir(root) / stem
    fb = model_bodies_dir(root) / stem
    dens = denss_dir(root) / stem
    has_dam = dam.is_dir() and (
        any(dam.glob("dammif-*.cif")) or (dam / "dammif_fits.yml").is_file()
    )
    has_bod = fb.is_dir() and (
        (fb / "bodies_fits.yml").is_file() or any(fb.glob("*.fir"))
    )
    has_denss = dens.is_dir() and (
        any(dens.glob("*.mrc"))
        or any(dens.glob("*_denss_input.dat"))
        or any(p.is_dir() and any(p.glob("*_avg.mrc")) for p in dens.iterdir() if p.is_dir())
    )
    if has_denss and not has_dam and not has_bod:
        return MonodisperseShapeMode.DENSS
    if has_dam and not has_bod and not has_denss:
        return MonodisperseShapeMode.DAMMIF
    if has_bod and not has_dam and not has_denss:
        return MonodisperseShapeMode.BODIES
    return None


def discover_monodisperse_artifacts(
    *,
    watchdir: Path,
    stem: str,
    tiff_path: str = "",
    watch_mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
    shape_mode: MonodisperseShapeMode = MonodisperseShapeMode.NONE,
) -> MonodisperseArtifactBundle:
    root = analysis_output_root(watchdir=watchdir, sample_path=tiff_path, mode=watch_mode)
    _, profile_path = resolve_analysis_profile(
        watchdir=watchdir, stem=stem, sample_path=tiff_path, watch_mode=watch_mode
    )
    bundle = MonodisperseArtifactBundle(
        profile_path=profile_path,
        output_root=root,
        stem=stem,
    )
    guinier_txt = resolve_guinier_results_txt(root=root, stem=stem, chain="mono")
    if guinier_txt is not None:
        bundle.guinier = {"results_path": str(guinier_txt)}
    fd = fit_distances_dir(root) / stem
    gnom_out = resolve_fit_distances_gnom_out(root=root, stem=stem)
    if gnom_out is not None:
        bundle.gnom = {
            "best_gnom_out_path": str(gnom_out),
            "atsas_fit_ok": True,
            "output_subdir": str(fd),
        }
    mode = shape_mode
    inferred = None
    if mode == MonodisperseShapeMode.NONE:
        inferred = infer_shape_mode_from_disk(root, stem)
        if inferred is not None:
            mode = inferred
            bundle.inferred_shape_mode = inferred
    # Only paint shape when **this stem** has artifacts (session mode alone is not enough).
    if mode != MonodisperseShapeMode.NONE and _stem_has_shape_artifacts(root, stem, mode):
        bundle.shape_mode = mode.value
    return bundle


def discover_polydisperse_artifacts(
    *,
    watchdir: Path,
    stem: str,
    tiff_path: str = "",
    watch_mode: LiveviewWatchMode = LiveviewWatchMode.FLAT,
    mixture_mode: PolydisperseMixtureMode = PolydisperseMixtureMode.NONE,
) -> PolydisperseArtifactBundle:
    root = analysis_output_root(watchdir=watchdir, sample_path=tiff_path, mode=watch_mode)
    _, profile_path = resolve_analysis_profile(
        watchdir=watchdir, stem=stem, sample_path=tiff_path, watch_mode=watch_mode
    )
    bundle = PolydisperseArtifactBundle(
        profile_path=profile_path,
        output_root=root,
        stem=stem,
    )
    guinier_txt = resolve_guinier_results_txt(root=root, stem=stem, chain="poly")
    if guinier_txt is not None:
        bundle.guinier = {"results_path": str(guinier_txt)}
    fs = fit_sizes_dir(root) / stem
    gnom_out = resolve_fit_sizes_gnom_out(root=root, stem=stem)
    if fs.is_dir():
        payload: Dict[str, Any] = {
            "atsas_fit_ok": True,
            "output_subdir": str(fs),
            "best_gnom_out_path": str(gnom_out) if gnom_out is not None else "",
        }
        log_yml = resolve_fit_sizes_log_yml(root=root, stem=stem)
        if log_yml is not None:
            payload["best_summary_path"] = str(log_yml)
        handoff = resolve_fit_sizes_handoff_yml(root=root, stem=stem)
        if handoff is not None:
            payload["quality_passport_path"] = str(handoff)
        bundle.sizes = payload
    mode = mixture_mode
    mx = mixture_dir(root) / stem
    mix_csv = resolve_mixture_results_csv(root=root, stem=stem)
    has_mix = mix_csv is not None
    # Mirror mono shape: when session mode is still NONE, paint from exclusive disk artifacts.
    if mode == PolydisperseMixtureMode.NONE and has_mix:
        mode = PolydisperseMixtureMode.MIXTURE
        bundle.inferred_mixture_mode = PolydisperseMixtureMode.MIXTURE
    if mode == PolydisperseMixtureMode.MIXTURE and has_mix:
        bundle.mixture = {
            "output_subdir": str(mx),
            "results_csv_path": str(mix_csv),
        }
    return bundle


def present_right(
    right: Any,
    *,
    state: LiveviewSessionState,
    sample_path: str,
    stem: str,
    watch_mode: LiveviewWatchMode,
    source: RightPresentSource,
    result: Optional[dict] = None,
    skill_name: str = "",
    session: Any = None,
) -> None:
    """
    Single entry for right analysis presentation.

    ``source=disk``: clear previews, discover on-disk artifacts, apply.
    ``source=live``: incremental apply from skill ``result`` (no full clear).
    """
    if source == RightPresentSource.LIVE:
        if isinstance(result, dict):
            right.ingest_skill_result(result, skill_name=skill_name or str(result.get("skill_name") or ""))
        return

    right.clear_output_previews()
    if not (state.monodisperse_armed or state.polydisperse_armed) or not (stem or "").strip():
        return
    stem = stem.strip()
    tiff_path = sample_path or ""
    if state.monodisperse_armed:
        bundle = discover_monodisperse_artifacts(
            watchdir=state.watchdir,
            stem=stem,
            tiff_path=tiff_path,
            watch_mode=watch_mode,
            shape_mode=state.monodisperse_shape_mode,
        )
        if bundle.inferred_shape_mode is not None and session is not None:
            session.apply_inferred_shape_mode(bundle.inferred_shape_mode)
        elif bundle.inferred_shape_mode is not None and state.monodisperse_shape_mode == MonodisperseShapeMode.NONE:
            state.monodisperse_shape_mode = bundle.inferred_shape_mode
        right.apply_monodisperse_bundle(bundle)
    if state.polydisperse_armed:
        bundle_p = discover_polydisperse_artifacts(
            watchdir=state.watchdir,
            stem=stem,
            tiff_path=tiff_path,
            watch_mode=watch_mode,
            mixture_mode=state.polydisperse_mixture_mode,
        )
        if bundle_p.inferred_mixture_mode is not None and session is not None:
            session.apply_inferred_mixture_mode(bundle_p.inferred_mixture_mode)
        elif (
            bundle_p.inferred_mixture_mode is not None
            and state.polydisperse_mixture_mode == PolydisperseMixtureMode.NONE
        ):
            state.polydisperse_mixture_mode = bundle_p.inferred_mixture_mode
        right.apply_polydisperse_bundle(bundle_p)
