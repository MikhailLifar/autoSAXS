"""Main GUI window for SAXS Data Processor."""
import os
import shutil
import threading
import traceback
import customtkinter as ctk
import tkinter as tk
from typing import Optional
from ..core.constants import TEMP_DIR, STATUS_COLORS, CONVERSIONS_TO_INTERNAL, CONVERSIONS_TO_DISPLAY
from ..core.event_bus import EventBus, EventType
from ..models import ConfigManager, DataManager, CalibrationManager, ProcessingManager
from ..models.data_manager import FileType
from ..services import CalibrationService, ProcessingService
from .control_panel import ControlPanel
from .image_tab_2d import ImageTab2D
from .curves_tab_1d import CurvesTab1D
from .widgets import center_window, enable_text_copying_recursive
from utils import read_from_tiff


class SAXSProcessorGUI:
    """Main application window - coordinates views and delegates to managers."""
    
    def __init__(self, root):
        """
        Initialize the main GUI window.
        
        Args:
            root: Tkinter root window
        """
        self.root = root
        self.root.title("SAXS Data Processor")
        self.root.geometry("1400x900")
        
        # Ensure temp directory exists
        os.makedirs(TEMP_DIR, exist_ok=True)
        
        # Initialize event bus
        self.event_bus = EventBus()
        
        # Initialize managers
        self.config_manager = ConfigManager()
        self.data_manager = DataManager()
        self.calibration_manager = CalibrationManager(self.config_manager, TEMP_DIR)
        self.processing_manager = ProcessingManager(self.calibration_manager, TEMP_DIR)
        
        # Initialize services
        self.calibration_service = CalibrationService(
            self.calibration_manager,
            self.data_manager,
            self.event_bus,
            TEMP_DIR
        )
        self.processing_service = ProcessingService(
            self.processing_manager,
            self.data_manager,
            self.event_bus
        )
        
        # Initialize GUI component references (will be set in create_widgets)
        self.control_panel: Optional[ControlPanel] = None
        self.image_tab_2d: Optional[ImageTab2D] = None
        self.curves_tab_1d: Optional[CurvesTab1D] = None
        
        # Sync config_manager basic_params to config_dictionary for ControlPanel compatibility
        # ControlPanel expects config_dictionary, so we maintain it as a view of basic_params
        self.config_dictionary = self.config_manager.basic_params
        
        # Subscribe to events
        self._subscribe_to_events()
        
        # Create GUI elements
        self.create_widgets()
        
        # Center window
        center_window(self.root)
        
        # Enable text copying globally for all widgets (after all widgets are created)
        enable_text_copying_recursive(self.root)
    
    def _subscribe_to_events(self):
        """Subscribe to events from managers/services."""
        self.event_bus.subscribe(EventType.CALIBRATION_COMPLETE, self._on_calibration_complete)
        self.event_bus.subscribe(EventType.CALIBRATION_ERROR, self._on_calibration_error)
        self.event_bus.subscribe(EventType.PROCESSING_COMPLETE, self._on_processing_complete)
    
    def _on_calibration_complete(self, data: dict):
        """Handle calibration completion event."""
        calibrated_params = data.get('calibrated_params', {})
        from_cache = data.get('from_cache', False)
        
        # Update GUI with calibrated parameters
        self.update_gui_after_calibration()
        
        # Display calibrant image
        if self.data_manager.calibrant_path and self.image_tab_2d:
            filename = os.path.basename(str(self.data_manager.calibrant_path))
            self.display_2d_image(self.data_manager.calibrant_path, f"Calibrant: {filename}")
            # Copy image to temp with descriptive naming
            self.data_manager.copy_image_to_temp(
                self.data_manager.calibrant_path,
                "calibrant",
                TEMP_DIR
            )
            # Save plot - view component handles its own filename generation
            self.image_tab_2d.save_calibrant_plot(self.data_manager.calibrant_path)
        
        # Auto-process any existing buffer or sample images
        if not from_cache:
            if self.data_manager.buffer_path:
                buffer_filename = os.path.basename(str(self.data_manager.buffer_path))
                threading.Thread(
                    target=self._process_image_worker,
                    args=(self.data_manager.buffer_path, "buffer", f"Buffer: {buffer_filename}"),
                    daemon=True
                ).start()
            if self.data_manager.sample_path:
                sample_filename = os.path.basename(str(self.data_manager.sample_path))
                threading.Thread(
                    target=self._process_image_worker,
                    args=(self.data_manager.sample_path, "sample", f"Sample: {sample_filename}"),
                    daemon=True
                ).start()
    
    def _on_calibration_error(self, data: dict):
        """Handle calibration error event."""
        error_msg = data.get('error', 'Unknown error')
        self._update_status(f"ERROR: {error_msg}", "error")
        self.root.after(5000, self.reset_status_bar_color)
    
    def _on_processing_complete(self, data: dict):
        """Handle processing completion event."""
        # Processing handled in worker thread
        pass
    
    def create_widgets(self):
        """Create and layout GUI widgets."""
        # Configure grid layout
        self.root.grid_columnconfigure(0, weight=1)
        self.root.grid_rowconfigure(0, weight=1)
        
        # Main container
        main_frame = ctk.CTkFrame(self.root)
        main_frame.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_columnconfigure(1, weight=3)
        main_frame.grid_rowconfigure(0, weight=1)
        
        # Create control panel (left side)
        callbacks = {
            'on_file_drop': self.on_file_drop,
            'on_apply_calibration': self.apply_calibration,
            'on_save': self.save_results,
        }
        self.control_panel = ControlPanel(main_frame, self.root, callbacks, self.config_dictionary)
        
        # Right panel for visualization
        right_panel = ctk.CTkFrame(main_frame)
        right_panel.grid(row=0, column=1, sticky="nsew")
        right_panel.grid_columnconfigure(0, weight=1)
        right_panel.grid_rowconfigure(0, weight=1)
        
        # Create visualization area
        self.create_visualization_area(right_panel)
    
    def create_visualization_area(self, parent):
        """Create the visualization area with tabs."""
        # Create notebook for tabs
        self.notebook = ctk.CTkTabview(parent)
        self.notebook.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        
        # 2D images tab
        tab_2d = self.notebook.add("2D Images")
        self.image_tab_2d = ImageTab2D(tab_2d)
        
        # 1D curves tab
        tab_1d = self.notebook.add("1D Curves")
        self.curves_tab_1d = CurvesTab1D(tab_1d)
        self.curves_tab_1d.update_callback = lambda: self.display_1d_curves()  # type: ignore
        
        # Status bar
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
    
    def on_file_drop(self, file_path: str, title: str) -> bool:
        """
        Handle file drop callback from ControlPanel.
        
        Returns:
            True if file was successfully processed, False if validation failed
        """
        file_type_map = {
            "Calibrant Image": (FileType.CALIBRANT, "Calibrant", True),
            "Buffer Image": (FileType.BUFFER, "Buffer", False),
            "Sample Image": (FileType.SAMPLE, "Sample", False),
            "Mask File (Optional)": (FileType.MASK, "Mask", False),
        }
        
        if title in file_type_map:
            file_type, image_type, always_display = file_type_map[title]
            
            # Validate mask file before storing
            if title == "Mask File (Optional)":
                if not self.data_manager.validate_mask_file(file_path):
                    self._update_status(f"Invalid mask file: {os.path.basename(str(file_path))}. Only .npy, .txt, .msk files with values 0/1 or True/False are allowed.", "error")
                    self.root.after(5000, self.reset_status_bar_color)
                    return False  # Don't store invalid mask file
            
            # Validate image files (calibrant, buffer, sample) - must be .tif
            elif title in ["Calibrant Image", "Buffer Image", "Sample Image"]:
                if not self.data_manager.validate_image_file(file_path):
                    self._update_status(f"Invalid {title.lower()}: {os.path.basename(str(file_path))}. Only .tif files are allowed.", "error")
                    self.root.after(5000, self.reset_status_bar_color)
                    return False  # Don't store invalid image file
            
            # Store the file path
            self.data_manager.set_file(file_type, file_path)
            
            # Add thumbnail for all image types (including mask)
            if self.image_tab_2d:
                self.image_tab_2d.add_image_thumbnail(file_path, image_type)
            
            # Mask file doesn't need to be displayed as an image in main view initially
            if title == "Mask File (Optional)":
                self._update_status(f"Mask file loaded: {os.path.basename(str(file_path))}")
            elif always_display or self.calibration_manager.is_calibrated:
                filename = os.path.basename(str(file_path))
                display_title = f"{image_type}: {filename}"
                self.display_2d_image(file_path, display_title)
                
                # Auto-process buffer or sample if calibration is available
                if self.calibration_manager.is_calibrated:
                    if title == "Buffer Image":
                        threading.Thread(
                            target=self._process_image_worker,
                            args=(file_path, "buffer", f"Buffer: {filename}"),
                            daemon=True
                        ).start()
                    elif title == "Sample Image":
                        threading.Thread(
                            target=self._process_image_worker,
                            args=(file_path, "sample", f"Sample: {filename}"),
                            daemon=True
                        ).start()
            else:
                self._update_status("Please calibrate first")
            
            return True  # File successfully processed
        
        return False  # Unknown file type
    
    def _update_status(self, message: str, color: str = "default"):
        """Update status bar with message and optional color."""
        self.status_var.set(message)
        if hasattr(self, 'status_bar'):
            self.status_bar.configure(fg_color=STATUS_COLORS.get(color, STATUS_COLORS["default"]))
    
    def reset_status_bar_color(self):
        """Reset status bar to default color."""
        if hasattr(self, 'status_bar'):
            self.status_bar.configure(fg_color=STATUS_COLORS["default"])
    
    def update_config_from_gui(self):
        """Update config_manager from GUI values."""
        if not self.control_panel:
            return
        for param, var in self.control_panel.param_vars.items():
            try:
                display_value = var.get()
                if display_value is None:
                    continue
                    
                if param == "pixel_size":
                    converted = display_value * CONVERSIONS_TO_INTERNAL.get(param, 1e-3)
                    self.config_manager.set_param(param, [converted, converted])
                else:
                    conversion = CONVERSIONS_TO_INTERNAL.get(param, 1)
                    self.config_manager.set_param(param, display_value * conversion)
            except Exception as e:
                print(f"Error updating {param} from GUI: {e}")
    
    def apply_calibration(self):
        """Apply calibration button callback."""
        if not self.data_manager.calibrant_path:
            self._update_status("No calibrant image loaded")
            return
        
        self.update_config_from_gui()
        missing = self._validate_required_params()
        if missing:
            self._update_status(f"Please set required parameters: {', '.join(missing)}")
            return
        
        self.config_manager.save()
        self.calibration_service.run_calibration(status_callback=self._update_status)
        # Status monitoring is started by the service, but we also need GUI-level monitoring
        self._start_status_monitoring()
    
    def _validate_required_params(self):
        """Validate that all required parameters are set."""
        required_params = ['wavelength', 'detector_distance', 'pixel_size', 'beam_center_x', 'beam_center_y']
        missing = []
        for param in required_params:
            value = self.config_manager.get_param(param)
            if value is None:
                missing.append(param)
            elif param == 'pixel_size' and (not isinstance(value, list) or len(value) < 2 or value[0] is None):
                missing.append(param)
        return missing
    
    def _start_status_monitoring(self):
        """Start monitoring calibration service status file."""
        status_file = os.path.join(TEMP_DIR, 'calibration_status.json')
        
        def check_status():
            if not self.calibration_service.status_monitor_running:
                return
            
            try:
                if os.path.exists(status_file):
                    import json
                    with open(status_file, 'r') as f:
                        status_data = json.load(f)
                    
                    message = status_data.get('message', 'Calibrating...')
                    status_type = status_data.get('type', 'progress')
                    
                    if status_type == 'success':
                        self._update_status(message, "success")
                        self.calibration_service.stop_status_monitoring()
                    elif status_type == 'error':
                        self._update_status(f"ERROR: {message}", "error")
                        self.calibration_service.stop_status_monitoring()
                    else:
                        self._update_status(f"Calibrating: {message}...", "progress")
            except Exception:
                # Ignore errors reading status file
                pass
            
            if self.calibration_service.status_monitor_running:
                # Check again in 500ms
                self.root.after(500, check_status)
        
        check_status()
    
    def update_gui_after_calibration(self):
        """Update GUI with calibrated parameters."""
        if not self.control_panel:
            return
        
        calibrated_params = self.calibration_manager.get_calibrated_params()
        pixel_size = self.config_manager.get_param('pixel_size', [None])[0]
        
        if pixel_size and pixel_size > 1e-10:
            center_y_px = calibrated_params.get('poni1', 0) / pixel_size
            center_x_px = calibrated_params.get('poni2', 0) / pixel_size
            if 'beam_center_y' in self.control_panel.param_vars:
                self.control_panel.param_vars['beam_center_y'].set(center_y_px)
            if 'beam_center_x' in self.control_panel.param_vars:
                self.control_panel.param_vars['beam_center_x'].set(center_x_px)
        
        self._update_gui_param('dist', 'detector_distance', CONVERSIONS_TO_DISPLAY['detector_distance'])
        self._update_gui_param('wavelength', 'wavelength', CONVERSIONS_TO_DISPLAY['wavelength'])
        self._update_gui_param('rot1', 'detector_tilt', CONVERSIONS_TO_DISPLAY['detector_tilt'])
        self._update_gui_param('rot2', 'tilt_plane_rotation', CONVERSIONS_TO_DISPLAY['tilt_plane_rotation'])
        self.root.update_idletasks()
    
    def _update_gui_param(self, calib_key: str, gui_key: str, conversion: float = 1):
        """Update a GUI parameter from calibrated params."""
        if not self.control_panel:
            return
        calibrated_params = self.calibration_manager.get_calibrated_params()
        if calib_key in calibrated_params and gui_key in self.control_panel.param_vars:
            value = calibrated_params[calib_key] * conversion
            self.control_panel.param_vars[gui_key].set(value)
            self.config_manager.set_param(gui_key, calibrated_params[calib_key])
    
    def _process_image_worker(self, image_path: str, image_type: str, title: str):
        """Worker function for processing images in a thread."""
        # Update GUI on main thread
        self.root.after_idle(lambda: self.display_2d_image(image_path, title))
        
        # Process image using service
        output_path = self.processing_service.process_image(
            image_path,
            image_type,
            status_callback=lambda msg, typ: self.root.after_idle(lambda: self._update_status(msg, typ))
        )
        
        if output_path:
            # Copy source image to temp with descriptive naming (delegated to DataManager)
            self.data_manager.copy_image_to_temp(image_path, image_type, TEMP_DIR)
            
            # Register curve in GUI and save plots (delegated to view component)
            if self.curves_tab_1d:
                self.root.after_idle(lambda: self.curves_tab_1d.add_curve(output_path, image_type))
                
                # Save all plots for this curve (view component handles filename generation)
                def save_all_curve_plots():
                    if self.curves_tab_1d:
                        self.curves_tab_1d.save_all_curve_plots(output_path)
                
                # Handle subtraction for sample
                if image_type == "sample":
                    # Store output_path in closure before scheduling
                    sample_output_path = output_path
                    
                    def create_subtracted_curves():
                        if not self.curves_tab_1d:
                            return
                        
                        # Find the most recently added buffer curve
                        # Skip any curves that match the current sample output_path to avoid confusion
                        last_buffer_path = None
                        sample_unique_id = os.path.abspath(str(sample_output_path))
                        
                        # Iterate in reverse order (most recent first) to find the most recent buffer
                        for unique_id, (buffer_path, buf_type, _, _, filename) in reversed(list(self.curves_tab_1d.curves.items())):
                            # Skip the current sample curve if it's already in the list
                            if unique_id == sample_unique_id:
                                continue
                            # Find the most recent buffer curve
                            if buf_type == "buffer" and os.path.exists(buffer_path):
                                last_buffer_path = buffer_path
                                break
                        
                        # Perform subtraction if we found a buffer curve
                        # Important: subtract_buffer expects (buffer_path, sample_path, output_path)
                        # It calculates: sample - buffer
                        if last_buffer_path:
                            subtracted_path = self.processing_service.create_subtracted_curve(
                                last_buffer_path,      # buffer curve (first argument) - will be subtracted FROM sample
                                sample_output_path     # sample curve (second argument) - this is what we're subtracting FROM
                            )
                            if subtracted_path and self.curves_tab_1d:
                                self.curves_tab_1d.add_curve(subtracted_path, "subtracted")
                                # Save plots for subtracted curve (view component handles this)
                                self.curves_tab_1d.save_all_curve_plots(subtracted_path)
                    
                    # Schedule subtraction after a short delay to ensure sample curve is added to the list first
                    self.root.after(100, create_subtracted_curves)
                
                # Schedule plot saving
                self.root.after(200, save_all_curve_plots)
            
            # Update display and save 2D image plot (delegated to view component)
            self.root.after_idle(lambda: self.display_1d_curves())
            if self.image_tab_2d:
                self.root.after_idle(lambda: self.image_tab_2d.save_image_plot(image_path, image_type))
    
    def display_2d_image(self, image_path: str, title: str):
        """Display a 2D image in the 2D tab."""
        if self.image_tab_2d:
            # Extract image type from title if it's in format "Type: filename"
            if ":" in title:
                image_type = title.split(":")[0].strip()
            else:
                image_type = title.split()[0] if title else "Image"
            
            # Ensure thumbnail exists
            if image_path and os.path.exists(image_path):
                self.image_tab_2d.add_image_thumbnail(image_path, image_type)
            self.image_tab_2d.display_image(image_path, title)
    
    def display_1d_curves(self):
        """Display 1D curves in the 1D tab."""
        if self.curves_tab_1d:
            self.curves_tab_1d.update_display()
    
    def save_results(self):
        """Save all files from temporary directory to user-selected location."""
        import tkinter.filedialog as filedialog
        
        # Let user select an existing directory (must be empty)
        dest_dir = filedialog.askdirectory(
            title="Select empty directory to save results (directory must exist and be empty)",
            mustexist=True
        )
        
        if not dest_dir:
            return  # User cancelled
        
        # Check if directory is empty
        if os.path.exists(dest_dir):
            try:
                # Check if directory is empty
                if os.listdir(dest_dir):
                    self._update_status(f"Error: Directory '{dest_dir}' is not empty. Please select an empty directory.", "error")
                    self.root.after(5000, self.reset_status_bar_color)
                    return
            except OSError as e:
                self._update_status(f"Error: Cannot access directory '{dest_dir}': {str(e)}", "error")
                self.root.after(5000, self.reset_status_bar_color)
                return
        
        # Delegate file operations to DataManager
        try:
            files_copied = self.data_manager.save_temp_files(TEMP_DIR, dest_dir)
            if files_copied is not None:
                if files_copied > 0:
                    self._update_status(f"Successfully saved {files_copied} items to {dest_dir}", "success")
                else:
                    self._update_status("No temporary files found to save", "error")
            else:
                self._update_status("Error: Failed to save files", "error")
            
            self.root.after(5000, self.reset_status_bar_color)
        except Exception as e:
            error_msg = f"Error saving files: {str(e)}"
            self._update_status(error_msg, "error")
            self.root.after(5000, self.reset_status_bar_color)
            import traceback
            traceback.print_exc()

