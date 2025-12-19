"""Calibration management for the application."""
import os
import hashlib
import yaml
from typing import Optional, Dict, Any
from ..core.constants import TEMP_DIR
from ..core.interfaces import ICalibrationManager
from .config_manager import ConfigManager
from processor import IntegratorExtended


class CalibrationManager(ICalibrationManager):
    """Manages calibration state and operations."""
    
    def __init__(self, config_manager: ConfigManager, temp_dir: str = TEMP_DIR):
        """
        Initialize the calibration manager.
        
        Args:
            config_manager: Configuration manager instance
            temp_dir: Temporary directory for calibration files
        """
        self.config_manager = config_manager
        self.temp_dir = temp_dir
        self.integrator: Optional[IntegratorExtended] = None
        self.calibrated_params: Dict[str, Any] = {}
        self.last_calibration_hash: Optional[str] = None
        self.calibration_cache_path = os.path.join(temp_dir, "calibration_cache.yml")
        
        # Try to load integrator from disk if it exists
        self._try_load_integrator()
    
    @property
    def is_calibrated(self) -> bool:
        """Check if calibration is available."""
        return self.integrator is not None and bool(self.calibrated_params)
    
    def get_integrator(self) -> Optional[IntegratorExtended]:
        """Get the current integrator if calibrated."""
        return self.integrator
    
    def get_calibrated_params(self) -> Dict[str, Any]:
        """Get calibrated parameters."""
        return self.calibrated_params.copy()
    
    def _try_load_integrator(self):
        """Try to load integrator from disk if it exists."""
        integrator_subd = os.path.join(self.temp_dir, 'integrator_params')
        if os.path.exists(integrator_subd):
            try:
                self.integrator = IntegratorExtended.from_disk(integrator_subd)
                # Try to load calibrated params from config
                config = self.config_manager.load()
                if 'calibrated_params' in config:
                    self.calibrated_params = config.get('calibrated_params', {})
            except Exception as e:
                print(f"Could not load integrator from disk: {e}")
    
    def build_calibration_config(self, mask_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Build the config dictionary required by autocalib.
        
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
        
        config = {
            'detector_geometry': {
                'dist': required['detector_distance'],
                'wavelength': required['wavelength'],
                'pixel_size': pixel_size,
                'rot1': self.config_manager.get_param('detector_tilt', 0.0),
                'rot2': self.config_manager.get_param('tilt_plane_rotation', 0.0),
                'rot3': 0.0,
            },
            'center_refinement': self.config_manager.advanced_params['center_refinement'],
            'ring_search': self.config_manager.advanced_params['ring_search'],
            'r_beam_px': self.config_manager.get_param('r_beam_px', 35),
            'calibrant_name': self.config_manager.get_param('calibrant_name', 'AgBh'),
            'mask_config': mask_config,
        }
        
        return config
    
    def compute_calibration_hash(self, calib_path: str, config: Dict[str, Any]) -> Optional[str]:
        """
        Compute hash of calibration inputs for caching.
        
        Args:
            calib_path: Path to calibrant image
            config: Configuration dictionary
            
        Returns:
            Hash string or None if computation fails
        """
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
    
    def load_cached_calibration(self, hash_value: str) -> bool:
        """
        Load cached calibration if hash matches.
        
        Args:
            hash_value: Hash of calibration inputs
            
        Returns:
            True if cached calibration was loaded, False otherwise
        """
        if not os.path.exists(self.calibration_cache_path):
            return False
        
        try:
            with open(self.calibration_cache_path, 'r') as f:
                cache = yaml.safe_load(f)
            if cache and cache.get('hash') == hash_value and cache.get('calibrated_params'):
                # Try to load integrator from disk
                integrator_subd = os.path.join(self.temp_dir, 'integrator_params')
                if os.path.exists(integrator_subd):
                    try:
                        self.calibrated_params = cache['calibrated_params']
                        self.integrator = IntegratorExtended.from_disk(integrator_subd)
                        self.last_calibration_hash = hash_value
                        return True
                    except Exception as e:
                        print(f"Error loading integrator from disk: {e}")
                        return False
        except Exception as e:
            print(f"Error loading cached calibration: {e}")
        return False
    
    def save_calibration_cache(self, hash_value: str, calibrated_params: Dict[str, Any]):
        """
        Save calibration cache to disk.
        
        Args:
            hash_value: Hash of calibration inputs
            calibrated_params: Calibrated parameters
        """
        try:
            cache = {'hash': hash_value, 'calibrated_params': calibrated_params}
            os.makedirs(os.path.dirname(self.calibration_cache_path), exist_ok=True)
            with open(self.calibration_cache_path, 'w') as f:
                yaml.dump(cache, f, default_flow_style=False)
        except Exception as e:
            print(f"Error saving calibration cache: {e}")
    
    def set_calibration_result(self, integrator: IntegratorExtended, 
                               calibrated_params: Dict[str, Any], 
                               calibration_hash: Optional[str] = None):
        """
        Set the calibration result.
        
        Args:
            integrator: Calibrated integrator
            calibrated_params: Calibrated parameters
            calibration_hash: Optional hash of calibration inputs
        """
        self.integrator = integrator
        self.calibrated_params = calibrated_params
        if calibration_hash:
            self.last_calibration_hash = calibration_hash
            self.save_calibration_cache(calibration_hash, calibrated_params)
        
        # Save integrator to disk
        integrator_subd = os.path.join(self.temp_dir, 'integrator_params')
        integrator.to_disk(integrator_subd)
        
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

