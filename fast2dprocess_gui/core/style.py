"""Single source of truth for all visual style: color theme, fonts, colors, plot constants.

All GUI code must import from this module; no hardcoded style values elsewhere.
Font entries are kwargs for customtkinter.CTkFont (e.g. ctk.CTkFont(**FONTS["status_bar"])).
"""

# CustomTkinter default color theme ("blue", "green", "dark-blue"). Applied at startup.
COLOR_THEME = "blue"

# Status bar background colors: (light_theme, dark_theme) per status type.
STATUS_COLORS = {
    "default": ("gray85", "gray25"),
    "progress": ("lightblue", "darkblue"),
    "success": ("green", "darkgreen"),
    "error": ("red", "darkred"),
}

# Font definitions for CTk widgets. Use as ctk.CTkFont(**FONTS["key"]).
FONTS = {
    "status_bar": {"size": 14, "weight": "bold"},
    "heading": {"size": 14, "weight": "bold"},       # section/button titles
    "panel_title": {"size": 12, "weight": "bold"},   # "Images", "Curves", "Plot Type:"
    "drop_zone": {"size": 12},
    "curve_checkbox": {"size": 10},
    "thumbnail_label": {"size": 9},
}

# Widget colors: Save button, thumbnail selection, drop zone.
COLORS = {
    "save_button": ("green", "darkgreen"),
    "thumbnail_selected": ("gray75", "gray35"),
    "thumbnail_unselected": ("gray90", "gray20"),
    "drop_zone": "transparent",
}

# Plot-related (matplotlib)
PLOT_COLORMAP = "tab10"
PLOT_DEFAULT_CURVE_COLOR = "#1f77b4"
PLOT_LEGEND_FONTSIZE = 9
