from __future__ import annotations

from PyQt5.QtCore import QSettings


ORG = "autosaxs"
APP = "guisaxs-skills"


def settings() -> QSettings:
    return QSettings(ORG, APP)


KEY_MAIN_GEOM = "main/geometry"
KEY_MAIN_STATE = "main/state"
KEY_SPLITTER = "main/splitter"

