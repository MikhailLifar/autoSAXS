"""Shape apply_context must not clobber session-owned run controls on sample switch."""

from __future__ import annotations

from pathlib import Path

import pytest
from PyQt5.QtWidgets import QApplication

from guisaxs_skills.modeling.context import ModelingContext
from guisaxs_skills.modeling.shape.window import ShapeModelingWindow


@pytest.fixture(scope="module")
def qapp():
    created = QApplication.instance() is None
    app = QApplication.instance() or QApplication([])
    yield app
    if created:
        app.quit()


def test_apply_context_preserves_n_runs_across_sample_push(qapp, tmp_path: Path):
    """Liveview context push after sample B must keep user n_runs (pending Confirm case)."""
    sample_a = tmp_path / "a"
    sample_b = tmp_path / "b"
    sample_a.mkdir()
    sample_b.mkdir()
    prof_a = sample_a / "sub_a.dat"
    prof_b = sample_b / "sub_b.dat"
    prof_a.write_text("1 1\n", encoding="utf-8")
    prof_b.write_text("1 1\n", encoding="utf-8")

    win = ShapeModelingWindow(
        ModelingContext(
            profile_path=str(prof_a),
            output_dir=str(sample_a),
            mode="dammif",
            options={"n_runs": 1},
        )
    )
    try:
        assert int(win._n_runs.value()) == 1  # noqa: SLF001
        win._n_runs.setValue(3)  # noqa: SLF001 — user set knobs in the modeling app

        # Sample switch / deferred re-push (as after busy finish + pending Confirm).
        win.apply_context(
            ModelingContext(
                profile_path=str(prof_b),
                output_dir=str(sample_b),
                mode="dammif",
                options={"n_runs": 1},  # liveview session default must not win
            )
        )

        assert int(win._n_runs.value()) == 3  # noqa: SLF001
        assert (win._pf_profile.text() or "").strip() == str(prof_b)  # noqa: SLF001
        assert int(win._options_for_confirm()["n_runs"]) == 3  # noqa: SLF001
    finally:
        win.close()
