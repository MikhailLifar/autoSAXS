"""Service for handling calibration operations."""
import os
import sys
import json
import subprocess
import threading
from typing import Optional, Callable
from ..models.calibration_manager import CalibrationManager
from ..models.data_manager import DataManager
from ..core.event_bus import EventBus, EventType
import shutil
import yaml


class CalibrationService:
    """Service for handling calibration operations with subprocess execution."""
    
    def __init__(self, calibration_manager: CalibrationManager,
                 data_manager: DataManager,
                 working_dir: str,
                 event_bus: Optional[EventBus] = None):
        """
        Initialize the calibration service.
        
        Args:
            calibration_manager: Calibration manager instance
            data_manager: Data manager instance
            working_dir: Working directory for calibration files (required)
            event_bus: Optional event bus for publishing events
        """
        self.calibration_manager = calibration_manager
        self.data_manager = data_manager
        self.event_bus = event_bus
        self.temp_dir = working_dir  # working directory (legacy name kept for internal paths)
        
        self.calibration_running = False
        self.calibration_process: Optional[subprocess.Popen] = None
        self.status_monitor_running = False
        self.calibration_thread: Optional[threading.Thread] = None
    
    def run_calibration(self, status_callback: Optional[Callable[[str, str], None]] = None) -> bool:
        """
        Run calibration asynchronously with progress updates.
        
        Args:
            status_callback: Optional callback(status_message, status_type) for status updates
            
        Returns:
            True if calibration was started, False otherwise
        """
        calibrant_path = self.data_manager.calibrant_path
        if not calibrant_path:
            if status_callback:
                status_callback("No calibrant image loaded", "error")
            if self.event_bus:
                self.event_bus.publish(EventType.CALIBRATION_ERROR, {"error": "No calibrant image loaded"})
            return False
        
        if self.calibration_running:
            if status_callback:
                status_callback("Calibration already in progress...", "error")
            return False
        
        # Build calibration config
        try:
            config = self.calibration_manager.build_calibration_config(self.data_manager.mask_path)
        except ValueError as e:
            if status_callback:
                status_callback(str(e), "error")
            if self.event_bus:
                self.event_bus.publish(EventType.CALIBRATION_ERROR, {"error": str(e)})
            return False
        
        # Remove stale status file from previous run so GUI does not show old "Calibration complete"
        status_file = os.path.join(self.temp_dir, 'calibration_status.json')
        if os.path.exists(status_file):
            try:
                os.remove(status_file)
            except OSError:
                pass
        
        # Start calibration (no cache; every run uses subprocess)
        self.calibration_running = True
        status_msg = f"Calibrating: {os.path.basename(str(calibrant_path))}..."
        if status_callback:
            status_callback(status_msg, "progress")
        if self.event_bus:
            self.event_bus.publish(EventType.CALIBRATION_STARTED, {"calibrant_path": calibrant_path})
        
        # Start calibration in separate thread
        def calibration_worker():
            """Worker thread that launches calibration subprocess and monitors it."""
            try:
                self._run_calibration_service(config, status_callback)
            except Exception as e:
                error_msg = f"Unexpected error: {str(e)}"
                import traceback
                traceback.print_exc()
                self.stop_status_monitoring()
                self._handle_calibration_error(error_msg, status_callback)
        
        self.calibration_thread = threading.Thread(target=calibration_worker, daemon=False)
        self.calibration_thread.start()
        
        # Start status monitoring
        self.start_status_monitoring(status_callback)
        
        return True
    
    def _run_calibration_service(self, config: dict,
                                  status_callback: Optional[Callable[[str, str], None]]):
        """Run calibration using separate subprocess service."""
        # Prepare configuration file for service
        service_config_file = os.path.join(self.temp_dir, 'calibration_config.json')
        status_file = os.path.join(self.temp_dir, 'calibration_status.json')
        output_dir = os.path.join(self.temp_dir, 'calibration_output')
        calib_config_yaml = os.path.join(self.temp_dir, "calibration_config.yml")
        
        os.makedirs(output_dir, exist_ok=True)
        
        # Write calibration config YAML for autosaxs skill (Option A).
        # `autosaxs.skill.calibrate` expects a config_path, not an in-memory dict.
        try:
            with open(calib_config_yaml, "w") as f:
                yaml.safe_dump(config, f, default_flow_style=False)
        except Exception as e:
            raise RuntimeError(f"Failed to write calibration YAML config: {e}")

        # Prepare config data for subprocess (paths only)
        config_data = {
            'calibrant_path': str(self.data_manager.calibrant_path),
            'mask_path': str(self.data_manager.mask_path) if self.data_manager.mask_path else None,
            'config_path': calib_config_yaml,
        }
        
        # Write config file
        try:
            with open(service_config_file, 'w') as f:
                json.dump(config_data, f, indent=2)
        except Exception as e:
            raise RuntimeError(f"Failed to write calibration config: {e}")
        
        # Get path to calibration service script
        script_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        service_script = os.path.join(script_dir, 'calibration_service.py')
        
        if not os.path.exists(service_script):
            raise RuntimeError(f"Calibration service script not found: {service_script}")
        
        # Launch calibration service as subprocess
        try:
            self.calibration_process = subprocess.Popen(
                [sys.executable, service_script, service_config_file, output_dir, '--status-file', status_file],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Wait for process to complete
            stdout, stderr = self.calibration_process.communicate()
            
            if self.calibration_process.returncode != 0:
                error_msg = f"Calibration service failed: {stderr if stderr else 'Unknown error'}"
                self.stop_status_monitoring()
                self._handle_calibration_error(error_msg, status_callback)
                return
            
            # Load results
            result_file = os.path.join(output_dir, 'calibration_result.json')
            if not os.path.exists(result_file):
                error_msg = "Calibration completed but result file not found"
                self.stop_status_monitoring()
                self._handle_calibration_error(error_msg, status_callback)
                return
            
            with open(result_file, 'r') as f:
                result_data = json.load(f)
            
            if result_data.get('status') != 'success':
                error_msg = result_data.get('error', 'Calibration failed')
                self.stop_status_monitoring()
                self._handle_calibration_error(error_msg, status_callback)
                return

            integrator_dir = result_data.get("integrator_dir")
            refined_path = result_data.get("refined_path")
            calibrated_params = result_data.get("calibrated_params", {})

            if not integrator_dir or not os.path.isdir(str(integrator_dir)):
                error_msg = "Calibrated integrator directory not found"
                self.stop_status_monitoring()
                self._handle_calibration_error(error_msg, status_callback)
                return

            # Copy integrator to stable location in working dir for downstream integration.
            main_integrator_dir = os.path.join(self.temp_dir, "integrator")
            if os.path.exists(main_integrator_dir):
                shutil.rmtree(main_integrator_dir)
            shutil.copytree(str(integrator_dir), main_integrator_dir)
            
            # Complete calibration
            self.stop_status_monitoring()
            self._handle_calibration_complete(calibrated_params, main_integrator_dir, status_callback)
            
        except Exception as e:
            error_msg = f"Error running calibration service: {str(e)}"
            import traceback
            traceback.print_exc()
            self.stop_status_monitoring()
            self._handle_calibration_error(error_msg, status_callback)
    
    def start_status_monitoring(self, status_callback: Optional[Callable[[str, str], None]] = None):
        """Start monitoring calibration service status file."""
        self.status_monitor_running = True
        # Note: The actual monitoring is handled by the GUI layer using root.after()
        # This method just sets the flag
        
    def stop_status_monitoring(self):
        """Stop monitoring calibration service status."""
        self.status_monitor_running = False
    
    def _handle_calibration_complete(self, calibrated_params: dict, integrator_dir: str,
                                     status_callback: Optional[Callable[[str, str], None]]):
        """Handle calibration completion."""
        self.calibration_manager.set_calibration_result(integrator_dir, calibrated_params)
        self.calibration_running = False
        
        success_text = f"✓ Calibration complete: {os.path.basename(str(self.data_manager.calibrant_path))}"
        if status_callback:
            status_callback(success_text, "success")
        
        if self.event_bus:
            self.event_bus.publish(EventType.CALIBRATION_COMPLETE, {
                "calibrated_params": calibrated_params,
                "calibrant_path": self.data_manager.calibrant_path,
            })
    
    def _handle_calibration_error(self, error_msg: str, status_callback: Optional[Callable[[str, str], None]]):
        """Handle calibration error."""
        self.calibration_running = False
        self.status_monitor_running = False
        
        if status_callback:
            status_callback(f"ERROR: {error_msg}", "error")
        
        if self.event_bus:
            self.event_bus.publish(EventType.CALIBRATION_ERROR, {"error": error_msg})

