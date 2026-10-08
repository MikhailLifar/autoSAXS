"""Regression: bare directories must unwrap for extension-constrained path expressions."""

from __future__ import annotations

from pathlib import Path

import pytest

from autosaxs.core.path_expression import (
    DatPathExpression,
    PathExpression,
    TiffPathExpression,
)


def test_dat_path_expression_bare_directory_expands_to_dat_files(tmp_path: Path) -> None:
    (tmp_path / "a.dat").write_text("1 2 3\n", encoding="utf-8")
    (tmp_path / "b.dat").write_text("4 5 6\n", encoding="utf-8")
    (tmp_path / "skip.txt").write_text("x\n", encoding="utf-8")

    got = DatPathExpression(str(tmp_path)).unwrap()
    assert [Path(p).name for p in got] == ["a.dat", "b.dat"]
    assert all(Path(p).is_file() for p in got)


def test_dat_path_expression_glob_still_works(tmp_path: Path) -> None:
    (tmp_path / "a.dat").write_text("1 2 3\n", encoding="utf-8")
    (tmp_path / "b.dat").write_text("4 5 6\n", encoding="utf-8")

    got = DatPathExpression(str(tmp_path / "*.dat")).unwrap()
    assert {Path(p).name for p in got} == {"a.dat", "b.dat"}


def test_dat_path_expression_empty_directory_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / "only.txt").write_text("x\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        DatPathExpression(str(empty)).unwrap()


def test_tiff_path_expression_bare_directory_expands(tmp_path: Path) -> None:
    (tmp_path / "a.tif").write_bytes(b"tif")
    (tmp_path / "b.tiff").write_bytes(b"tiff")
    (tmp_path / "c.png").write_bytes(b"png")

    got = TiffPathExpression(str(tmp_path)).unwrap()
    assert [Path(p).name for p in got] == ["a.tif", "b.tiff"]


def test_bare_path_expression_still_returns_directory(tmp_path: Path) -> None:
    got = PathExpression(str(tmp_path)).unwrap()
    assert got == [str(tmp_path.resolve())]
