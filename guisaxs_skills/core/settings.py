from __future__ import annotations

from PyQt5.QtCore import QSettings


ORG = "autosaxs"
APP = "guisaxs-skills"
APP_LIVEVIEW = "guisaxs-liveview"


def settings() -> QSettings:
    return QSettings(ORG, APP)


def liveview_settings() -> QSettings:
    return QSettings(ORG, APP_LIVEVIEW)


KEY_MAIN_GEOM = "main/geometry"
KEY_MAIN_STATE = "main/state"
KEY_SPLITTER = "main/splitter"
KEY_LIVEVIEW_LAST_WATCHDIR = "watchdir/last"

