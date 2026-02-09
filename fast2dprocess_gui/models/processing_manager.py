"""Processing management for the application."""
import os
from typing import Optional
from ..models.calibration_manager import CalibrationManager
from ..utils.filename_utils import generate_filename
from autosaxs.processor import integrate_2d_to_1d
from autosaxs.utils import read_from_tiff


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
        
        integrator = self.calibration_manager.get_integrator()
        if integrator is None:
            raise ValueError("Integrator is not available")
        
        # Read image data
        data = read_from_tiff(image_path)
        
        # Create output filename with descriptive naming
        output_path = generate_filename(
            image_path,
            "int",
            ".dat",
            base_dir=self.temp_dir
        )
        
        # Perform integration
        metadata = {'type': image_type, 'source_path': image_path}
        integrate_2d_to_1d(integrator, data, npt=1000, destpath=output_path, metadata=metadata)
        
        return output_path
    
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
        from autosaxs.processor import subtract_buffer
        
        if output_path is None:
            # Generate descriptive filename from both buffer and sample names
            buffer_basename = os.path.splitext(os.path.basename(buffer_path))[0]
            sample_basename = os.path.splitext(os.path.basename(sample_path))[0]
            # Use generate_filename with additional_info for the sample name
            output_path = generate_filename(
                buffer_path,
                "subtracted",
                ".dat",
                additional_info=sample_basename,
                base_dir=self.temp_dir
            )
        
        subtract_buffer(buffer_path, sample_path, output_path, method='match_tail')
        return output_path

