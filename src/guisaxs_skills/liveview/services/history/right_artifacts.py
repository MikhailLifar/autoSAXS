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

from ...ingest.sample_revision import is_dat_path
from ...session.output_paths import (
    analysis_output_root,
    dammif_dir,
    denss_dir,
    fit_distances_dir,
    fit_sizes_dir,
    guinier_mono_dir,
    guinier_poly_dir,
    integrated_dat_path,
    mixture_dir,
    model_bodies_dir,
    subtracted_dat_path,
    tiff_output_root,
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


def _resolve_profile(
    *,
    watchdir: Path,
    stem: str,
    tiff_path: str,
    watch_mode: LiveviewWatchMode,
) -> tuple[Path, str]:
    from ...ingest.curve_classify import usable_analysis_curve_path

    if is_dat_path(tiff_path or ""):
        root = watchdir.expanduser().resolve()
    else:
        root = tiff_output_root(watchdir=watchdir, tiff_path=tiff_path, mode=watch_mode)
    sub = subtracted_dat_path(root=root, stem=stem)
    integ = integrated_dat_path(root=root, stem=stem, integrator_ready=True)
    candidates: list[Path] = []
    if is_dat_path(tiff_path or "") and Path(tiff_path).is_file():
        candidates.append(Path(tiff_path).expanduser().resolve())
    candidates.append(sub)
    candidates.append(integ)
    profile_path = ""
    for cand in candidates:
        profile_path = usable_analysis_curve_path(cand)
        if profile_path:
            break
    return root, profile_path


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
    _, profile_path = _resolve_profile(
        watchdir=watchdir, stem=stem, tiff_path=tiff_path, watch_mode=watch_mode
    )
    bundle = MonodisperseArtifactBundle(
        profile_path=profile_path,
        output_root=root,
        stem=stem,
    )
    gstem = guinier_mono_dir(root) / stem
    if gstem.is_dir():
        for txt in sorted(gstem.glob("*_results.txt"), key=lambda p: p.stat().st_mtime, reverse=True):
            if "kratky" in txt.name.lower():
                continue
            bundle.guinier = {"results_path": str(txt)}
            break
    fd = fit_distances_dir(root) / stem
    gnom_out = None
    for cand in (
        fd / "gnom_best.out",
        fd / f"{stem}_gnom.out",
        fd / f"{stem}.out",
        fd / "datgnom_best.out",  # legacy auto-path name
    ):
        if cand.is_file():
            gnom_out = cand
            break
    if gnom_out is None:
        outs = sorted(fd.glob("*.out"), key=lambda p: p.stat().st_mtime, reverse=True)
        gnom_out = outs[0] if outs else fd / "gnom_best.out"
    if gnom_out.is_file():
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
    if mode != MonodisperseShapeMode.NONE:
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
    _, profile_path = _resolve_profile(
        watchdir=watchdir, stem=stem, tiff_path=tiff_path, watch_mode=watch_mode
    )
    bundle = PolydisperseArtifactBundle(
        profile_path=profile_path,
        output_root=root,
        stem=stem,
    )
    gstem = guinier_poly_dir(root) / stem
    if gstem.is_dir():
        for txt in sorted(gstem.glob("*_results.txt"), key=lambda p: p.stat().st_mtime, reverse=True):
            if "kratky" in txt.name.lower():
                continue
            bundle.guinier = {"results_path": str(txt)}
            break
    fs = fit_sizes_dir(root) / stem
    if fs.is_dir():
        gnom = ""
        outs = sorted(fs.glob("*.out"), key=lambda p: p.stat().st_mtime, reverse=True)
        if outs:
            gnom = str(outs[0])
        payload: Dict[str, Any] = {
            "atsas_fit_ok": True,
            "output_subdir": str(fs),
            "best_gnom_out_path": gnom,
        }
        best_yml = list(fs.glob("*_fit_sizes_best.yml"))
        if best_yml:
            payload["best_summary_path"] = str(best_yml[0])
        q_yml = list(fs.glob("*_fit_sizes_quality.yml"))
        if q_yml:
            payload["quality_passport_path"] = str(q_yml[0])
        bundle.sizes = payload
    if mixture_mode == PolydisperseMixtureMode.MIXTURE:
        mx = mixture_dir(root) / stem
        if mx.is_dir():
            csvs = list(mx.glob("mixture_results.csv"))
            bundle.mixture = {
                "output_subdir": str(mx),
                "results_csv_path": str(csvs[0]) if csvs else "",
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
        right.apply_polydisperse_bundle(bundle_p)
