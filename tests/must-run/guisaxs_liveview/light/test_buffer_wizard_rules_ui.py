"""Buffer rules UI lives in the buffer wizard, not the left Buffer pane."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PyQt5.QtWidgets")

from PyQt5.QtWidgets import QApplication, QGroupBox, QRadioButton

from guisaxs_skills.liveview.session.api import LiveviewSession
from guisaxs_skills.liveview.ui.panels.left import LiveviewLeftPanel
from guisaxs_skills.liveview.ui.wizards.buffer_rules import BufferRulesStrip
from guisaxs_skills.liveview.ui.wizards.left import BufferWizardDialog
from guisaxs_skills.logic.session_state import SessionPathHints


@pytest.fixture(scope="module")
def qapp():
    created = QApplication.instance() is None
    app = QApplication.instance() or QApplication([])
    yield app
    if created:
        app.quit()


def test_left_buffer_pane_has_no_rules_strip(qapp, tmp_path: Path) -> None:
    session = LiveviewSession(watchdir=tmp_path, load_settings=False)
    panel = LiveviewLeftPanel(session=session)
    assert panel.findChildren(BufferRulesStrip) == []
    assert "Edit rules" not in panel._buf_hint.text()


def test_buffer_wizard_session_settings_host_rules_and_q_mode(qapp, tmp_path: Path) -> None:
    session = LiveviewSession(watchdir=tmp_path, load_settings=False)
    session.state.buffer_rules = [("*{stem}_sample*", "*{stem}_buffer*")]
    wiz = BufferWizardDialog(
        watchdir=tmp_path,
        hints=SessionPathHints(),
        session=session,
        parent=None,
    )
    strips = wiz.findChildren(BufferRulesStrip)
    assert len(strips) == 1
    titles = {g.title() for g in wiz.findChildren(QGroupBox)}
    assert "Session settings" in titles
    assert "This buffer" in titles
    radios = {r.text(): r for r in wiz.findChildren(QRadioButton)}
    assert "Auto" in radios and "Fixed" in radios
    assert radios["Auto"].isChecked()
    assert strips[0]._table.rowCount() == 1  # noqa: SLF001


def test_set_buffer_preserves_pairing_rules(qapp, tmp_path: Path) -> None:
    session = LiveviewSession(watchdir=tmp_path, load_settings=False)
    rules = [("*{stem}_sample*", "*{stem}_buffer*"), ("*solo*", "")]
    session.set_buffer_rules(rules, subtract_options={"q_min": 0.1})
    buf = tmp_path / "pin.dat"
    buf.write_text("0.1 1\n0.2 1\n", encoding="utf-8")
    session.set_buffer(buf, {"q_min": 0.2, "q_max": 0.5})
    assert session.state.buffer_rules == rules
    assert session.state.buffer_dat_path == buf.resolve()
    assert session.state.subtract_options == {"q_min": 0.2, "q_max": 0.5}
