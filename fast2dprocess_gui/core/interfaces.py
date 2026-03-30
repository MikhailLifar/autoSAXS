"""Interfaces for dependency injection and abstraction."""
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any


class IConfigManager(ABC):
    """Interface for configuration management."""
    
    @abstractmethod
    def load(self) -> Dict[str, Any]:
        """Load configuration from persistent storage."""
        pass
    
    @abstractmethod
    def save(self, config: Optional[Dict[str, Any]] = None):
        """Save configuration to persistent storage."""
        pass
    
    @abstractmethod
    def get_param(self, key: str, default: Any = None) -> Any:
        """Get a configuration parameter."""
        pass
    
    @abstractmethod
    def set_param(self, key: str, value: Any):
        """Set a configuration parameter."""
        pass


class ICalibrationManager(ABC):
    """Interface for calibration management."""
    
    @property
    @abstractmethod
    def is_calibrated(self) -> bool:
        """Check if calibration is available."""
        pass
    
    @abstractmethod
    def calibrate(self, calibrant_path: str, mask_path: Optional[str] = None) -> Dict[str, Any]:
        """Run calibration and return results."""
        pass
    
    @abstractmethod
    def get_integrator_dir(self) -> Optional[str]:
        """Get the path to the calibrated integrator directory, if available."""
        pass
    
    @abstractmethod
    def get_calibrated_params(self) -> Dict[str, Any]:
        """Get calibrated parameters."""
        pass


class IStatusReporter(ABC):
    """Interface for status reporting."""
    
    @abstractmethod
    def update_status(self, message: str, status_type: str = "default"):
        """Update status message."""
        pass

