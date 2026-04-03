"""Data file management for the application."""
from typing import Optional, List
from enum import Enum
import os
import shutil
from ..utils.filename_utils import generate_filename


class FileType(Enum):
    """Types of files managed by the application."""
    CALIBRANT = "calibrant"
    BUFFER = "buffer"
    SAMPLE = "sample"
    MASK = "mask"


class DataManager:
    """Manages file paths and data loading. Sample holds a list of paths; others are single path."""
    
    def __init__(self):
        """Initialize the data manager."""
        self._files: dict[FileType, Optional[str]] = {
            FileType.CALIBRANT: None,
            FileType.BUFFER: None,
            FileType.MASK: None,
        }
        self._sample_paths: List[str] = []
    
    def set_file(self, file_type: FileType, path: str):
        """
        Set file path for a given type. For SAMPLE, use set_sample_paths instead.
        
        Args:
            file_type: Type of file (must not be SAMPLE)
            path: Path to the file
        """
        if file_type == FileType.SAMPLE:
            self._sample_paths = [path] if path else []
        else:
            self._files[file_type] = path
    
    def get_file(self, file_type: FileType) -> Optional[str]:
        """
        Get file path for a given type. For SAMPLE, returns first path or None.
        
        Args:
            file_type: Type of file
            
        Returns:
            Path to the file, or None if not set
        """
        if file_type == FileType.SAMPLE:
            return self._sample_paths[0] if self._sample_paths else None
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
    def sample_paths(self) -> List[str]:
        """Get list of sample file paths (zero or more)."""
        return list(self._sample_paths)
    
    @sample_paths.setter
    def sample_paths(self, paths: List[str]):
        """Set sample file paths (replaces previous list)."""
        self._sample_paths = list(paths) if paths else []
    
    @property
    def mask_path(self) -> Optional[str]:
        """Get mask file path."""
        return self.get_file(FileType.MASK)
    
    @mask_path.setter
    def mask_path(self, path: str):
        """Set mask file path."""
        self.set_file(FileType.MASK, path)
    
    def copy_image_to_temp(self, source_path: str, image_type: str, base_dir: str) -> Optional[str]:
        """
        Copy image file to working directory with descriptive naming.
        
        Args:
            source_path: Path to source image file
            image_type: Type of image (e.g., "buffer", "sample", "calibrant")
            base_dir: Working directory path (all outputs go here)
            
        Returns:
            Path to copied file, or None if error occurred
        """
        if not source_path or not os.path.exists(source_path):
            return None
        
        try:
            # Generate descriptive filename based on original name and type
            dest_name = generate_filename(
                source_path,
                image_type,
                os.path.splitext(str(source_path))[1]
            )
            dest_path = os.path.join(base_dir, dest_name)
            shutil.copy2(str(source_path), dest_path)
            return dest_path
        except Exception as e:
            print(f"Error copying {source_path} to working directory: {e}")
            return None
    
    @staticmethod
    def save_temp_files(source_dir: str, dest_dir: str) -> Optional[int]:
        """
        Copy all files from source directory to destination directory.
        
        Args:
            source_dir: Source directory path
            dest_dir: Destination directory path
            
        Returns:
            Number of items copied, or None if error occurred
        """
        if not os.path.exists(source_dir):
            return None
        
        try:
            files_copied = 0
            for item in os.listdir(source_dir):
                source_path = os.path.join(source_dir, item)
                dest_path = os.path.join(dest_dir, item)
                
                if os.path.isfile(source_path):
                    shutil.copy2(source_path, dest_path)
                    files_copied += 1
                elif os.path.isdir(source_path):
                    shutil.copytree(source_path, dest_path, dirs_exist_ok=True)
                    files_copied += 1
            
            return files_copied
        except Exception as e:
            print(f"Error copying files: {e}")
            return None
    
    @staticmethod
    def validate_image_file(file_path: str) -> bool:
        """
        Validate image file: check that it has .tif extension.
        
        Args:
            file_path: Path to image file
            
        Returns:
            True if valid, False otherwise
        """
        # Check file extension
        _, ext = os.path.splitext(file_path)
        ext = ext.lower()
        
        # Accept both .tif and .tiff extensions
        if ext not in ['.tif', '.tiff']:
            return False
        
        # Check if file exists and is readable
        if not os.path.exists(file_path):
            return False
        
        return True
    
    @staticmethod
    def validate_mask_file(file_path: str) -> bool:
        """
        Validate mask file: check extension and that it contains only 0/1 or True/False values.
        
        Args:
            file_path: Path to mask file
            
        Returns:
            True if valid, False otherwise
        """
        import numpy as np
        import fabio
        
        # Check file extension
        _, ext = os.path.splitext(file_path)
        ext = ext.lower()
        if ext not in ['.npy', '.txt', '.msk']:
            return False
        
        # Try to read the mask file (read raw data before boolean conversion)
        try:
            if ext == '.npy':
                mask_raw = np.load(file_path)
            elif ext == '.txt':
                mask_raw = np.loadtxt(file_path)
            elif ext == '.msk':
                mask_raw = fabio.open(file_path).data
            
            # Check that mask has only two unique values
            unique_values = np.unique(mask_raw)
            
            # Check that all values are either 0/1 or True/False
            # Convert to set of unique boolean-like values
            valid_values = set()
            for val in unique_values:
                # Convert numpy scalar to Python type for comparison
                val_py = val.item() if hasattr(val, 'item') else val
                
                # Check if value is 0/1 or True/False
                if val_py == 0 or val_py is False or (isinstance(val_py, float) and abs(val_py) < 1e-10):
                    valid_values.add(0)
                elif val_py == 1 or val_py is True or (isinstance(val_py, float) and abs(val_py - 1.0) < 1e-10):
                    valid_values.add(1)
                else:
                    # Value is neither 0/1 nor True/False
                    return False
            
            # Should have exactly 2 unique values (0 and 1)
            if len(valid_values) != 2:
                return False
            
            return True
        except Exception as e:
            print(f"Error validating mask file: {e}")
            return False

