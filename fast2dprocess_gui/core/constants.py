"""Constants used throughout the application."""
import os
from autosaxs.utils import ROOT_DIR

# Determine temp directory
TEMP_DIR = os.path.join(ROOT_DIR, "fast2dprocess_gui_temp")
CONFIG_PATH = os.path.join(TEMP_DIR, "config.yml")

# Unit conversion constants
CONVERSIONS_TO_INTERNAL = {
    "wavelength": 1e-10,  # Å to m
    "detector_distance": 1e-3,  # mm to m
    "pixel_size": 1e-3,  # mm to m
    "beam_center_x": 1,  # pixels
    "beam_center_y": 1,  # pixels
    "detector_tilt": 1,  # radians
    "tilt_plane_rotation": 1,  # radians
}

CONVERSIONS_TO_DISPLAY = {
    "wavelength": 1e10,  # m to Å
    "detector_distance": 1e3,  # m to mm
    "pixel_size": 1e3,  # m to mm
    "detector_tilt": 1,  # radians
    "tilt_plane_rotation": 1,  # radians
}

STATUS_COLORS = {
    "default": ("gray85", "gray25"),
    "progress": ("lightblue", "darkblue"),
    "success": ("green", "darkgreen"),
    "error": ("red", "darkred"),
}

