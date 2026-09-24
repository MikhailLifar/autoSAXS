"""Regression: DAMMIF YAML keys must resolve to the correct ``dammif-*-1.cif`` for the 3D viewer."""

from __future__ import annotations

from pathlib import Path

from guisaxs_skills.liveview.services.artifacts import best_dammif_cif


def test_best_dammif_cif_new_yaml_keys_match_atsas_prefix(tmp_path: Path) -> None:
    d = tmp_path / "dammif"
    d.mkdir()
    (d / "dammif-1-1.cif").write_text("#", encoding="utf-8")
    (d / "dammif-2-1.cif").write_text("#", encoding="utf-8")
    (d / "dammif_fits.yml").write_text(
        "dammif-1:\n  chi2: 10.0\n  Rg: 1.0\ndammif-2:\n  chi2: 5.0\n  Rg: 1.0\n",
        encoding="utf-8",
    )
    out = best_dammif_cif(d)
    assert out is not None
    assert out.endswith("dammif-2-1.cif")


def test_best_dammif_cif_legacy_zero_based_yaml(tmp_path: Path) -> None:
    d = tmp_path / "dammif"
    d.mkdir()
    (d / "dammif-1-1.cif").write_text("#", encoding="utf-8")
    (d / "dammif-2-1.cif").write_text("#", encoding="utf-8")
    (d / "dammif_fits.yml").write_text(
        "dammif-0:\n  chi2: 3.0\n  Rg: 1.0\ndammif-1:\n  chi2: 5.0\n  Rg: 1.0\n",
        encoding="utf-8",
    )
    out = best_dammif_cif(d)
    assert out is not None
    assert out.endswith("dammif-1-1.cif")


def test_best_dammif_cif_without_yml_falls_back_to_newest_cif(tmp_path: Path) -> None:
    import time

    d = tmp_path / "dammif"
    d.mkdir()
    a = d / "dammif-1-1.cif"
    b = d / "dammif-2-1.cif"
    a.write_text("#", encoding="utf-8")
    time.sleep(0.05)
    b.write_text("#", encoding="utf-8")
    out = best_dammif_cif(d)
    assert out is not None
    assert out.endswith("dammif-2-1.cif")
