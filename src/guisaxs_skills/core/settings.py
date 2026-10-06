from __future__ import annotations

from PyQt5.QtCore import QSettings


ORG = "autosaxs"
APP = "guisaxs-skills"


def settings() -> QSettings:
    return QSettings(ORG, APP)


KEY_MAIN_GEOM = "main/geometry"
KEY_MAIN_STATE = "main/state"
KEY_SPLITTER = "main/splitter"

# Appearance (shared by guisaxs-skills, guisaxs-liveview, modeling mini-apps).
KEY_FONT_POINT_SIZE = "appearance/font_point_size"
DEFAULT_FONT_POINT_SIZE = 11
FONT_POINT_SIZE_MIN = 9
FONT_POINT_SIZE_MAX = 18
FONT_PRESET_SMALL = 10
FONT_PRESET_MEDIUM = DEFAULT_FONT_POINT_SIZE
FONT_PRESET_LARGE = 14

