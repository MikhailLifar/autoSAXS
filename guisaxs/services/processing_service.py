"""Service for handling image processing operations."""
from typing import Optional, Callable
from ..models.processing_manager import ProcessingManager
from ..models.data_manager import DataManager, FileType
from ..core.event_bus import EventBus, EventType
import os


class ProcessingService:
    """Service for handling image processing operations."""
    
    def __init__(self, processing_manager: ProcessingManager,
                 data_manager: DataManager,
                 event_bus: Optional[EventBus] = None):
        """
        Initialize the processing service.
        
        Args:
            processing_manager: Processing manager instance
            data_manager: Data manager instance
            event_bus: Optional event bus for publishing events
        """
        self.processing_manager = processing_manager
        self.data_manager = data_manager
        self.event_bus = event_bus
    
    def process_image(self, image_path: str, image_type: str,
                     status_callback: Optional[Callable[[str, str], None]] = None) -> Optional[str]:
        """
        Process an image and return output path.
        
        Args:
            image_path: Path to input image
            image_type: Type of image ("buffer" or "sample")
            status_callback: Optional callback(status_message, status_type) for status updates
            
        Returns:
            Path to processed output file, or None if processing failed
        """
        if not image_path:
            error_msg = f"No {image_type} image loaded"
            if status_callback:
                status_callback(error_msg, "error")
            return None
        
        if not self.processing_manager.calibration_manager.is_calibrated:
            error_msg = "Please calibrate first"
            if status_callback:
                status_callback(error_msg, "error")
            return None
        
        status_msg = f"Processing {image_type}: {os.path.basename(str(image_path))}"
        if status_callback:
            status_callback(status_msg, "progress")
        
        if self.event_bus:
            self.event_bus.publish(EventType.PROCESSING_STARTED, {
                "image_path": image_path,
                "image_type": image_type
            })
        
        try:
            output_path = self.processing_manager.process_image(image_path, image_type)
            
            success_msg = f"{image_type.capitalize()} processed: {os.path.basename(str(image_path))}"
            if status_callback:
                status_callback(success_msg, "success")
            
            if self.event_bus:
                self.event_bus.publish(EventType.PROCESSING_COMPLETE, {
                    "image_path": image_path,
                    "image_type": image_type,
                    "output_path": output_path
                })
            
            return output_path
        except Exception as e:
            error_msg = f"Error processing {image_type}: {str(e)}"
            if status_callback:
                status_callback(error_msg, "error")
            
            if self.event_bus:
                self.event_bus.publish(EventType.PROCESSING_ERROR, {
                    "image_path": image_path,
                    "image_type": image_type,
                    "error": str(e)
                })
            
            import traceback
            traceback.print_exc()
            return None
    
    def create_subtracted_curve(self, buffer_path: str, sample_path: str,
                                status_callback: Optional[Callable[[str, str], None]] = None) -> Optional[str]:
        """
        Create subtracted curve from buffer and sample.
        
        Args:
            buffer_path: Path to buffer 1D curve file
            sample_path: Path to sample 1D curve file
            status_callback: Optional callback for status updates
            
        Returns:
            Path to subtracted curve file, or None if failed
        """
        try:
            output_path = self.processing_manager.create_subtracted_curve(buffer_path, sample_path)
            return output_path
        except Exception as e:
            error_msg = f"Error creating subtracted curve: {str(e)}"
            if status_callback:
                status_callback(error_msg, "error")
            import traceback
            traceback.print_exc()
            return None

