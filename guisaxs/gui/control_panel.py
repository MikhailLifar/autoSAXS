"""Control panel widget for file uploads and calibration parameters."""
import os
import customtkinter as ctk
import tkinter as tk
from tkinterdnd2 import DND_FILES
from typing import Dict, Callable, Optional, Union, List
from ..core.constants import CONVERSIONS_TO_INTERNAL
from ..core.style import FONTS, COLORS


class ControlPanel:
    """Left panel containing file uploads and calibration parameters."""
    
    def __init__(self, parent, root, callbacks: Dict[str, Callable], config_dictionary: Dict):
        """
        Initialize the control panel.
        
        Args:
            parent: Parent frame to contain the panel
            root: Root window for drag-and-drop
            callbacks: Dict with callbacks: on_file_drop, on_apply_calibration
            config_dictionary: Reference to config dictionary for default values
        """
        self.root = root
        self.config_dictionary = config_dictionary
        self.callbacks = callbacks
        
        # Create panel frame: width responds to window (narrower, flexible)
        self.parent = parent
        self.panel = ctk.CTkFrame(parent, width=280)
        self.panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.panel.grid_columnconfigure(0, weight=1)
        self.panel.grid_rowconfigure(0, weight=1)   # file section expands with height
        self.panel.grid_rowconfigure(1, weight=0)  # params section natural height
        self._panel_min_width = 240
        self._panel_max_width = 320
        self._panel_width_ratio = 0.20  # fraction of window width
        self._update_panel_width()
        self.root.bind("<Configure>", lambda e: self._on_configure(e))

        # File upload section (expands with window height)
        file_frame = ctk.CTkFrame(self.panel)
        file_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=6)
        file_frame.grid_columnconfigure(0, weight=1)
        for r in range(4):
            file_frame.grid_rowconfigure(r, weight=1, minsize=52)  # drop zones have min height
        
        # Create drag and drop areas
        self.calibrant_frame = self.create_drag_drop_area(file_frame, "Calibrant Image", 0)
        self.mask_frame = self.create_drag_drop_area(file_frame, "Mask File (Optional)", 1)
        self.buffer_frame = self.create_drag_drop_area(file_frame, "Buffer Image", 2)
        self.sample_frame = self.create_drag_drop_area(file_frame, "Sample Image(s)", 3)
        
        # Calibration parameters section (fixed height, at bottom)
        params_frame = ctk.CTkFrame(self.panel)
        params_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=6)
        params_frame.grid_columnconfigure(0, weight=1)
        
        params_title = ctk.CTkLabel(
            params_frame, 
            text="Calibration Parameters", 
            font=ctk.CTkFont(**FONTS["heading"])
        )
        params_title.grid(row=0, column=0, columnspan=2, pady=6)
        
        self.param_vars = {}
        self.param_sliders = {}
        
        param_mapping = {
            "Wavelength (Å)": ("wavelength", 1e-10, (0.1, 3.0)),
            "Detector Distance (mm)": ("detector_distance", 1e-3, (100.0, 1000.0)),
            "Pixel Size (mm)": ("pixel_size", 1e-3, (0.05, 0.5)),
            "Beam Center X (px)": ("beam_center_x", 1, (0, 2048)),
            "Beam Center Y (px)": ("beam_center_y", 1, (0, 2048)),
            "Detector Tilt (rad)": ("detector_tilt", 1, (-0.1, 0.1)),
            "Tilt Plane Rotation (rad)": ("tilt_plane_rotation", 1, (-0.1, 0.1)),
        }
        
        row = 1
        for display_name, (config_key, conversion, slider_range) in param_mapping.items():
            default_display = self._get_default_display_value(config_key, conversion, slider_range)
            
            label = ctk.CTkLabel(params_frame, text=display_name)
            label.grid(row=row, column=0, sticky="w", padx=8, pady=2)
            
            self.param_vars[config_key] = tk.DoubleVar(value=default_display)
            entry = ctk.CTkEntry(params_frame, width=90, textvariable=self.param_vars[config_key])
            entry.grid(row=row, column=1, padx=8, pady=2)
            
            slider_min, slider_max = slider_range
            slider = ctk.CTkSlider(
                params_frame,
                from_=slider_min,
                to=slider_max,
                variable=self.param_vars[config_key],
                command=lambda v, p=config_key: self.update_param_value(p, v)
            )
            slider.grid(row=row+1, column=0, columnspan=2, sticky="ew", padx=8, pady=(0, 6))
            self.param_sliders[config_key] = slider
            row += 2
        
        # Apply calibration button
        apply_button = ctk.CTkButton(
            params_frame,
            text="Apply Calibration",
            command=self.callbacks.get('on_apply_calibration'),
            font=ctk.CTkFont(**FONTS["heading"])
        )
        apply_button.grid(row=row, column=0, columnspan=2, pady=6)
    
    def _on_configure(self, event):
        """Update panel width when root window is resized (only on root)."""
        if event.widget is self.root:
            self._update_panel_width()

    def _update_panel_width(self):
        """Set panel width from window width (narrower, clamped to min/max)."""
        try:
            w = self.root.winfo_width()
            if w <= 1:
                return
            width = int(w * self._panel_width_ratio)
            width = max(self._panel_min_width, min(self._panel_max_width, width))
            if self.panel.winfo_width() != width:
                self.panel.configure(width=width)
        except (tk.TclError, Exception):
            pass

    def _dnd_text_no_file(self, title: str) -> str:
        """Text shown in DnD area when no file is uploaded."""
        return f"{title} (Drag and Drop)"

    def _dnd_text_with_file(self, title: str, file_path: Union[str, List[str]]) -> str:
        """Text shown in DnD area when file(s) uploaded. For multiple samples: 'N files' or 'File: <first> + N more'."""
        if isinstance(file_path, list):
            if not file_path:
                return self._dnd_text_no_file(title)
            if len(file_path) == 1:
                return f"{title}: {os.path.basename(str(file_path[0]))}"
            first = os.path.basename(str(file_path[0]))
            return f"{title}: {first} + {len(file_path) - 1} more"
        filename = os.path.basename(str(file_path))
        return f"{title}: {filename}"

    def create_drag_drop_area(self, parent, title, row):
        """Create a single drop zone with all text inside the field."""
        drop_area = ctk.CTkFrame(parent)
        drop_area.grid(row=row, column=0, sticky="nsew", padx=8, pady=4)
        drop_area.grid_columnconfigure(0, weight=1)
        drop_area.grid_rowconfigure(0, weight=1)
        
        # Single label inside the DnD field: "<Image type> (Drag and Drop)" or "<Image type>: <file name>"
        label = ctk.CTkLabel(
            drop_area,
            text=self._dnd_text_no_file(title),
            fg_color=COLORS["drop_zone"],
            font=ctk.CTkFont(**FONTS["drop_zone"]),
            wraplength=240,
        )
        label.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        
        drop_area.title = title
        drop_area.dnd_label = label
        drop_area.drop_target_register(DND_FILES)  # type: ignore
        drop_area.dnd_bind("<<Drop>>", lambda e, d=drop_area, t=title: self.on_drop(e, d, t))  # type: ignore
        
        return drop_area
    
    def _update_drop_labels(self, drop_area, file_path: Union[str, List[str]]):
        """Update the single label inside the DnD area: '<Image type>: <file name>' or for multiple samples 'N files' / 'File: <first> + N more'."""
        if hasattr(drop_area, 'dnd_label') and hasattr(drop_area, 'title'):
            text = self._dnd_text_with_file(drop_area.title, file_path)
            drop_area.dnd_label.configure(text=text)
    
    def _reset_drop_labels(self, drop_area, title):
        """Reset the DnD area label to '<Image type> (Drag and Drop)'."""
        if hasattr(drop_area, 'dnd_label'):
            drop_area.dnd_label.configure(text=self._dnd_text_no_file(title))
    
    def on_drop(self, event, drop_area, title):
        """Handle file drop event. Sample Image(s) accepts multiple files; other zones accept only one (error if multiple)."""
        files = self.root.tk.splitlist(event.data)
        if not files or not files[0]:
            return
        
        # Only Sample Image(s) accepts multiple files; others must receive a single file
        is_sample_zone = title == "Sample Image(s)"
        if is_sample_zone:
            file_path_or_paths = list(files)
        else:
            if len(files) > 1:
                # Spec: error if more than one file on non-sample zone
                if self.callbacks.get('on_file_drop'):
                    self.callbacks['on_file_drop'](files, title)  # pass list to trigger "only one file" error
                return
            file_path_or_paths = files[0]
        
        if self.callbacks.get('on_file_drop'):
            success = self.callbacks['on_file_drop'](file_path_or_paths, title)
            if success:
                self._update_drop_labels(drop_area, file_path_or_paths)
            else:
                self._reset_drop_labels(drop_area, title)
        else:
            self._update_drop_labels(drop_area, file_path_or_paths)
    
    def _get_default_display_value(self, config_key, conversion, slider_range):
        """Get default display value for a parameter, updating config if needed."""
        if config_key == "pixel_size":
            value = self.config_dictionary.get(config_key)
            default_display = value[0] / conversion if value and value[0] is not None else None
        else:
            value = self.config_dictionary.get(config_key)
            default_display = value / conversion if value is not None else None
        
        if default_display is None:
            slider_min, slider_max = slider_range
            default_display = (slider_min + slider_max) / 2.0
            if config_key == "pixel_size":
                self.config_dictionary[config_key] = [default_display * conversion] * 2
            else:
                self.config_dictionary[config_key] = default_display * conversion
        
        return default_display
    
    def update_param_value(self, param, value):
        """Update the entry field when slider moves."""
        self.param_vars[param].set(float(value))
    
    def update_gui_param(self, config_key, value, conversion=1):
        """Update a GUI parameter from external source (e.g., calibration)."""
        if config_key in self.param_vars:
            self.param_vars[config_key].set(value * conversion)

