# Set threading environment variables BEFORE importing NumPy/SciPy/pyFAI
# to prevent threading conflicts in calibration worker thread
# Force set (not setdefault) to ensure they're always set correctly
# Save original values to restore on exit
import os
import atexit

# Threading environment variable names
_THREADING_ENV_VARS = [
    'OMP_NUM_THREADS',
    'MKL_NUM_THREADS',
    'NUMEXPR_NUM_THREADS',
    'OPENBLAS_NUM_THREADS',
    'VECLIB_MAXIMUM_THREADS'
]

# Save original values
_ORIGINAL_THREADING_ENV = {}
for var in _THREADING_ENV_VARS:
    _ORIGINAL_THREADING_ENV[var] = os.environ.get(var)

# Set to 1 thread to prevent deadlocks in worker threads
os.environ['OMP_NUM_THREADS'] = '1'
os.environ['MKL_NUM_THREADS'] = '1'
os.environ['NUMEXPR_NUM_THREADS'] = '1'
os.environ['OPENBLAS_NUM_THREADS'] = '1'
os.environ['VECLIB_MAXIMUM_THREADS'] = '1'

def _restore_threading_env():
    """Restore original threading environment variables."""
    for var, original_value in _ORIGINAL_THREADING_ENV.items():
        if original_value is None:
            # Variable wasn't set originally, remove it
            os.environ.pop(var, None)
        else:
            # Restore original value
            os.environ[var] = original_value

# Register cleanup function to run on exit
atexit.register(_restore_threading_env)

import customtkinter as ctk
import tkinter as tk
from tkinterdnd2 import DND_FILES, TkinterDnD
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import numpy as np
import yaml
import shutil
import traceback
import threading
import hashlib

# Import processing functions
from processor import autocalib, integrate_2d_to_1d, subtract_buffer, IntegratorExtended, calc_beam_abnormal_mask
from utils import read_from_tiff, read_saxs, write_saxs, ROOT_DIR

# Import pyFAI for image reading
from pyFAI.io import image

# Determine temp directory
TEMP_DIR = os.path.join(ROOT_DIR, "fast2dprocess_gui_temp")
CONFIG_PATH = os.path.join(TEMP_DIR, "config.yml")

# Constants
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

def _center_window(win):
    """Center a Tk/CTk window on the primary screen."""
    win.update_idletasks()
    width = win.winfo_width()
    height = win.winfo_height()
    screen_width = win.winfo_screenwidth()
    screen_height = win.winfo_screenheight()
    x = int((screen_width - width) / 2)
    y = int((screen_height - height) / 2)
    win.geometry(f"+{x}+{y}")

class SAXSProcessorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("SAXS Data Processor")
        self.root.geometry("1400x900")
        
        # Ensure temp directory exists
        os.makedirs(TEMP_DIR, exist_ok=True)
        
        # Initialize variables
        self.calibrant_path = None
        self.buffer_path = None
        self.sample_path = None
        self.calibrated_params = {}
        self.integrator = None
        self.buffer_1d_path = None
        self.sample_1d_path = None
        self.subtracted_1d_path = None
        self.calibration_thread = None
        self.calibration_running = False
        self.calibration_stage = None
        
        # Track calibration inputs for fast-forward mode
        self.last_calibration_hash = None
        self.calibration_cache_path = os.path.join(TEMP_DIR, "calibration_cache.yml")
        
        # Plot type for 1D curves
        self.plot_type = "I vs q"  # Default plot type
        
        # Default calibration parameters with typical SAXS values
        # These will be used as initial guesses if not loaded from saved config
        self.config_dictionary = {
            "wavelength": 1.445e-10,  # m (1.445 Å - typical for AgBh calibration)
            "detector_distance": 0.7,  # m (700 mm - typical for SAXS)
            "pixel_size": [1.72e-4, 1.72e-4],  # m (0.172 mm - typical for Pilatus)
            "beam_center_x": 1024,  # pixels (center of typical detector)
            "beam_center_y": 1024,  # pixels (center of typical detector)
            "detector_tilt": 0.0,  # radians
            "tilt_plane_rotation": 0.0,  # radians
            "calibrant_name": "AgBh",
            "r_beam_px": 35,
            "detector_name": "Pilatus1M",
        }
        
        # Advanced parameters with defaults
        self.advanced_params = {
            "center_refinement": {
                "q_start": 0.95,
                "q_stop": 0.995,
                "min_segment_len": 50,
            },
            "ring_search": {
                "q_stop": 0.995,
                "ring_I_threshold": 80.0,
                "r_max_px": 1000,
                "r_step_px": 3,
            },
            "mask_config": {
                "mode": "auto",
                "window_size": 7,
                "iqr_tol": 1.5,
            },
        }
        
        # Load configuration if it exists
        self.load_config()
        
        # Create GUI elements
        self.create_widgets()
        
        # Center window
        _center_window(self.root)
    
    def load_config(self):
        """Load configuration from YAML file if it exists."""
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, 'r') as f:
                    loaded_config = yaml.safe_load(f) or {}
                
                # Update basic parameters (only if value is not None)
                if 'config_dictionary' in loaded_config:
                    for key, value in loaded_config['config_dictionary'].items():
                        if key in self.config_dictionary and value is not None:
                            self.config_dictionary[key] = value
                
                # Update advanced parameters
                if 'advanced_params' in loaded_config:
                    for key, value in loaded_config['advanced_params'].items():
                        if key in self.advanced_params:
                            if isinstance(value, dict):
                                self.advanced_params[key].update(value)
                            else:
                                self.advanced_params[key] = value
                
                # Load calibration state if available
                if 'calibrated_params' in loaded_config and loaded_config['calibrated_params']:
                    self.calibrated_params = loaded_config['calibrated_params']
                    # Try to recreate integrator from saved calibration
                    self.try_load_integrator()
                    
            except Exception as e:
                print(f"Error loading config: {e}")
    
    def try_load_integrator(self):
        """Try to recreate integrator from saved calibration parameters."""
        if not self.calibrated_params or not self.calibrant_path:
            return
        
        try:
            calib_data = read_from_tiff(self.calibrant_path)
            pixel_size = self.config_dictionary.get('pixel_size', [1.72e-4, 1.72e-4])[0]
            if pixel_size and pixel_size > 1e-10:
                center_y_px = self.calibrated_params.get('poni1', 0) / pixel_size
                center_x_px = self.calibrated_params.get('poni2', 0) / pixel_size
                self.integrator = self.create_integrator_from_refined(
                    self.calibrated_params,
                    calib_data,
                    center_y_px,
                    center_x_px
                )
        except Exception as e:
            print(f"Could not load integrator: {e}")
    
    def save_config(self):
        """Save current configuration to YAML file."""
        try:
            config_to_save = {
                'config_dictionary': self.config_dictionary.copy(),
                'advanced_params': self.advanced_params.copy(),
                'calibrated_params': self.calibrated_params.copy() if self.calibrated_params else {},
            }
            with open(CONFIG_PATH, 'w') as f:
                yaml.dump(config_to_save, f, default_flow_style=False)
        except Exception as e:
            print(f"Error saving config: {e}")
    
    def create_widgets(self):
        # Configure grid layout
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        
        # Main container
        main_frame = ctk.CTkFrame(self.root)
        main_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_columnconfigure(1, weight=3)
        main_frame.grid_rowconfigure(0, weight=1)
        
        # Left panel for controls
        left_panel = ctk.CTkFrame(main_frame)
        left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left_panel.grid_columnconfigure(0, weight=1)
        
        # File upload section
        file_frame = ctk.CTkFrame(left_panel)
        file_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=10)
        file_frame.grid_columnconfigure(0, weight=1)
        
        # Calibrant upload
        self.calibrant_frame = self.create_drag_drop_area(file_frame, "Calibrant Image", 0)
        
        # Buffer upload
        self.buffer_frame = self.create_drag_drop_area(file_frame, "Buffer Image", 1)
        
        # Sample upload
        self.sample_frame = self.create_drag_drop_area(file_frame, "Sample Image", 2)
        
        # Calibration parameters section
        params_frame = ctk.CTkFrame(left_panel)
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
            command=self.apply_calibration,
            font=ctk.CTkFont(size=14, weight="bold")
        )
        apply_button.grid(row=row, column=0, columnspan=2, pady=10)
        
        # Right panel for visualization
        right_panel = ctk.CTkFrame(main_frame)
        right_panel.grid(row=0, column=1, sticky="nsew")
        right_panel.grid_columnconfigure(0, weight=1)
        right_panel.grid_rowconfigure(0, weight=1)
        
        # Create visualization area
        self.create_visualization_area(right_panel)
    
    def create_drag_drop_area(self, parent, title, row):
        # Frame for drag and drop
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
        
        # Store references (using setattr to avoid type checker warnings)
        setattr(drop_area, 'title', title)
        setattr(drop_area, 'status_label', status_label)
        
        # Configure drag and drop
        drop_area.drop_target_register(DND_FILES)  # type: ignore
        drop_area.dnd_bind("<<Drop>>", lambda e, f=frame, t=title: self.on_drop(e, f, t))  # type: ignore
        
        return frame
    
    def create_visualization_area(self, parent):
        # Create notebook for tabs
        self.notebook = ctk.CTkTabview(parent)
        self.notebook.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        
        # 2D images tab
        self.tab_2d = self.notebook.add("2D Images")
        self.tab_2d.grid_columnconfigure(0, weight=1)
        self.tab_2d.grid_rowconfigure(0, weight=1)
        
        # Create figure for 2D images
        self.fig_2d = Figure(figsize=(10, 6))
        self.ax_2d = self.fig_2d.add_subplot(111)
        self.ax_2d.set_title("2D SAXS Images")
        self.ax_2d.set_xlabel("X (pixels)")
        self.ax_2d.set_ylabel("Y (pixels)")
        
        self.canvas_2d = FigureCanvasTkAgg(self.fig_2d, master=self.tab_2d)
        self.canvas_2d.get_tk_widget().grid(row=0, column=0, sticky="nsew")
        
        # 1D curves tab
        self.tab_1d = self.notebook.add("1D Curves")
        self.tab_1d.grid_columnconfigure(0, weight=1)
        self.tab_1d.grid_rowconfigure(1, weight=1)
        
        # Plot type selection frame
        plot_type_frame = ctk.CTkFrame(self.tab_1d)
        plot_type_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        plot_type_frame.grid_columnconfigure(0, weight=1)
        
        ctk.CTkLabel(
            plot_type_frame,
            text="Plot Type:",
            font=ctk.CTkFont(size=12, weight="bold")
        ).grid(row=0, column=0, sticky="w", padx=10, pady=5)
        
        # Create plot type segmented button
        plot_types = ["I vs q", "log I vs q", "log I vs log q", "Guinier: log I vs q^2", "Kratky: q^2 * I vs q"]
        self.plot_type_segbutton = ctk.CTkSegmentedButton(
            plot_type_frame,
            values=plot_types,
            command=self.on_plot_type_change
        )
        self.plot_type_segbutton.set("I vs q")
        self.plot_type_segbutton.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        
        # Create figure for 1D curves
        self.fig_1d = Figure(figsize=(10, 6))
        self.ax_1d = self.fig_1d.add_subplot(111)
        self.ax_1d.set_title("1D SAXS Curves")
        self.ax_1d.set_xlabel("q (nm⁻¹)")
        self.ax_1d.set_ylabel("Intensity (a.u.)")
        self.ax_1d.grid(True)
        
        self.canvas_1d = FigureCanvasTkAgg(self.fig_1d, master=self.tab_1d)
        self.canvas_1d.get_tk_widget().grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
        
        self.status_var = tk.StringVar(value="Ready")
        status_bar = ctk.CTkLabel(
            parent, 
            textvariable=self.status_var, 
            height=40,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=STATUS_COLORS["default"],
            corner_radius=5,
            anchor="w",
            padx=15,
            pady=5
        )
        status_bar.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self.status_bar = status_bar
    
    def _update_drop_labels(self, frame, file_path):
        """Update labels in drop frame with file name."""
        filename = os.path.basename(str(file_path))
        for child in frame.winfo_children():
            if isinstance(child, ctk.CTkFrame):
                for subchild in child.winfo_children():
                    if isinstance(subchild, ctk.CTkLabel) and "Drag & Drop" in subchild.cget("text"):
                        subchild.configure(text=f"File: {filename}")
                        break
            elif isinstance(child, ctk.CTkLabel) and child.cget("text") == "No file selected":
                child.configure(text=f"File: {filename}")
                break
    
    def on_drop(self, event, frame, title):
        files = self.root.tk.splitlist(event.data)
        if not files or not files[0]:
            return
        
        file_path = files[0]
        self._update_drop_labels(frame, file_path)
        
        file_type_map = {
            "Calibrant Image": ("calibrant_path", "Calibrant", True),
            "Buffer Image": ("buffer_path", "Buffer", False),
            "Sample Image": ("sample_path", "Sample", False),
        }
        
        if title in file_type_map:
            attr_name, display_name, always_display = file_type_map[title]
            setattr(self, attr_name, file_path)
            if always_display or self.is_calibration_available():
                self.display_2d_image(file_path, display_name)
                
                # Auto-process buffer or sample if calibration is available
                if self.is_calibration_available():
                    if title == "Buffer Image":
                        # Process buffer immediately in a thread
                        threading.Thread(target=self._process_image_worker, args=(file_path, "buffer", "Buffer"), daemon=True).start()
                    elif title == "Sample Image":
                        # Process sample immediately in a thread
                        threading.Thread(target=self._process_image_worker, args=(file_path, "sample", "Sample"), daemon=True).start()
            else:
                self._update_status("Please calibrate first")
    
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
    
    def _update_status(self, message, color="default"):
        """Update status bar with message and optional color."""
        self.status_var.set(message)
        if hasattr(self, 'status_bar'):
            self.status_bar.configure(fg_color=STATUS_COLORS.get(color, STATUS_COLORS["default"]))
    
    def _copy_file_to_temp(self, source_path, dest_name):
        """Copy file to temp directory."""
        if source_path:
            try:
                dest = os.path.join(TEMP_DIR, dest_name)
                shutil.copy2(str(source_path), dest)
            except Exception as e:
                print(f"Error copying {dest_name}: {e}")
    
    def _save_plot(self, fig, filename):
        """Save figure to temp directory."""
        try:
            plot_path = os.path.join(TEMP_DIR, filename)
            fig.savefig(plot_path, dpi=150, bbox_inches='tight')
        except Exception as e:
            print(f"Error saving plot: {e}")
    
    def update_config_from_gui(self):
        """Update config_dictionary from GUI values."""
        for param, var in self.param_vars.items():
            try:
                display_value = var.get()
                if display_value is None:
                    continue
                    
                if param == "pixel_size":
                    converted = display_value * CONVERSIONS_TO_INTERNAL.get(param, 1e-3)
                    self.config_dictionary[param] = [converted, converted]
                else:
                    conversion = CONVERSIONS_TO_INTERNAL.get(param, 1)
                    self.config_dictionary[param] = display_value * conversion
            except Exception as e:
                print(f"Error updating {param} from GUI: {e}")
    
    def is_calibration_available(self):
        """Check if calibration is available (integrator exists)."""
        return self.integrator is not None and self.calibrated_params
    
    def compute_calibration_hash(self, calib_path, config):
        """Compute hash of calibration inputs for fast-forward mode."""
        try:
            # Read file content hash
            with open(calib_path, 'rb') as f:
                file_hash = hashlib.md5(f.read()).hexdigest()
            
            # Create config hash
            config_str = yaml.dump(config, default_flow_style=False)
            config_hash = hashlib.md5(config_str.encode()).hexdigest()
            
            # Combine hashes
            combined = f"{file_hash}_{config_hash}"
            return hashlib.md5(combined.encode()).hexdigest()
        except Exception as e:
            print(f"Error computing calibration hash: {e}")
            return None
    
    def build_autocalib_config(self):
        """Build the config dictionary required by autocalib."""
        # CRITICAL: Update config_dictionary from GUI values first
        # This ensures that any changes made in the GUI are reflected in the config
        self.update_config_from_gui()
        
        # Validate that required parameters are set
        required = {
            'detector_distance': self.config_dictionary.get('detector_distance'),
            'wavelength': self.config_dictionary.get('wavelength'),
            'pixel_size': self.config_dictionary.get('pixel_size'),
            'beam_center_x': self.config_dictionary.get('beam_center_x'),
            'beam_center_y': self.config_dictionary.get('beam_center_y'),
        }
        
        missing = [k for k, v in required.items() if v is None]
        if missing:
            raise ValueError(f"Required parameters not set: {', '.join(missing)}")
        
        # Get center from config (will be refined during calibration)
        center_x = required['beam_center_x']
        center_y = required['beam_center_y']
        
        # Ensure pixel_size is a list
        pixel_size = required['pixel_size']
        if not isinstance(pixel_size, list):
            pixel_size = [pixel_size, pixel_size]
        
        config = {
            'detector_geometry': {
                'dist': required['detector_distance'],
                'wavelength': required['wavelength'],
                'pixel_size': pixel_size,
                'rot1': self.config_dictionary.get('detector_tilt', 0.0),
                'rot2': self.config_dictionary.get('tilt_plane_rotation', 0.0),
                'rot3': 0.0,
            },
            'center_refinement': self.advanced_params['center_refinement'],
            'ring_search': self.advanced_params['ring_search'],
            'r_beam_px': self.config_dictionary.get('r_beam_px', 35),
            'calibrant_name': self.config_dictionary.get('calibrant_name', 'AgBh'),
            'mask_config': self.advanced_params['mask_config'],
        }
        
        return config
    
    def create_integrator_from_refined(self, refined_params, calib_data, center_y_px, center_x_px):
        """Create an IntegratorExtended object from refined parameters."""
        # Create mask if needed
        mask = None
        mask_config = self.advanced_params['mask_config']
        if mask_config['mode'] in ['auto', 'combined']:
            automask_ops = {k: v for k, v in mask_config.items() if k != 'mode'}
            mask = calc_beam_abnormal_mask(
                calib_data, 
                center_y_px, 
                center_x_px, 
                self.config_dictionary['r_beam_px'],
                **automask_ops
            )
        
        # Create integrator
        integrator = IntegratorExtended(
            ai_params={'wavelength': refined_params['wavelength'], **refined_params},
            detector_params={
                'detector_name': self.config_dictionary['detector_name'],
                'pixel_size': self.config_dictionary['pixel_size']
            },
            mask=mask
        )
        
        return integrator
    
    def _load_cached_calibration(self, current_hash):
        """Load cached calibration if hash matches."""
        if not os.path.exists(self.calibration_cache_path):
            return False
        
        try:
            with open(self.calibration_cache_path, 'r') as f:
                cache = yaml.safe_load(f)
            if cache and cache.get('hash') == current_hash and cache.get('calibrated_params'):
                self._update_status("Using cached calibration (inputs unchanged)")
                self.calibrated_params = cache['calibrated_params']
                calib_data = read_from_tiff(self.calibrant_path)
                pixel_size = self.config_dictionary['pixel_size'][0]
                if pixel_size and pixel_size > 1e-10:
                    center_y_px = self.calibrated_params['poni1'] / pixel_size
                    center_x_px = self.calibrated_params['poni2'] / pixel_size
                    self.integrator = self.create_integrator_from_refined(
                        self.calibrated_params, calib_data, center_y_px, center_x_px
                    )
                self.update_gui_after_calibration()
                self.save_config()
                
                # Auto-process any existing buffer or sample images
                if self.buffer_path:
                    threading.Thread(target=self._process_image_worker, args=(self.buffer_path, "buffer", "Buffer"), daemon=True).start()
                if self.sample_path:
                    threading.Thread(target=self._process_image_worker, args=(self.sample_path, "sample", "Sample"), daemon=True).start()
                
                return True
        except Exception as e:
            print(f"Error loading cached calibration: {e}")
        return False
    
    def process_calibrant(self):
        if not self.calibrant_path: 
            self._update_status("No calibrant image loaded")
            return
        
        if self.calibration_running:
            self._update_status("Calibration already in progress...")
            return
        
        config = self.build_autocalib_config()
        current_hash = self.compute_calibration_hash(self.calibrant_path, config)
        
        if current_hash and current_hash == self.last_calibration_hash:
            if self._load_cached_calibration(current_hash):
                return
        
        self.calibration_running = True
        self._update_status(f"Calibrating: {os.path.basename(str(self.calibrant_path))}...", "progress")
        self.status_update_counter = 0
        self.start_status_updates()
        
        def calibration_worker():
            try:
                try:
                    import threadpoolctl
                    with threadpoolctl.threadpool_limits(limits=1):
                        if not self.calibrant_path:
                            raise ValueError("Calibrant path is None")
                        self._run_calibration(config, current_hash)
                except ImportError:
                    if not self.calibrant_path:
                        raise ValueError("Calibrant path is None")
                    self._run_calibration(config, current_hash)
            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}"
                traceback.print_exc()
                self.root.after(0, self.stop_status_updates)
                self.root.after(0, self.calibration_error, error_msg)
        
        self.calibration_thread = threading.Thread(target=calibration_worker, daemon=False)
        self.calibration_thread.start()
    
    def start_status_updates(self):
        """Start periodic status updates during calibration."""
        if not hasattr(self, 'status_update_counter'):
            self.status_update_counter = 0
        
        def update_status_periodic():
            if self.calibration_running:
                self.status_update_counter += 1
                # Add dots to show activity
                dots = "." * ((self.status_update_counter // 2) % 4)
                current_status = self.status_var.get()
                if "Calibrating:" in current_status:
                    # Keep the base message and add animated dots
                    if "..." in current_status:
                        base_status = current_status.replace("...", "")
                    else:
                        base_status = current_status
                    self.status_var.set(f"{base_status}{dots}")
                # Schedule next update
                self.root.after(500, update_status_periodic)
            else:
                # Stop updates when calibration is done
                self.status_update_counter = 0
        
        update_status_periodic()
    
    def stop_status_updates(self):
        """Stop periodic status updates."""
        if hasattr(self, 'status_update_counter'):
            self.status_update_counter = 0
    
    def calibration_complete(self, calibrated_params, integrator, calibration_hash):
        """Called when calibration completes (in main thread)."""
        try:
            self.calibrated_params = calibrated_params
            self.integrator = integrator
            self.last_calibration_hash = calibration_hash
            
            try:
                cache = {'hash': calibration_hash, 'calibrated_params': calibrated_params}
                with open(self.calibration_cache_path, 'w') as f:
                    yaml.dump(cache, f, default_flow_style=False)
            except Exception as e:
                print(f"Error saving calibration cache: {e}")
            
            self.update_gui_after_calibration()
            self.save_config()
            self._copy_file_to_temp(self.calibrant_path, "calibrant.tif")
            
            if self.calibrant_path:
                self.display_2d_image(self.calibrant_path, "Calibrant")
                self._save_plot(self.fig_2d, "calibrant_2d.png")
            
            success_text = f"✓ Calibration complete: {os.path.basename(str(self.calibrant_path))}"
            self._update_status(success_text, "success")
            self.calibration_running = False
            self.root.update_idletasks()
            self.root.after(3000, self.reset_status_bar_color)
            
            # Auto-process any existing buffer or sample images
            if self.buffer_path:
                threading.Thread(target=self._process_image_worker, args=(self.buffer_path, "buffer", "Buffer"), daemon=True).start()
            if self.sample_path:
                threading.Thread(target=self._process_image_worker, args=(self.sample_path, "sample", "Sample"), daemon=True).start()
        except Exception as e:
            error_msg = f"Error in calibration_complete: {str(e)}"
            self.calibration_error(error_msg)
            traceback.print_exc()
    
    def _run_calibration(self, config, current_hash):
        """Run the actual calibration work (called from worker thread with thread limits)."""
        def update_status(msg):
            self.calibration_stage = msg
            self.root.after(0, lambda: self._update_status(f"Calibrating: {msg}...", "progress"))
        
        update_status("Loading image")
        
        try:
            update_status("Finding center (this may take a while)")
            import time
            time.sleep(0.1)
            calibrated_params = autocalib(str(self.calibrant_path), config, mask_path=None)
        except Exception as e:
            error_msg = f"Autocalib failed: {str(e)}"
            traceback.print_exc()
            self.root.after(0, self.stop_status_updates)
            self.root.after(0, self.calibration_error, error_msg)
            return
        
        update_status("Creating integrator")
        
        try:
            calib_data = read_from_tiff(self.calibrant_path)
        except Exception as e:
            error_msg = f"Failed to load calibration image: {str(e)}"
            self.root.after(0, self.stop_status_updates)
            self.root.after(0, self.calibration_error, error_msg)
            return
        
        pixel_size = self.config_dictionary.get('pixel_size', [None])[0]
        if not pixel_size or pixel_size < 1e-10:
            error_msg = f"Invalid pixel size: {pixel_size}"
            self.root.after(0, self.stop_status_updates)
            self.root.after(0, self.calibration_error, error_msg)
            return
        
        center_y_px = calibrated_params.get('poni1', 0) / pixel_size
        center_x_px = calibrated_params.get('poni2', 0) / pixel_size
        
        try:
            integrator = self.create_integrator_from_refined(
                calibrated_params, calib_data, center_y_px, center_x_px
            )
        except Exception as e:
            error_msg = f"Failed to create integrator: {str(e)}"
            traceback.print_exc()
            self.root.after(0, self.stop_status_updates)
            self.root.after(0, self.calibration_error, error_msg)
            return
        
        self.root.after(0, self.stop_status_updates)
        self.root.after(0, self.calibration_complete, calibrated_params, integrator, current_hash)
    
    def calibration_error(self, error_msg):
        """Called when calibration fails (in main thread)."""
        self._update_status(f"ERROR: {error_msg}", "error")
        self.calibration_running = False
        traceback.print_exc()
        self.root.after(5000, self.reset_status_bar_color)
    
    def reset_status_bar_color(self):
        """Reset status bar to default color."""
        if hasattr(self, 'status_bar'):
            self.status_bar.configure(fg_color=STATUS_COLORS["default"])
    
    def _update_gui_param(self, calib_key, gui_key, conversion=1):
        """Update a GUI parameter from calibrated params."""
        if calib_key in self.calibrated_params and gui_key in self.param_vars:
            value = self.calibrated_params[calib_key] * conversion
            self.param_vars[gui_key].set(value)
            self.config_dictionary[gui_key] = self.calibrated_params[calib_key]
    
    def update_gui_after_calibration(self):
        """Update GUI with calibrated parameters."""
        pixel_size = self.config_dictionary.get('pixel_size', [None])[0]
        if pixel_size and pixel_size > 1e-10:
            center_y_px = self.calibrated_params.get('poni1', 0) / pixel_size
            center_x_px = self.calibrated_params.get('poni2', 0) / pixel_size
            if 'beam_center_y' in self.param_vars:
                self.param_vars['beam_center_y'].set(center_y_px)
            if 'beam_center_x' in self.param_vars:
                self.param_vars['beam_center_x'].set(center_x_px)
        
        self._update_gui_param('dist', 'detector_distance', CONVERSIONS_TO_DISPLAY['detector_distance'])
        self._update_gui_param('wavelength', 'wavelength', CONVERSIONS_TO_DISPLAY['wavelength'])
        self._update_gui_param('rot1', 'detector_tilt', CONVERSIONS_TO_DISPLAY['detector_tilt'])
        self._update_gui_param('rot2', 'tilt_plane_rotation', CONVERSIONS_TO_DISPLAY['tilt_plane_rotation'])
        self.root.update_idletasks()
    
    def _process_image_worker(self, image_path, image_type, title):
        """Worker function for processing images in a thread."""
        if not image_path:
            self.root.after(0, lambda: self._update_status(f"No {image_type} image loaded"))
            return
        
        if not self.is_calibration_available():
            self.root.after(0, lambda: self._update_status("Please calibrate first"))
            return
        
        self.root.after(0, lambda: self._update_status(f"Processing {image_type}: {os.path.basename(str(image_path))}", "progress"))
        
        try:
            # Update GUI on main thread
            self.root.after(0, lambda: self.display_2d_image(image_path, title))
            self.root.after(0, lambda: self._save_plot(self.fig_2d, f"{image_type}_2d.png"))
            
            data = read_from_tiff(image_path)
            output_path = os.path.join(TEMP_DIR, f"{image_type}_1d.dat")
            metadata = {'type': image_type, 'source_path': image_path}
            
            integrate_2d_to_1d(self.integrator, data, npt=1000, destpath=output_path, metadata=metadata)
            
            self._copy_file_to_temp(image_path, f"{image_type}.tif")
            
            # Handle subtraction for sample if buffer exists
            if image_type == "sample" and self.buffer_1d_path and os.path.exists(self.buffer_1d_path):
                self.subtracted_1d_path = os.path.join(TEMP_DIR, "subtracted_1d.dat")
                subtract_buffer(self.buffer_1d_path, output_path, self.subtracted_1d_path, method='match_tail')
            
            setattr(self, f"{image_type}_1d_path", output_path)
            
            # Update GUI on main thread
            self.root.after(0, lambda: self.display_1d_curves())
            self.root.after(0, lambda: self._save_plot(self.fig_1d, f"{image_type}_1d.png"))
            self.root.after(0, lambda: self._update_status(f"{title} processed: {os.path.basename(str(image_path))}", "success"))
        except Exception as e:
            self.root.after(0, lambda: self._update_status(f"Error processing {image_type}: {str(e)}", "error"))
            traceback.print_exc()
    
    def _process_image(self, image_path, image_type, title):
        """Common processing logic for buffer and sample images."""
        if not image_path:
            self._update_status(f"No {image_type} image loaded")
            return
        
        if not self.is_calibration_available():
            self._update_status("Please calibrate first")
            return
        
        self._update_status(f"Processing {image_type}: {os.path.basename(str(image_path))}", "progress")
        self.root.update()
        
        try:
            self.display_2d_image(image_path, title)
            self._save_plot(self.fig_2d, f"{image_type}_2d.png")
            
            data = read_from_tiff(image_path)
            output_path = os.path.join(TEMP_DIR, f"{image_type}_1d.dat")
            metadata = {'type': image_type, 'source_path': image_path}
            
            integrate_2d_to_1d(self.integrator, data, npt=1000, destpath=output_path, metadata=metadata)
            
            self._copy_file_to_temp(image_path, f"{image_type}.tif")
            
            # Handle subtraction for sample if buffer exists
            if image_type == "sample" and self.buffer_1d_path and os.path.exists(self.buffer_1d_path):
                self.subtracted_1d_path = os.path.join(TEMP_DIR, "subtracted_1d.dat")
                subtract_buffer(self.buffer_1d_path, output_path, self.subtracted_1d_path, method='match_tail')
            
            self.display_1d_curves()
            self._save_plot(self.fig_1d, f"{image_type}_1d.png")
            
            setattr(self, f"{image_type}_1d_path", output_path)
            self._update_status(f"{title} processed: {os.path.basename(str(image_path))}", "success")
        except Exception as e:
            self._update_status(f"Error processing {image_type}: {str(e)}", "error")
            traceback.print_exc()
    
    def process_buffer(self):
        self._process_image(self.buffer_path, "buffer", "Buffer")
    
    def process_sample(self):
        self._process_image(self.sample_path, "sample", "Sample")
    
    def display_2d_image(self, image_path, title):
        # Clear previous plot
        self.ax_2d.clear()
        
        # Load and display image
        try:
            img_data = read_from_tiff(image_path)
            
            # Display image with log scale
            im = self.ax_2d.imshow(
                np.log1p(img_data), 
                cmap='viridis', 
                origin='lower'
            )
            # self.fig_2d.colorbar(im, ax=self.ax_2d, label='Log(Intensity + 1)')
            
            self.ax_2d.set_title(f"2D Image: {title}")
            self.ax_2d.set_xlabel("X (pixels)")
            self.ax_2d.set_ylabel("Y (pixels)")
            
            self.canvas_2d.draw()
        except Exception as e:
            print(f"Error displaying image: {str(e)}")
            traceback.print_exc()
    
    def on_plot_type_change(self, value):
        """Callback when plot type is changed."""
        self.plot_type = value
        self.display_1d_curves()
    
    def _plot_1d_curve(self, file_path, label, color, plot_type):
        """Helper to load and plot a 1D curve with specified plot type."""
        if file_path and os.path.exists(file_path):
            try:
                q, I, sigma, _ = read_saxs(file_path)
                q_nm = q * 1e-9  # Convert q from 1/m to 1/nm
                
                # Filter out zero/negative values for log plots
                valid_mask = (I > 0) & (q_nm > 0)
                q_plot = q_nm[valid_mask]
                I_plot = I[valid_mask]
                
                if plot_type == "I vs q":
                    x_data = q_plot
                    y_data = I_plot
                elif plot_type == "log I vs q":
                    x_data = q_plot
                    y_data = np.log10(I_plot)
                elif plot_type == "log I vs log q":
                    x_data = np.log10(q_plot)
                    y_data = np.log10(I_plot)
                elif plot_type == "Guinier: log I vs q^2":
                    x_data = q_plot ** 2
                    y_data = np.log10(I_plot)
                elif plot_type == "Kratky: q^2 * I vs q":
                    x_data = q_plot
                    y_data = (q_plot ** 2) * I_plot
                else:
                    # Default to I vs q
                    x_data = q_plot
                    y_data = I_plot
                
                self.ax_1d.plot(x_data, y_data, label=label, color=color, linewidth=2)
            except Exception as e:
                print(f"Error loading {label} curve: {str(e)}")
    
    def display_1d_curves(self):
        """Display 1D curves with the selected plot type."""
        self.ax_1d.clear()
        
        # Get current plot type from segmented button
        if hasattr(self, 'plot_type_segbutton'):
            self.plot_type = self.plot_type_segbutton.get()
        
        # Plot curves with current plot type
        self._plot_1d_curve(self.buffer_1d_path, 'Buffer', 'blue', self.plot_type)
        self._plot_1d_curve(self.sample_1d_path, 'Sample', 'red', self.plot_type)
        self._plot_1d_curve(self.subtracted_1d_path, 'Subtracted', 'green', self.plot_type)
        
        # Set labels based on plot type
        if self.plot_type == "I vs q":
            self.ax_1d.set_xlabel("q (nm⁻¹)")
            self.ax_1d.set_ylabel("Intensity (a.u.)")
            self.ax_1d.set_yscale('linear')
        elif self.plot_type == "log I vs q":
            self.ax_1d.set_xlabel("q (nm⁻¹)")
            self.ax_1d.set_ylabel("log₁₀(I)")
            self.ax_1d.set_yscale('linear')
        elif self.plot_type == "log I vs log q":
            self.ax_1d.set_xlabel("log₁₀(q / nm⁻¹)")
            self.ax_1d.set_ylabel("log₁₀(I)")
            self.ax_1d.set_yscale('linear')
        elif self.plot_type == "Guinier: log I vs q^2":
            self.ax_1d.set_xlabel("q² (nm⁻²)")
            self.ax_1d.set_ylabel("log₁₀(I)")
            self.ax_1d.set_yscale('linear')
        elif self.plot_type == "Kratky: q^2 * I vs q":
            self.ax_1d.set_xlabel("q (nm⁻¹)")
            self.ax_1d.set_ylabel("q² × I (a.u.)")
            self.ax_1d.set_yscale('linear')
        
        self.ax_1d.set_title(f"1D SAXS Curves - {self.plot_type}")
        self.ax_1d.grid(True)
        self.ax_1d.legend()
        self.canvas_1d.draw()
    
    def update_param_value(self, param, value):
        # Update the entry field when slider moves
        self.param_vars[param].set(float(value))
    
    def _validate_required_params(self):
        """Validate that all required parameters are set."""
        required_params = ['wavelength', 'detector_distance', 'pixel_size', 'beam_center_x', 'beam_center_y']
        missing = []
        for param in required_params:
            value = self.config_dictionary.get(param)
            if value is None:
                missing.append(param)
            elif param == 'pixel_size' and (not isinstance(value, list) or len(value) < 2 or value[0] is None):
                missing.append(param)
        return missing
    
    def apply_calibration(self):
        if not self.calibrant_path:
            self._update_status("No calibrant image loaded")
            return
        
        self.update_config_from_gui()
        missing = self._validate_required_params()
        if missing:
            self._update_status(f"Please set required parameters: {', '.join(missing)}")
            return
        
        self.save_config()
        self.process_calibrant()

# Main function
def main():
    # Create root window with DND support
    root = TkinterDnD.Tk()
    
    # Set theme to match gui.py style
    ctk.set_appearance_mode("System")
    ctk.set_default_color_theme("blue")
    
    # Create GUI
    app = SAXSProcessorGUI(root)
    
    # Restore environment variables when window is closed
    def on_closing():
        _restore_threading_env()
        root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    
    # Run the application
    root.mainloop()

if __name__ == "__main__":
    main()
