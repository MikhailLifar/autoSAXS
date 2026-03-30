"""Processing management for the application."""
import os
from typing import Optional
from ..models.calibration_manager import CalibrationManager
from autosaxs.skill import integrate, subtract


class ProcessingManager:
    """Manages image processing operations."""
    
    def __init__(self, calibration_manager: CalibrationManager, working_dir: str):
        """
        Initialize the processing manager.
        
        Args:
            calibration_manager: Calibration manager instance
            working_dir: Working directory for processing output
        """
        self.calibration_manager = calibration_manager
        self.temp_dir = working_dir
    
    def process_image(self, image_path: str, image_type: str) -> str:
        """
        Process image and return output path.
        
        Args:
            image_path: Path to input image
            image_type: Type of image (e.g., "buffer", "sample")
            
        Returns:
            Path to processed output file
            
        Raises:
            ValueError: If calibration is not available
            FileNotFoundError: If image file doesn't exist
        """
        if not self.calibration_manager.is_calibrated:
            raise ValueError("Calibration is not available. Please calibrate first.")
        
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"Image file not found: {image_path}")
        
        integrator_dir = self.calibration_manager.get_integrator_dir()
        if not integrator_dir:
            raise ValueError("Integrator directory is not available")

        # Use autosaxs skill (loads integrator + reads TIFF internally)
        out = integrate(
            images=[image_path],
            integrator_dir=integrator_dir,
            output_dir=self.temp_dir,
            npt=1000,
            use_cache=True,
        )
        paths = out.get("integrated_1d") if isinstance(out, dict) else None
        # Skills batching wrapper returns single-sample outputs as scalar strings (not list-of-one).
        if isinstance(paths, str) and paths:
            return paths
        if isinstance(paths, list) and paths and isinstance(paths[0], str):
            return str(paths[0])
        raise RuntimeError("Integration failed: no output curve produced")
    
    def create_subtracted_curve(self, buffer_path: str, sample_path: str, 
                                 output_path: Optional[str] = None) -> str:
        """
        Create subtracted curve from buffer and sample.
        
        Args:
            buffer_path: Path to buffer 1D curve file
            sample_path: Path to sample 1D curve file
            output_path: Optional output path. If None, auto-generated from input names.
            
        Returns:
            Path to subtracted curve file
        """
        # Keep optional output_path argument for API compatibility; skill decides filename under output_dir.
        output_dir = self.temp_dir if output_path is None else os.path.dirname(os.path.abspath(output_path))

        q_min = None
        q_max = None
        cfg_sub = getattr(self.calibration_manager.config_manager, "advanced_params", {}).get("sub", {})
        if isinstance(cfg_sub, dict):
            q_range = cfg_sub.get("q_range_abs")
            if isinstance(q_range, (list, tuple)) and len(q_range) == 2:
                try:
                    q_min = float(q_range[0]) if q_range[0] is not None else None
                    q_max = float(q_range[1]) if q_range[1] is not None else None
                except (TypeError, ValueError):
                    q_min = None
                    q_max = None

        out = subtract(
            sample_1d=sample_path,
            buffer_1d=buffer_path,
            output_dir=output_dir,
            method="match_tail",
            q_min=q_min,
            q_max=q_max,
            use_cache=True,
        )
        sub_path = out.get("subtracted_1d") if isinstance(out, dict) else None
        if not isinstance(sub_path, str) or not sub_path:
            raise RuntimeError("Subtraction failed: no output curve produced")
        return sub_path

