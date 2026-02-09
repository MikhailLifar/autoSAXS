"""Constants used throughout the application."""
# Working directory is selected by the user at launch via dialog; not a constant.

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
