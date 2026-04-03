"""Calibration management for the application."""
import os
from typing import Optional, Dict, Any
from ..core.interfaces import ICalibrationManager
from .config_manager import ConfigManager


class CalibrationManager(ICalibrationManager):
    """Manages calibration state and operations."""
    
    def __init__(self, config_manager: ConfigManager, working_dir: str):
        """
        Initialize the calibration manager.
        
        Args:
            config_manager: Configuration manager instance
            working_dir: Working directory for calibration files
        """
        self.config_manager = config_manager
        self.temp_dir = working_dir
        self.integrator_dir: Optional[str] = None
        self.calibrated_params: Dict[str, Any] = {}
        
        # Try to load integrator from disk if it exists
        self._try_load_integrator()
    
    @property
    def is_calibrated(self) -> bool:
        """Check if calibration is available."""
        return self.integrator_dir is not None and os.path.isdir(self.integrator_dir) and bool(self.calibrated_params)
    
    def get_integrator_dir(self) -> Optional[str]:
        """Get path to calibrated integrator directory if available."""
        return self.integrator_dir
    
    def get_calibrated_params(self) -> Dict[str, Any]:
        """Get calibrated parameters."""
        return self.calibrated_params.copy()
    
    def _try_load_integrator(self):
        """Try to load integrator directory and calibrated params from disk if they exist."""
        integrator_dir = os.path.join(self.temp_dir, "integrator")
        if os.path.isdir(integrator_dir):
            try:
                self.integrator_dir = integrator_dir
                # Try to load calibrated params from config.yml saved by GUI.
                config = self.config_manager.load()
                if 'calibrated_params' in config:
                    self.calibrated_params = config.get('calibrated_params', {})
            except Exception as e:
                print(f"Could not load integrator from disk: {e}")
    
    def build_calibration_config(self, mask_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Build the config dictionary required by the autosaxs `calibrate` skill.
        
        Args:
            mask_path: Optional path to mask file
            
        Returns:
            Configuration dictionary for calibration
        """
        # Validate that required parameters are set
        required = {
            'detector_distance': self.config_manager.get_param('detector_distance'),
            'wavelength': self.config_manager.get_param('wavelength'),
            'pixel_size': self.config_manager.get_param('pixel_size'),
            'beam_center_x': self.config_manager.get_param('beam_center_x'),
            'beam_center_y': self.config_manager.get_param('beam_center_y'),
        }
        
        missing = [k for k, v in required.items() if v is None]
        if missing:
            raise ValueError(f"Required parameters not set: {', '.join(missing)}")
        
        # Ensure pixel_size is a list
        pixel_size = required['pixel_size']
        if not isinstance(pixel_size, list):
            pixel_size = [pixel_size, pixel_size]
        
        # Set mask_config based on whether mask file is provided
        mask_config = self.config_manager.advanced_params['mask_config'].copy()
        if mask_path:
            # If mask file is provided, use "combined" mode with calc_abnormal_mask=False
            mask_config['mode'] = 'combined'
            mask_config['calc_abnormal_mask'] = False
        else:
            # If no mask file, use "auto" mode
            mask_config['mode'] = 'auto'
        
        ring_analysis = self.config_manager.advanced_params.get("ring_analysis", {})
        if not isinstance(ring_analysis, dict):
            ring_analysis = {}

        config = {
            'detector_geometry': {
                'dist': required['detector_distance'],
                'wavelength': required['wavelength'],
                'pixel_size': pixel_size,
                'rot1': self.config_manager.get_param('detector_tilt', 0.0),
                'rot2': self.config_manager.get_param('tilt_plane_rotation', 0.0),
                'rot3': 0.0,
            },
            # Skills-based autocalibration uses ring-analysis settings under `ring_analysis`.
            'ring_analysis': ring_analysis,
            'r_beam_px': self.config_manager.get_param('r_beam_px', 35),
            'calibrant_name': self.config_manager.get_param('calibrant_name', 'AgBh'),
            'mask_config': mask_config,
        }
        
        return config
    
    def set_calibration_result(self, integrator_dir: str, calibrated_params: Dict[str, Any]):
        """
        Set the calibration result.
        
        Args:
            integrator_dir: Directory containing the calibrated integrator (autosaxs skill output)
            calibrated_params: Calibrated parameters
        """
        self.integrator_dir = integrator_dir
        self.calibrated_params = calibrated_params
        
        # Update config with calibrated params
        config = self.config_manager.get_all_config()
        config['calibrated_params'] = calibrated_params
        self.config_manager.save(config)
    
    def calibrate(self, calibrant_path: str, mask_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Run calibration and return results.
        
        Note: This is a placeholder. Actual calibration is done by CalibrationService
        in a subprocess. This method is for interface compatibility.
        
        Args:
            calibrant_path: Path to calibrant image
            mask_path: Optional path to mask file
            
        Returns:
            Dictionary with calibration results
        """
        # This is typically called by CalibrationService, not directly
        raise NotImplementedError("Calibration should be performed via CalibrationService")

