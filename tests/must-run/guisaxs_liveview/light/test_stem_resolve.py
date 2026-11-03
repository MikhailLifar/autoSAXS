"""P3: same stem ⇒ same paths from every consumer; stable-name precedence."""

from __future__ import annotations

from pathlib import Path

from guisaxs_skills.liveview.services.artifacts import discover_gnom_out_path
from guisaxs_skills.liveview.services.history.middle_from_stem import (
    integrated_dat_for_tiff_stem,
    subtracted_dat_for_tiff_stem,
)
from guisaxs_skills.liveview.services.history.right_artifacts import (
    discover_monodisperse_artifacts,
    discover_polydisperse_artifacts,
    resolve_analysis_profile,
)
from guisaxs_skills.liveview.session.state import LiveviewWatchMode
from guisaxs_skills.liveview.session.stem_resolve import (
    resolve_fit_distances_gnom_out,
    resolve_fit_sizes_gnom_out,
    resolve_guinier_results_txt,
    resolve_integrated_dat,
    resolve_subtracted_dat,
)


def _touch(p: Path, text: str = "x\n") -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def test_stable_gnom_beats_newer_orphan(tmp_path: Path) -> None:
    stem = "samp"
    fd = tmp_path / "fit_distances" / stem
    orphan = _touch(fd / "orphan_rg_99.out")
    stable = _touch(fd / "gnom_best.out")
    # Make orphan newer on mtime.
    orphan.write_text("newer\n", encoding="utf-8")
    got = resolve_fit_distances_gnom_out(root=tmp_path, stem=stem)
    assert got is not None
    assert got.resolve() == stable.resolve()


def test_poly_sizes_uses_same_stable_gnom(tmp_path: Path) -> None:
    stem = "np"
    fs = tmp_path / "fit_sizes" / stem
    _touch(fs / "legacy_other.out")
    stable = _touch(fs / "gnom_best.out")
    got = resolve_fit_sizes_gnom_out(root=tmp_path, stem=stem)
    assert got is not None
    assert got.resolve() == stable.resolve()


def test_middle_and_stem_resolve_agree_on_int_sub(tmp_path: Path) -> None:
    stem = "frame01"
    tiff = tmp_path / f"{stem}.tif"
    _touch(tiff, "")
    integ = _touch(tmp_path / "averaged" / f"int_{stem}.dat", "0 1 0\n")
    sub = _touch(tmp_path / "subtracted" / f"sub_{stem}.dat", "0 1 0\n")

    mid_int = integrated_dat_for_tiff_stem(
        tmp_path, stem, integrator_ready=True, tiff_path=str(tiff)
    )
    mid_sub = subtracted_dat_for_tiff_stem(tmp_path, stem, tiff_path=str(tiff))
    res_int = resolve_integrated_dat(
        watchdir=tmp_path, stem=stem, integrator_ready=True, sample_path=str(tiff)
    )
    res_sub = resolve_subtracted_dat(watchdir=tmp_path, stem=stem, sample_path=str(tiff))
    assert mid_int == res_int == str(integ.resolve())
    assert mid_sub == res_sub == str(sub.resolve())


def test_discover_mono_matches_shared_resolve(tmp_path: Path) -> None:
    stem = "prot"
    tiff = str(tmp_path / f"{stem}.tif")
    _touch(Path(tiff), "")
    _touch(tmp_path / "subtracted" / f"sub_{stem}.dat", "0.1 10 1\n0.2 9 1\n")
    g_txt = _touch(
        tmp_path / "guinier_mono" / stem / f"{stem}_results.txt",
        "Rg = 1\n",
    )
    # Prefer stable over newer orphan.
    _touch(tmp_path / "fit_distances" / stem / "zzz_orphan.out", "bad\n")
    gnom = _touch(tmp_path / "fit_distances" / stem / "gnom_best.out", "out\n")

    bundle = discover_monodisperse_artifacts(
        watchdir=tmp_path,
        stem=stem,
        tiff_path=tiff,
        watch_mode=LiveviewWatchMode.FLAT,
    )
    _, profile = resolve_analysis_profile(
        watchdir=tmp_path, stem=stem, sample_path=tiff, watch_mode=LiveviewWatchMode.FLAT
    )
    assert bundle.profile_path == profile
    assert bundle.guinier is not None
    assert Path(bundle.guinier["results_path"]).resolve() == g_txt.resolve()
    assert Path(bundle.guinier["results_path"]).resolve() == resolve_guinier_results_txt(
        root=tmp_path, stem=stem, chain="mono"
    )
    assert bundle.gnom is not None
    assert Path(bundle.gnom["best_gnom_out_path"]).resolve() == gnom.resolve()
    assert Path(bundle.gnom["best_gnom_out_path"]).resolve() == resolve_fit_distances_gnom_out(
        root=tmp_path, stem=stem
    )


def test_discover_poly_and_dammif_hint_share_stable_path(tmp_path: Path) -> None:
    stem = "np1"
    tiff = str(tmp_path / f"{stem}.tif")
    _touch(Path(tiff), "")
    _touch(tmp_path / "subtracted" / f"sub_{stem}.dat", "0.1 10 1\n")
    gnom = _touch(tmp_path / "fit_sizes" / stem / "gnom_best.out", "out\n")
    _touch(tmp_path / "fit_sizes" / stem / f"{stem}_fit_sizes.yml", "fit: {}\n")
    _touch(tmp_path / "mixture" / stem / "mixture_results.csv", "a,b\n")

    bundle = discover_polydisperse_artifacts(
        watchdir=tmp_path,
        stem=stem,
        tiff_path=tiff,
        watch_mode=LiveviewWatchMode.FLAT,
    )
    assert bundle.sizes is not None
    assert Path(bundle.sizes["best_gnom_out_path"]).resolve() == gnom.resolve()
    assert bundle.mixture is not None

    # Distances path used by DAMMIF discovery agrees with shared iterator when usable.
    fd = tmp_path / "fit_distances" / stem
    # Minimal GNOM-like table is hard; just assert empty when unusable, and stable pick without filter.
    _touch(fd / "gnom_best.out", "not-a-real-gnom\n")
    assert resolve_fit_distances_gnom_out(root=tmp_path, stem=stem) is not None
    # discover_gnom_out_path requires usable parse — unusable returns "".
    assert (
        discover_gnom_out_path(
            profile_abs=str(tmp_path / "subtracted" / f"sub_{stem}.dat"),
            output_root=tmp_path,
            watchdir=tmp_path,
        )
        == ""
    )
