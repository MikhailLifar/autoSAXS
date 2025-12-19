"""Control panel widget for file uploads and calibration parameters."""
import os
import customtkinter as ctk
import tkinter as tk
from tkinterdnd2 import DND_FILES
from typing import Dict, Callable, Optional
from ..core.constants import CONVERSIONS_TO_INTERNAL


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
        
        # Create panel frame
        self.panel = ctk.CTkFrame(parent)
        self.panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.panel.grid_columnconfigure(0, weight=1)
        
        # File upload section
        file_frame = ctk.CTkFrame(self.panel)
        file_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        file_frame.grid_columnconfigure(0, weight=1)
        
        # Create drag and drop areas
        self.calibrant_frame = self.create_drag_drop_area(file_frame, "Calibrant Image", 0)
        self.mask_frame = self.create_drag_drop_area(file_frame, "Mask File (Optional)", 1)
        self.buffer_frame = self.create_drag_drop_area(file_frame, "Buffer Image", 2)
        self.sample_frame = self.create_drag_drop_area(file_frame, "Sample Image", 3)
        
        # Calibration parameters section
        params_frame = ctk.CTkFrame(self.panel)
        params_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=10)
        params_frame.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(
            params_frame, 
            text="Calibration Parameters", 
            font=ctk.CTkFont(size=16, weight="bold")
        ).grid(row=0, column=0, columnspan=2, pady=10)
        
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
            
            ctk.CTkLabel(params_frame, text=display_name).grid(row=row, column=0, sticky="w", padx=10, pady=5)
            
            self.param_vars[config_key] = tk.DoubleVar(value=default_display)
            ctk.CTkEntry(params_frame, width=120, textvariable=self.param_vars[config_key]).grid(
                row=row, column=1, padx=10, pady=5
            )
            
            slider_min, slider_max = slider_range
            slider = ctk.CTkSlider(
                params_frame,
                from_=slider_min,
                to=slider_max,
                variable=self.param_vars[config_key],
                command=lambda v, p=config_key: self.update_param_value(p, v)
            )
            slider.grid(row=row+1, column=0, columnspan=2, sticky="ew", padx=10, pady=(0, 10))
            self.param_sliders[config_key] = slider
            row += 2
        
        # Apply calibration button
        apply_button = ctk.CTkButton(
            params_frame,
            text="Apply Calibration",
            command=self.callbacks.get('on_apply_calibration'),
            font=ctk.CTkFont(size=14, weight="bold")
        )
        apply_button.grid(row=row, column=0, columnspan=2, pady=10)
    
    def create_drag_drop_area(self, parent, title, row):
        """Create a drag and drop area for file upload."""
        frame = ctk.CTkFrame(parent)
        frame.grid(row=row, column=0, sticky="ew", padx=10, pady=10)
        frame.grid_columnconfigure(0, weight=1)
        
        # Title label
        label = ctk.CTkLabel(
            frame, 
            text=title, 
            font=ctk.CTkFont(size=14, weight="bold")
        )
        label.grid(row=0, column=0, pady=(10, 0))
        
        # Drag and drop area
        drop_area = ctk.CTkFrame(frame, height=100)
        drop_area.grid(row=1, column=0, sticky="ew", padx=10, pady=10)
        drop_area.grid_columnconfigure(0, weight=1)
        drop_area.grid_rowconfigure(0, weight=1)
        
        # Drop label
        drop_label = ctk.CTkLabel(
            drop_area, 
            text=f"Drag & Drop {title} Here", 
            fg_color="transparent"
        )
        drop_label.grid(row=0, column=0, pady=20)
        
        # Status label
        status_label = ctk.CTkLabel(frame, text="No file selected")
        status_label.grid(row=2, column=0, pady=(0, 10))
        
        # Store references for easy access when updating
        setattr(drop_area, 'title', title)
        setattr(drop_area, 'drop_label', drop_label)
        setattr(frame, 'status_label', status_label)
        
        # Configure drag and drop
        drop_area.drop_target_register(DND_FILES)  # type: ignore
        drop_area.dnd_bind("<<Drop>>", lambda e, f=frame, t=title: self.on_drop(e, f, t))  # type: ignore
        
        return frame
    
    def _update_drop_labels(self, frame, file_path):
        """Update labels in drop frame with file name."""
        filename = os.path.basename(str(file_path))
        
        # Update status label using stored reference
        if hasattr(frame, 'status_label'):
            frame.status_label.configure(text=f"File: {filename}")
        
        # Find and update drop_label (inside drop_area frame)
        for child in frame.winfo_children():
            if isinstance(child, ctk.CTkFrame):
                # Check if this is the drop_area and has stored reference
                if hasattr(child, 'drop_label'):
                    drop_label = getattr(child, 'drop_label')
                    drop_label.configure(text=f"File: {filename}")
                else:
                    # Fallback: search for the label
                    for subchild in child.winfo_children():
                        if isinstance(subchild, ctk.CTkLabel):
                            current_text = subchild.cget("text")
                            # Update if it's the drop label (contains "Drag & Drop" or starts with "File:")
                            if "Drag & Drop" in current_text or current_text.startswith("File:"):
                                subchild.configure(text=f"File: {filename}")
                                break
    
    def on_drop(self, event, frame, title):
        """Handle file drop event."""
        files = self.root.tk.splitlist(event.data)
        if not files or not files[0]:
            return
        
        file_path = files[0]
        self._update_drop_labels(frame, file_path)
        
        # Call the callback to handle file drop
        if self.callbacks.get('on_file_drop'):
            self.callbacks['on_file_drop'](file_path, title)
    
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

