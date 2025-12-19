"""Data file management for the application."""
from typing import Optional
from enum import Enum


class FileType(Enum):
    """Types of files managed by the application."""
    CALIBRANT = "calibrant"
    BUFFER = "buffer"
    SAMPLE = "sample"
    MASK = "mask"


class DataManager:
    """Manages file paths and data loading."""
    
    def __init__(self):
        """Initialize the data manager."""
        self._files: dict[FileType, Optional[str]] = {
            FileType.CALIBRANT: None,
            FileType.BUFFER: None,
            FileType.SAMPLE: None,
            FileType.MASK: None,
        }
    
    def set_file(self, file_type: FileType, path: str):
        """
        Set file path for a given type.
        
        Args:
            file_type: Type of file
            path: Path to the file
        """
        self._files[file_type] = path
    
    def get_file(self, file_type: FileType) -> Optional[str]:
        """
        Get file path for a given type.
        
        Args:
            file_type: Type of file
            
        Returns:
            Path to the file, or None if not set
        """
        return self._files.get(file_type)
    
    @property
    def calibrant_path(self) -> Optional[str]:
        """Get calibrant file path."""
        return self.get_file(FileType.CALIBRANT)
    
    @calibrant_path.setter
    def calibrant_path(self, path: str):
        """Set calibrant file path."""
        self.set_file(FileType.CALIBRANT, path)
    
    @property
    def buffer_path(self) -> Optional[str]:
        """Get buffer file path."""
        return self.get_file(FileType.BUFFER)
    
    @buffer_path.setter
    def buffer_path(self, path: str):
        """Set buffer file path."""
        self.set_file(FileType.BUFFER, path)
    
    @property
    def sample_path(self) -> Optional[str]:
        """Get sample file path."""
        return self.get_file(FileType.SAMPLE)
    
    @sample_path.setter
    def sample_path(self, path: str):
        """Set sample file path."""
        self.set_file(FileType.SAMPLE, path)
    
    @property
    def mask_path(self) -> Optional[str]:
        """Get mask file path."""
        return self.get_file(FileType.MASK)
    
    @mask_path.setter
    def mask_path(self, path: str):
        """Set mask file path."""
        self.set_file(FileType.MASK, path)

