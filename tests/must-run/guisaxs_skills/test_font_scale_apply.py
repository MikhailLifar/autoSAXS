"""Appearance font Apply must scale all live Qt chrome widgets."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt5.QtWidgets")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from guisaxs_skills.ui.style import (  # noqa: E402
    apply_font_point_size,
    apply_style,
    clamp_font_point_size,
    mark_font_role,
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    apply_style(app)
    return app


def test_apply_font_scales_sticky_and_role_widgets(qapp):
    win = QMainWindow()
    root = QWidget()
    lay = QVBoxLayout(root)
    plain = QLabel("plain")
    sticky = QLabel("sticky")
    sticky_font = sticky.font()
    sticky_font.setPointSize(9)
    sticky.setFont(sticky_font)
    titled = QLabel("title")
    mark_font_role(titled, delta_pt=4, bold=True)
    btn = QPushButton("Go")
    lay.addWidget(plain)
    lay.addWidget(sticky)
    lay.addWidget(titled)
    lay.addWidget(btn)
    win.setCentralWidget(root)
    win.show()
    qapp.processEvents()

    size = 16
    applied = apply_font_point_size(qapp, point_size=size, persist=False)
    assert applied == size
    qapp.processEvents()

    assert qapp.font().pointSize() == size
    assert "font-size: 16pt" in qapp.styleSheet()

    offenders = []
    for w in (plain, sticky, btn):
        if w.font().pointSize() != size:
            offenders.append((type(w).__name__, w.font().pointSize(), size))
    expect_title = clamp_font_point_size(size + 4)
    if titled.font().pointSize() != expect_title:
        offenders.append(("title", titled.font().pointSize(), expect_title))
    assert not offenders, f"widgets kept old font sizes: {offenders}"

    # Second Apply to a smaller size must also stick (not only grow).
    size2 = 10
    apply_font_point_size(qapp, point_size=size2, persist=False)
    qapp.processEvents()
    assert plain.font().pointSize() == size2
    assert sticky.font().pointSize() == size2
    assert titled.font().pointSize() == clamp_font_point_size(size2 + 4)

    win.close()
