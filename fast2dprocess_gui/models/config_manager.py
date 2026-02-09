"""Configuration management for the application."""
import os
import yaml
from typing import Dict, Any, Optional
from ..core.interfaces import IConfigManager


class ConfigManager(IConfigManager):
    """Manages configuration loading and saving."""
    
    def __init__(self, config_path: str):
        """
        Initialize the configuration manager.
        
        Args:
            config_path: Path to the configuration file (e.g. in working directory)
        """
        self.config_path = config_path
        self.basic_params = {
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
        self.load()
    
    def load(self) -> Dict[str, Any]:
        """Load configuration from YAML file if it exists."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    loaded_config = yaml.safe_load(f) or {}
                
                # Update basic parameters (only if value is not None)
                if 'config_dictionary' in loaded_config:
                    for key, value in loaded_config['config_dictionary'].items():
                        if key in self.basic_params and value is not None:
                            self.basic_params[key] = value
                
                # Update advanced parameters
                if 'advanced_params' in loaded_config:
                    for key, value in loaded_config['advanced_params'].items():
                        if key in self.advanced_params:
                            if isinstance(value, dict):
                                self.advanced_params[key].update(value)
                            else:
                                self.advanced_params[key] = value
            except Exception as e:
                print(f"Error loading config: {e}")
        
        return self.get_all_config()
    
    def save(self, config: Optional[Dict[str, Any]] = None):
        """
        Save current configuration to YAML file.
        
        Args:
            config: Optional config dict to save. If None, saves current state.
        """
        try:
            if config is None:
                config = self.get_all_config()
            
            config_to_save = {
                'config_dictionary': config.get('config_dictionary', self.basic_params.copy()),
                'advanced_params': config.get('advanced_params', self.advanced_params.copy()),
            }
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            
            with open(self.config_path, 'w') as f:
                yaml.dump(config_to_save, f, default_flow_style=False)
        except Exception as e:
            print(f"Error saving config: {e}")
    
    def get_param(self, key: str, default: Any = None) -> Any:
        """Get a configuration parameter."""
        return self.basic_params.get(key, default)
    
    def set_param(self, key: str, value: Any):
        """Set a configuration parameter."""
        self.basic_params[key] = value
    
    def get_advanced_param(self, category: str, key: str, default: Any = None) -> Any:
        """Get an advanced parameter."""
        if category in self.advanced_params:
            return self.advanced_params[category].get(key, default)
        return default
    
    def set_advanced_param(self, category: str, key: str, value: Any):
        """Set an advanced parameter."""
        if category not in self.advanced_params:
            self.advanced_params[category] = {}
        self.advanced_params[category][key] = value
    
    def get_all_config(self) -> Dict[str, Any]:
        """Get all configuration as a dictionary."""
        return {
            'config_dictionary': self.basic_params.copy(),
            'advanced_params': self.advanced_params.copy(),
        }

